"""요청 중복 제거, epoch 취소, 모델 단일 실행 슬롯."""
import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field

MAX_ENTRIES = 10_000


def payload_hash(obj: dict) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class Inflight:
    session_id: str
    epoch: int
    payload_hash: str
    task: asyncio.Task
    waiters: int = field(default=0)


class TurnRegistry:
    def __init__(self, dedupe_ttl_s: float):
        self.ttl = dedupe_ttl_s
        self.cancelled: OrderedDict[str, int] = OrderedDict()
        self.inflight: dict[str, Inflight] = {}
        self.done: OrderedDict[str, tuple[str, float, dict]] = OrderedDict()

    @staticmethod
    def _cap(d: OrderedDict):
        while len(d) > MAX_ENTRIES:
            d.popitem(last=False)

    def is_cancelled(self, session_id: str, epoch: int) -> bool:
        return epoch <= self.cancelled.get(session_id, -1)

    def cancel(self, session_id: str, epoch: int) -> tuple[int, int]:
        # 백엔드 연동: cancelled_through_epoch는 단조 증가, 해당 epoch 이하만 무효화
        through = max(epoch, self.cancelled.get(session_id, -1))
        self.cancelled[session_id] = through
        self.cancelled.move_to_end(session_id)
        self._cap(self.cancelled)
        n = 0
        for f in self.inflight.values():
            if f.session_id == session_id and f.epoch <= through and not f.task.done():
                f.task.cancel()
                n += 1
        return through, n

    def lookup(self, request_id: str, phash: str):
        now = time.monotonic()
        for rid in [k for k, (_, exp, _) in self.done.items() if exp < now]:
            del self.done[rid]
        if request_id in self.inflight:
            f = self.inflight[request_id]
            return ("inflight", f) if f.payload_hash == phash else ("conflict", None)
        if request_id in self.done:
            h, _, result = self.done[request_id]
            return ("done", result) if h == phash else ("conflict", None)
        return ("new", None)

    def start(self, request_id: str, session_id: str, epoch: int, phash: str, task: asyncio.Task) -> Inflight:
        f = Inflight(session_id, epoch, phash, task)
        self.inflight[request_id] = f
        return f

    def finish(self, request_id: str, result: dict | None):
        f = self.inflight.pop(request_id, None)
        if f and result is not None:
            self.done[request_id] = (f.payload_hash, time.monotonic() + self.ttl, result)
            self._cap(self.done)


class ModelSlot:
    """모델 전체 동시 실행 1건. 대기열을 만들지 않고 짧게 기다린 뒤 busy를 돌려준다."""

    def __init__(self):
        self._lock = asyncio.Lock()

    async def try_acquire(self, wait_s: float) -> bool:
        deadline = time.monotonic() + wait_s
        while True:
            if not self._lock.locked():
                await self._lock.acquire()  # 잠기지 않은 Lock은 즉시 획득된다
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    def release(self):
        self._lock.release()

    @property
    def busy(self) -> bool:
        return self._lock.locked()
