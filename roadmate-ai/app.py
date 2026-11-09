"""로드메이트 코리아 AI 서버 (FastAPI). 실행: uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1"""
import asyncio
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config import Settings, artifact_hashes
from engine import Engine
from inference import LlamaClient, MockClient, ModelError, ModelTimeout
from schemas import (CancelRequest, CancelResponse, DestinationRequest, DestinationResponse, ErrorBody,
                     TranslateRequest, TranslateResponse)
from sessions import ModelSlot, TurnRegistry, payload_hash
from validation import HANGUL

log = logging.getLogger("roadmate.ai")
API_VERSION = "1.3.1"  # 백엔드 연동: 계약이 바뀌면 올리고 docs/openapi.json 재생성


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", request_id: str | None = None, headers=None):
        self.status, self.code, self.message, self.request_id, self.headers = status, code, message, request_id, headers


def _error(status, code, message="", request_id=None, headers=None):
    body = ErrorBody(error_code=code, request_id=request_id, message=message).model_dump()
    return UTF8JSONResponse(body, status_code=status, headers=headers)


ERROR_RESPONSES = {c: {"model": ErrorBody} for c in (401, 409, 410, 422, 429, 502, 503, 504)}
ERROR_RESPONSES[422]["description"] = "Invalid input"  # Python 버전별 HTTPStatus 설명 차이를 계약에서 제거


def create_app(settings: Settings | None = None, client=None) -> FastAPI:
    s = settings or Settings.from_env()
    st: dict = {}

    @asynccontextmanager
    async def lifespan(_app):
        c = client or (MockClient() if s.model_backend == "mock" else LlamaClient(s.model_base_url, s.model_id, s.manifest))
        st.update(client=c, engine=Engine(c, s), registry=TurnRegistry(s.dedupe_ttl_s), slot=ModelSlot(),
                  self_check=None, settle=None, artifact_hashes=artifact_hashes(), instance_id=uuid.uuid4().hex)
        log.info("ai server up: backend=%s bundle=%s", s.model_backend, s.engine_bundle)
        yield
        for t in [f.task for f in st["registry"].inflight.values()] + [st["settle"]]:
            if t:
                t.cancel()
        await c.aclose()

    app = FastAPI(title="RoadMate Korea AI", version=API_VERSION, lifespan=lifespan,
                  default_response_class=UTF8JSONResponse)

    @app.exception_handler(ApiError)
    async def _api_error(_req, e: ApiError):
        return _error(e.status, e.code, e.message, e.request_id, e.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_req, e: RequestValidationError):
        body = e.body if isinstance(e.body, dict) else {}
        msg = "; ".join(f"{'.'.join(map(str, err['loc'][1:]))}: {err['msg']}" for err in e.errors())[:500]
        return _error(422, "invalid_input", msg, body.get("request_id") if isinstance(body.get("request_id"), str) else None)

    # 백엔드 연동: 백엔드만 호출하도록 내부 토큰 검사(AI_INTERNAL_TOKEN 설정 시)
    async def auth(x_internal_token: str | None = Header(default=None)):
        if s.internal_token and not hmac.compare_digest(x_internal_token or "", s.internal_token):
            raise ApiError(401, "unauthorized")

    async def _settle_slot():
        # QWEN 구현: 취소·timeout 뒤 llama-server 계산이 실제로 끝났을 때만 슬롯을 연다(그 전까지 429 busy)
        async def settle():
            try:
                while not await st["client"].wait_idle(s.cancel_settle_s):
                    log.warning("llama-server still processing after cancel/timeout; slot stays busy")
                    await asyncio.sleep(0.5)  # wait_idle이 즉시 False여도 이벤트 루프를 막지 않게
            finally:
                st["slot"].release()

        st["settle"] = asyncio.create_task(settle())
        await asyncio.wait({st["settle"]}, timeout=s.cancel_settle_s)

    async def _guarded(kind: str, req, timeout_s: float, wait_s: float):
        # 파라미터 튜닝: 대기열 없이 busy_wait_s만 기다림. 대기 중 취소되면 모델을 호출하지 않는다
        if not await st["slot"].try_acquire(wait_s):
            raise ApiError(429, "busy", "model is serving another turn", req.request_id, {"Retry-After": "1"})
        engine = st["engine"]
        fn = engine.translate if kind == "translate" else engine.parse_destination
        t0 = time.monotonic()
        try:
            out = await fn(req, t0 + timeout_s)
        except (asyncio.CancelledError, ModelTimeout):
            await _settle_slot()
            raise
        except BaseException:
            st["slot"].release()
            raise
        st["slot"].release()
        return out, int((time.monotonic() - t0) * 1000)

    async def _wait(request: Request, entry):
        while True:
            done, _ = await asyncio.wait({entry.task}, timeout=0.25)
            if done:
                break
            if await request.is_disconnected():
                if entry.waiters <= 1:
                    entry.task.cancel()
                raise ApiError(499, "client_disconnected")
        if entry.task.cancelled():
            raise ApiError(409, "epoch_cancelled")
        exc = entry.task.exception()
        if isinstance(exc, ApiError):
            raise exc
        if isinstance(exc, ModelError):
            raise ApiError(exc.status, exc.code, str(exc)[:500])
        if exc is not None:
            log.exception("turn failed", exc_info=exc)
            raise ApiError(500, "internal_error")
        return entry.task.result()

    async def run_turn(request: Request, req, kind: str, build):
        rid = req.request_id
        if req.engine_bundle != s.engine_bundle:
            raise ApiError(422, "bundle_mismatch", f"server bundle is {s.engine_bundle}", rid)
        if len(req.text) > s.max_text_chars:
            raise ApiError(422, "text_too_long", f"max {s.max_text_chars} chars", rid)
        reg = st["registry"]
        if reg.is_cancelled(req.session_id, req.session_epoch):
            raise ApiError(409, "epoch_cancelled", request_id=rid)
        phash = payload_hash({"kind": kind, **req.model_dump()})
        state, obj = reg.lookup(rid, phash)
        if state == "conflict":
            raise ApiError(409, "request_id_conflict", request_id=rid)
        if state == "done":
            return obj
        if state == "new":
            # 검증: 슬롯 대기 전에 등록(조회~등록 사이 await 없음) → 대기 중 같은 ID는 합류·충돌 409, 취소는 대기 작업을 취소
            task = asyncio.create_task(_guarded(kind, req, s.model_timeout_s, s.busy_wait_s))
            entry = reg.start(rid, req.session_id, req.session_epoch, phash, task)

            def _done(t: asyncio.Task):
                ok = not t.cancelled() and t.exception() is None
                reg.finish(rid, build(req, *t.result()) if ok else None)

            task.add_done_callback(_done)
        else:
            entry = obj
        entry.waiters += 1
        try:
            try:
                out, latency = await _wait(request, entry)
            except ApiError as e:
                e.request_id = rid
                raise
        finally:
            entry.waiters -= 1
        if reg.is_cancelled(req.session_id, req.session_epoch):
            raise ApiError(409, "epoch_cancelled", request_id=rid)
        result = build(req, out, latency)
        log.info("turn %s kind=%s status=%s latency_ms=%d", rid, kind, result["status"], latency)
        return result

    def build_translate(req: TranslateRequest, out: dict, latency: int) -> dict:
        ok = out["status"] == "ok"
        return TranslateResponse(
            request_id=req.request_id, session_id=req.session_id, session_epoch=req.session_epoch,
            turn_id=req.turn_id, status=out["status"],
            translated_text=out["translated_text"].strip() if ok else None,
            clarification_question=None if ok else out["clarification_question"].strip(),
            engine_bundle=s.engine_bundle, model_id=st["client"].model_id,
            prompt_version=st["engine"].translate_version, glossary_version=st["engine"].glossary_version,
            latency_ms=latency,
        ).model_dump()

    def build_destination(req: DestinationRequest, out: dict, latency: int) -> dict:
        return DestinationResponse(
            request_id=req.request_id, session_id=req.session_id, session_epoch=req.session_epoch,
            turn_id=req.turn_id, status=out["status"], intent=out["intent"],
            query_original=out.get("query_original"), query_ko_candidate=out.get("query_ko_candidate"),
            region_hint=out.get("region_hint"), needs_confirmation=out["needs_confirmation"],
            candidates=out["candidates"], clarification_question=out.get("clarification_question"),
            engine_bundle=s.engine_bundle, model_id=st["client"].model_id,
            prompt_version=st["engine"].destination_version, poi_version=st["engine"].poi.version,
            latency_ms=latency,
        ).model_dump()

    # 백엔드 연동: 백엔드 → POST /internal/v1/translate (최종 STT 문장 1개)
    @app.post("/internal/v1/translate", response_model=TranslateResponse, responses=ERROR_RESPONSES,
              dependencies=[Depends(auth)])
    async def translate(req: TranslateRequest, request: Request):
        return await run_turn(request, req, "translate", build_translate)

    # 백엔드 연동: 백엔드 → POST /internal/v1/parse-destination
    @app.post("/internal/v1/parse-destination", response_model=DestinationResponse, responses=ERROR_RESPONSES,
              dependencies=[Depends(auth)])
    async def parse_destination(req: DestinationRequest, request: Request):
        return await run_turn(request, req, "destination", build_destination)

    # 백엔드 연동: APK P 해제·이동 시 백엔드가 전달하는 취소
    @app.post("/internal/v1/cancel", response_model=CancelResponse, responses=ERROR_RESPONSES,
              dependencies=[Depends(auth)])
    async def cancel(req: CancelRequest):
        through, n = st["registry"].cancel(req.session_id, req.session_epoch)
        log.info("cancel session=%s through_epoch=%d inflight=%d", req.session_id, through, n)
        return CancelResponse(session_id=req.session_id, cancelled_through_epoch=through, cancelled_inflight=n)

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    async def _self_check() -> str:
        # 검증: 준비 확인용 짧은 번역 1회(성공 결과만 캐시)
        if st["self_check"] == "ok":
            return "ok"
        req = TranslateRequest(request_id="selfcheck", session_id="selfcheck", session_epoch=0, turn_id=0,
                               source_locale="en-US", target_locale="ko-KR", scenario="general",
                               text="Hello, thank you.", engine_bundle=s.engine_bundle)
        try:
            out, _ = await _guarded("translate", req, max(s.model_timeout_s, 20), 0)
            ok = out["status"] == "ok" and (st["client"].is_mock or bool(HANGUL.search(out["translated_text"])))
            st["self_check"] = "ok" if ok else "bad_output"
        except ApiError:
            return "skipped_busy"
        except ModelError as e:
            st["self_check"] = e.code
        return st["self_check"]

    # 백엔드 연동: 백엔드·개발 도구의 준비 확인
    @app.get("/health/ready")
    async def ready():
        c = st["client"]
        checks = {"model_server": await c.health(), "model_alias": False, "adapter": True,
                  "runtime_identity": c.is_mock}
        identity = None
        if checks["model_server"]:
            try:
                checks["model_alias"] = c.is_mock or s.model_id in await c.model_ids()
                if s.manifest.get("adapter"):
                    checks["adapter"] = bool(await c.lora_adapters())
            except Exception:  # noqa: BLE001 - 준비 확인은 실패로만 보고
                checks["model_alias"] = False
            if checks["model_alias"]:
                try:
                    identity = await c.runtime_identity()
                    checks["runtime_identity"] = c.is_mock or bool(identity)
                except ModelError as e:
                    log.warning("model runtime not verified: %s", e)
        checks["self_check"] = await _self_check() if all(checks.values()) else "not_run"
        is_ready = all(v is True for k, v in checks.items() if k != "self_check") and checks["self_check"] in (
            "ok", "skipped_busy")
        body = {"ready": is_ready, "checks": checks, "engine_bundle": s.engine_bundle, "model_id": c.model_id,
                "model_profile": s.model_profile,
                "instance_id": st["instance_id"],
                "model_runtime": identity,
                "backend": s.model_backend, "versions": s.manifest["versions"], "polish_ko": s.polish_ko,
                "runtime_config": {"model_id": c.model_id, "model_profile": s.model_profile, "polish_ko": s.polish_ko,
                                   "model_timeout_s": s.model_timeout_s, "max_text_chars": s.max_text_chars},
                "artifact_hashes": st["artifact_hashes"]}
        return UTF8JSONResponse(body, status_code=200 if is_ready else 503)

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # llama-server 호출마다 찍히는 요청 줄은 숨긴다
app = create_app()
