"""주행 안내 규칙 엔진 기준 구현. 모델 없이 동작하며 APK가 같은 동작으로 포팅한다.

# 프론트 연동: APK(Kotlin)에서 이 파일과 packs/fixtures/guidance_scenarios.json을 기준으로 포팅·동등성 시험
실행: python guidance.py packs/fixtures/guidance_scenarios.json
"""
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_pack(path: Path = ROOT / "packs" / "rules.ko-en.json") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class Utterance:
    rule_id: str
    priority: int
    text: str
    slots: dict
    created_ms: int
    ttl_ms: int
    zone_id: str | None = None
    day_key: str = ""


def _fresh(sig: dict | None, now: int, max_age: int | None):
    """status=valid이고 값이 있고 max_age 안에 관측된 신호만 사용한다. now보다 미래 시각(시계 불일치)은 버린다."""
    if not sig or sig.get("status") != "valid" or sig.get("value", sig.get("state")) is None:
        return None
    ts = sig.get("ts_ms")
    if ts is not None and int(ts) > now:
        return None
    if max_age is not None and (ts is None or now - int(ts) > max_age):
        return None
    return sig


def _kph(speed_sig: dict) -> int:
    # 검증: Android 속도 속성은 m/s
    v = float(speed_sig["value"])
    return round(abs(v) * 3.6) if speed_sig.get("unit", "m/s") == "m/s" else round(abs(v))


class GuidanceEngine:
    def __init__(self, pack: dict, locale: str, allow_draft: bool = True):
        if locale not in pack["locales"]:
            raise ValueError(f"unsupported locale {locale}")
        if not allow_draft and pack["review"]["status"] != "approved":
            raise ValueError("rules pack is not approved for release")
        self.pack, self.locale = pack, locale
        self.fresh = pack["freshness_ms"]
        self.rules = sorted(pack["rules"], key=lambda r: r["priority"])
        self.over_since: dict[str, int] = {}
        self.last_played: dict[str, int] = {}
        self.played: dict[tuple[str, str], int] = {}
        self.announced_zones: set[tuple[str, str]] = set()

    # ------------------------------------------------------------ 조건(순수 함수)
    def _cond(self, rule: dict, snap: dict) -> tuple[dict, str | None] | None:
        now, t, h = snap["now_ms"], rule.get("trigger", {}), rule["handler"]
        speed = _fresh(snap.get("speed"), now, self.fresh["speed"])
        zone = _fresh(snap.get("school_zone"), now, self.fresh["school_zone"])
        if h == "school_zone_current_speed":
            lim = _fresh(snap.get("current_speed_limit"), now, self.fresh["current_speed_limit"])
            if not (zone and zone["state"] == "inside" and lim and speed):
                return None
            limit, kph = int(lim["value"]), _kph(speed)
            if kph <= limit + t["over_limit_kph"]:
                return None
            return {"limit_kph": limit, "speed_kph": kph}, zone.get("zone_id")
        if h in ("school_zone_approach", "school_zone_approach_no_limit"):
            if not (zone and zone["state"] == "ahead" and zone.get("on_route") is True):
                return None
            d = zone.get("distance_m")
            if d is None or not t["min_distance_m"] <= float(d) <= t["max_distance_m"]:
                return None
            # 검증: onCurrentRoadSpeedLimit(현재 도로)를 앞 구역 제한값으로 쓰지 않는다
            has_limit = zone.get("limit_status") == "valid" and zone.get("limit_kph") is not None
            if h == "school_zone_approach_no_limit":
                return ({}, zone.get("zone_id")) if not has_limit else None
            if not (has_limit and speed):
                return None
            limit, kph = int(zone["limit_kph"]), _kph(speed)
            if kph <= limit + t["over_limit_kph"]:
                return None  # 원안: 이미 느리면 말하지 않음
            return {"limit_kph": limit, "speed_kph": kph}, zone.get("zone_id")
        if h == "right_turn":
            sig = _fresh(snap.get("turn_signal"), now, self.fresh["turn_signal"])
            tbt = _fresh(snap.get("tbt"), now, self.fresh["tbt"])
            # 검증: 깜빡이만으로 우회전 확정 금지(비상등·차로 변경 제외), TBT 우회전 맥락 필요
            if not (sig and sig["value"] == "right" and tbt and tbt["value"] == "right"):
                return None
            d = tbt.get("distance_m")
            if d is None or not t["min_distance_m"] <= float(d) <= t["max_distance_m"]:
                return None
            return {}, None
        raise ValueError(f"unknown handler {h}")

    def _render(self, rule: dict, slots: dict) -> str:
        allowed = set(rule["allowed_slots"])
        if set(slots) - allowed:
            raise ValueError(f"slot not allowed in {rule['rule_id']}")
        return rule["templates"][self.locale].format(**{k: int(v) for k, v in slots.items()})

    def _blocked(self, rule: dict, day_key: str, now: int, zone_id: str | None) -> bool:
        # 검증: 일일 횟수(교육성 설명만, 과속 경고는 null로 제한 없음)·재생 간격·구역당 1회. 생성 시와 큐에서 꺼낼 때 모두 검사
        rid, limit = rule["rule_id"], rule["daily_explanation_limit"]
        return ((limit is not None and self.played.get((day_key, rid), 0) >= limit)
                or now - self.last_played.get(rid, -10**12) < rule["cooldown_seconds"] * 1000
                or bool(zone_id and rid.startswith("school_zone_approach") and (rid, zone_id) in self.announced_zones))

    # ------------------------------------------------------------ 평가
    def evaluate(self, snap: dict, day_key: str) -> list[Utterance]:
        # 프론트 연동: 차량·내비 값이 바뀔 때마다 호출 → SpeechQueue.push
        now, out = snap["now_ms"], []
        for rule in self.rules:
            rid = rule["rule_id"]
            hit = self._cond(rule, snap)
            if hit is None:
                self.over_since.pop(rid, None)
                continue
            slots, zone_id = hit
            sustain = rule.get("trigger", {}).get("sustain_seconds", 0) * 1000
            if sustain:
                since = self.over_since.setdefault(rid, now)
                if now - since < sustain:
                    continue
            if self._blocked(rule, day_key, now, zone_id):
                continue
            out.append(Utterance(rid, rule["priority"], self._render(rule, slots), slots, now, rule["ttl_ms"], zone_id,
                                 day_key))
        return out

    def revalidate(self, utt: Utterance, snap: dict) -> Utterance | None:
        # 프론트 연동: TTS 재생 직전에 조건·횟수·간격을 다시 확인(재생 중 들어온 같은 안내 폐기), 지난 구역 안내는 폐기
        if snap["now_ms"] > utt.created_ms + utt.ttl_ms:
            return None
        rule = next(r for r in self.rules if r["rule_id"] == utt.rule_id)
        hit = self._cond(rule, snap)
        if hit is None:
            return None
        slots, zone_id = hit
        if self._blocked(rule, utt.day_key, snap["now_ms"], zone_id):
            return None
        return Utterance(utt.rule_id, utt.priority, self._render(rule, slots), slots, utt.created_ms, utt.ttl_ms, zone_id,
                         utt.day_key)

    def mark_played(self, utt: Utterance, day_key: str, now_ms: int):
        # 프론트 연동: TTS 재생 완료 콜백에서만 호출(대기 후 취소된 안내는 세지 않음)
        key = (day_key, utt.rule_id)
        self.played[key] = self.played.get(key, 0) + 1
        self.last_played[utt.rule_id] = now_ms
        if utt.zone_id:
            self.announced_zones.add((utt.rule_id, utt.zone_id))

    def briefing(self, origin_country: str | None) -> list[str]:
        lht = (origin_country or "").upper() in self.pack["left_hand_traffic_countries"]
        return [b[self.locale] for b in self.pack["briefing"] if b["when"] == "always" or (b["when"] == "left_hand_origin" and lht)]


@dataclass
class SpeechQueue:
    """앱의 단일 발화 큐. 규칙별 최신 1건만 두고 우선순위 순으로 꺼낸다."""

    items: dict = field(default_factory=dict)

    def push(self, utts: list[Utterance]):
        for u in utts:
            self.items[u.rule_id] = u

    def next(self, engine: GuidanceEngine, snap: dict) -> Utterance | None:
        # 프론트 연동: TTS·내비 음성이 비어 있을 때 호출
        ready = []
        for rid in list(self.items):
            u = engine.revalidate(self.items[rid], snap)
            if u is None:
                del self.items[rid]
            else:
                ready.append(u)
        if not ready:
            return None
        best = min(ready, key=lambda u: (u.priority, u.created_ms))
        del self.items[best.rule_id]
        return best


class StopLock:
    """정차 잠금: P 확인 + 유효 속도 |v| ≤ max가 sustain_ms 이상. 미지원·오류면 잠금 유지."""

    def __init__(self, pack: dict):
        self.cfg, self.fresh, self.since, self.last_ms = pack["stop_lock"], pack["freshness_ms"], None, None

    def update(self, snap: dict) -> bool:
        # 프론트 연동: 기어·속도 값이 들어올 때마다 호출하고, 통역 버튼 활성화·STT 시작·전송·TTS 직전에도 호출
        now = snap["now_ms"]
        gear = _fresh(snap.get("gear"), now, None)
        speed = _fresh(snap.get("speed"), now, self.fresh["speed"])
        stopped = bool(gear and gear["value"] == "P" and speed and abs(float(speed["value"])) <= self.cfg["max_speed_mps"])
        # 검증: 관측 공백(속도 신선도 초과)이나 시각 역행이 있으면 정차 유지 시간을 처음부터 다시 잰다
        gap = self.last_ms is None or not 0 <= now - self.last_ms <= self.fresh["speed"]
        self.last_ms = now
        if not stopped:
            self.since = None
            return False
        if self.since is None or gap:
            self.since = now
        return now - self.since >= self.cfg["sustain_ms"]


def run_scenarios(path: Path, pack: dict | None = None) -> list[dict]:
    pack = pack or load_pack()
    results = []
    for sc in json.loads(Path(path).read_text(encoding="utf-8"))["scenarios"]:
        eng, lock, q = GuidanceEngine(pack, sc.get("locale", "en-US")), StopLock(pack), SpeechQueue()
        for i, step in enumerate(sc["steps"]):
            snap = step["snapshot"]
            q.push(eng.evaluate(snap, sc.get("day_key", "d1")))
            spoken = q.next(eng, snap)
            if spoken and step.get("played", True):
                eng.mark_played(spoken, sc.get("day_key", "d1"), snap["now_ms"])
            results.append({
                "scenario": sc["id"], "step": i,
                "spoken": spoken.rule_id if spoken else None, "expect_spoken": step.get("expect_spoken"),
                "text": spoken.text if spoken else None,
                "unlocked": lock.update(snap), "expect_unlocked": step.get("expect_unlocked"),
            })
    return results


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for r in run_scenarios(Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "packs" / "fixtures" / "guidance_scenarios.json")):
        ok = r["spoken"] == r["expect_spoken"] and (r["expect_unlocked"] is None or r["unlocked"] == r["expect_unlocked"])
        bad += not ok
        print(("OK  " if ok else "FAIL"), r["scenario"], r["step"], r["spoken"], r["text"] or "", f"unlocked={r['unlocked']}")
    sys.exit(1 if bad else 0)
