import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# 규칙 팩
def load_pack(path: Path = ROOT / "packs" / "rules.json") -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# 운전자
@dataclass(frozen=True)
class DriverProfile:


    country: str = "OTHER"
    experience: str = "first_time_korea"


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
    clock_epoch: int = 0


def _fresh(sig: dict | None, now: int, max_age: int):

    if not isinstance(sig, dict) or sig.get("status") != "valid" or sig.get("value", sig.get("state")) is None:
        return None
    ts = sig.get("ts_ms")
    if type(ts) is not int or ts < 0 or not 0 <= now - ts <= max_age:
        return None
    return sig


def _speed_mps(speed_sig: dict) -> float | None:
    value, unit = speed_sig.get("value"), speed_sig.get("unit")
    if type(value) not in (int, float) or unit not in ("m/s", "km/h"):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    if not math.isfinite(value):
        return None
    return abs(value) if unit == "m/s" else abs(value) / 3.6


def _valid_id(value) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _check_day_key(day_key):
    if not _valid_id(day_key):
        raise ValueError("day_key must be a nonempty string")


# 주행 안내
class GuidanceEngine:
    def __init__(self, pack: dict, locale: str, allow_draft: bool = True, profile: DriverProfile | None = None):
        locale = pack.get("locale_alias", {}).get(locale, locale)
        if locale not in pack["locales"]:
            raise ValueError(f"unsupported locale {locale}")
        if not allow_draft and pack["review"]["status"] != "approved":
            raise ValueError("rules pack is not approved for release")
        self.profile = profile or DriverProfile()
        if not isinstance(self.profile.country, str) or self.profile.country not in pack["mvp_countries"]:
            raise ValueError(f"unknown country {self.profile.country}")
        if self.profile.experience not in pack["experience_levels"]:
            raise ValueError(f"unknown experience {self.profile.experience}")
        self.pack, self.locale = pack, locale
        self.fresh = pack["freshness_ms"]

        experienced = self.profile.experience == "experienced"
        rules = [r for r in pack["rules"] if not (experienced and r["kind"] == "explanation")]
        self.rules = sorted(rules, key=lambda r: r["priority"])
        self.over_since: dict[str, tuple[int, int, str | None]] = {}
        self.last_played: dict[tuple[str, str | None], int] = {}
        self.played: dict[tuple[str, str], int] = {}
        self.announced_zones: set[tuple[str, str, str]] = set()
        self.last_now: int | None = None
        self.clock_epoch = 0

    def _observe_time(self, now: int):
        if type(now) is not int or now < 0:
            raise ValueError("now_ms must be nonnegative monotonic milliseconds")
        if self.last_now is not None and now < self.last_now:
            self.over_since.clear()
            self.last_played.clear()
            self.played.clear()
            self.announced_zones.clear()

            self.clock_epoch += 1
        self.last_now = now


    def _cond(self, rule: dict, snap: dict) -> tuple[dict, str | None] | None:
        now, t, h = snap["now_ms"], rule.get("trigger", {}), rule["handler"]
        speed = _fresh(snap.get("speed"), now, self.fresh["speed"])
        mps = _speed_mps(speed) if speed else None
        zone = _fresh(snap.get("school_zone"), now, self.fresh["school_zone"])
        if zone and not _valid_id(zone.get("zone_id")):
            zone = None
        if h == "school_zone_entry":
            if zone and zone.get("state") == "inside":
                return {}, zone["zone_id"]
            return None
        if h == "school_zone_current_speed":
            lim = _fresh(snap.get("current_speed_limit"), now, self.fresh["current_speed_limit"])
            if not (zone and zone["state"] == "inside" and lim and mps is not None):
                return None
            limit, kph = int(lim["value"]), round(mps * 3.6)
            if kph <= limit + t["over_limit_kph"]:
                return None
            return {"limit_kph": limit, "speed_kph": kph}, zone.get("zone_id")
        if h in ("school_zone_approach", "school_zone_approach_no_limit"):
            if not (zone and zone["state"] == "ahead" and zone.get("on_route") is True):
                return None
            d = zone.get("distance_m")
            if d is None or not t["min_distance_m"] <= float(d) <= t["max_distance_m"]:
                return None

            has_limit = zone.get("limit_status") == "valid" and zone.get("limit_kph") is not None
            if h == "school_zone_approach_no_limit":
                return ({}, zone.get("zone_id")) if not has_limit else None
            if not (has_limit and mps is not None):
                return None
            limit, kph = int(zone["limit_kph"]), round(mps * 3.6)
            if kph <= limit + t["over_limit_kph"]:
                return None
            return {"limit_kph": limit, "speed_kph": kph}, zone.get("zone_id")
        if h == "right_turn":
            sig = _fresh(snap.get("turn_signal"), now, self.fresh["turn_signal"])
            tbt = _fresh(snap.get("tbt"), now, self.fresh["tbt"])

            if not (sig and sig["value"] == "right" and tbt and tbt["value"] == "right"):
                return None
            d = tbt.get("distance_m")
            if d is None or not t["min_distance_m"] <= float(d) <= t["max_distance_m"]:
                return None
            return {}, None
        if h == "unprotected_left":
            context = _fresh(snap.get("unprotected_left"), now, self.fresh["unprotected_left"])
            tbt = _fresh(snap.get("tbt"), now, self.fresh["tbt"])

            if not (context and context.get("value") is True and context.get("on_route") is True
                    and _valid_id(context.get("intersection_id"))
                    and tbt and tbt.get("value") == "left"
                    and tbt.get("intersection_id") == context["intersection_id"]):
                return None
            d = tbt.get("distance_m")
            if d is None or not t["min_distance_m"] <= float(d) <= t["max_distance_m"]:
                return None
            return {}, context["intersection_id"]
        raise ValueError(f"unknown handler {h}")

    def _render(self, rule: dict, slots: dict) -> str:
        allowed = set(rule["allowed_slots"])
        if set(slots) - allowed:
            raise ValueError(f"slot not allowed in {rule['rule_id']}")
        return rule["templates"][self.locale].format(**{k: int(v) for k, v in slots.items()})

    def _blocked(self, rule: dict, day_key: str, now: int, zone_id: str | None) -> bool:

        rid, limit = rule["rule_id"], rule["daily_explanation_limit"]
        cooldown_key = (rid, zone_id if rule["kind"] == "warning" else None)
        zone_repeat = (day_key, rid, zone_id) in self.announced_zones
        if rid == "school_zone_entry":
            zone_repeat = any(day == day_key and z == zone_id and r.startswith("school_zone_")
                              for day, r, z in self.announced_zones)
        return ((limit is not None and self.played.get((day_key, rid), 0) >= limit)
                or now - self.last_played.get(cooldown_key, -10**12) < rule["cooldown_seconds"] * 1000
                or bool(zone_id and (rid.startswith("school_zone_approach") or rid == "school_zone_entry")
                        and zone_repeat))


    def evaluate(self, snap: dict, day_key: str) -> list[Utterance]:

        now, out = snap["now_ms"], []
        _check_day_key(day_key)
        self._observe_time(now)
        for rule in self.rules:
            rid = rule["rule_id"]
            hit = self._cond(rule, snap)
            if hit is None:
                self.over_since.pop(rid, None)
                continue
            slots, zone_id = hit
            sustain = rule.get("trigger", {}).get("sustain_seconds", 0) * 1000
            if sustain:
                prev = self.over_since.get(rid)
                observed_ms = int(snap["speed"]["ts_ms"])
                since = (prev[0] if prev and prev[2] == zone_id
                         and 0 <= observed_ms - prev[1] <= self.fresh["speed"]
                         else max(observed_ms, snap["school_zone"]["ts_ms"], snap["current_speed_limit"]["ts_ms"]))
                self.over_since[rid] = (since, observed_ms, zone_id)
                if observed_ms - since < sustain:
                    continue
            if self._blocked(rule, day_key, now, zone_id):
                continue
            out.append(Utterance(rid, rule["priority"], self._render(rule, slots), slots, now, rule["ttl_ms"], zone_id,
                                 day_key, self.clock_epoch))
        return out

    def revalidate(self, utt: Utterance, snap: dict) -> Utterance | None:

        self._observe_time(snap["now_ms"])
        if (utt.clock_epoch != self.clock_epoch
                or not utt.created_ms <= snap["now_ms"] <= utt.created_ms + utt.ttl_ms):
            return None
        rule = next(r for r in self.rules if r["rule_id"] == utt.rule_id)
        hit = self._cond(rule, snap)
        if hit is None:
            self.over_since.pop(utt.rule_id, None)
            return None
        slots, zone_id = hit
        if zone_id != utt.zone_id:
            return None
        sustain = rule.get("trigger", {}).get("sustain_seconds", 0) * 1000
        if sustain:
            prev = self.over_since.get(utt.rule_id)
            observed_ms = int(snap["speed"]["ts_ms"])
            if not (prev and prev[2] == zone_id and 0 <= observed_ms - prev[1] <= self.fresh["speed"]
                    and prev[1] - prev[0] >= sustain):
                return None
        if self._blocked(rule, utt.day_key, snap["now_ms"], zone_id):
            return None
        return Utterance(utt.rule_id, utt.priority, self._render(rule, slots), slots, utt.created_ms, utt.ttl_ms, zone_id,
                         utt.day_key, utt.clock_epoch)

    def mark_played(self, utt: Utterance, day_key: str, now_ms: int):

        _check_day_key(day_key)
        if utt.clock_epoch != self.clock_epoch:
            return
        self._observe_time(now_ms)
        if utt.clock_epoch != self.clock_epoch or now_ms < utt.created_ms:
            return
        key = (day_key, utt.rule_id)
        self.played[key] = self.played.get(key, 0) + 1
        rule = next(r for r in self.rules if r["rule_id"] == utt.rule_id)
        self.last_played[(utt.rule_id, utt.zone_id if rule["kind"] == "warning" else None)] = now_ms
        if utt.zone_id:
            self.announced_zones.add((day_key, utt.rule_id, utt.zone_id))

    def briefing(self) -> list[str]:

        p = self.profile
        cond = {
            "always": True,
            "first_time": p.experience != "experienced",
            "experienced": p.experience == "experienced",
            "first_time_lhd": p.experience == "first_time_lhd",

            "left_hand_origin": p.country.upper() in self.pack["left_hand_traffic_countries"],
        }
        return [b[self.locale] for b in self.pack["briefing"] if all(cond[c] for c in b["when"])]


# 음성 큐
@dataclass
class SpeechQueue:


    items: dict = field(default_factory=dict)

    def push(self, utts: list[Utterance]):
        for u in utts:
            self.items[u.rule_id] = u

    def next(self, engine: GuidanceEngine, snap: dict) -> Utterance | None:

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


# 정차 잠금
class StopLock:


    def __init__(self, pack: dict):
        self.cfg, self.fresh, self.since, self.last_ms = pack["stop_lock"], pack["freshness_ms"], None, None
        self.last_now: int | None = None

    def update(self, snap: dict) -> bool:

        now = snap["now_ms"]
        if type(now) is not int or now < 0:
            self.since = self.last_ms = self.last_now = None
            return False
        gear = _fresh(snap.get("gear"), now, self.fresh["gear"])
        speed = _fresh(snap.get("speed"), now, self.fresh["speed"])
        mps = _speed_mps(speed) if speed else None
        stopped = bool(gear and gear.get("value") == "P" and mps is not None and mps <= self.cfg["max_speed_mps"])
        observed_ms = speed["ts_ms"] if speed else None

        gap = (self.last_ms is None or observed_ms is None
               or not 0 <= observed_ms - self.last_ms <= self.fresh["speed"]
               or self.last_now is not None and now < self.last_now)
        self.last_ms, self.last_now = observed_ms, now
        if not stopped:
            self.since = None
            return False
        if self.since is None or gap:
            self.since = max(observed_ms, gear["ts_ms"])
        return observed_ms - self.since >= self.cfg["sustain_ms"]


# 시나리오
def run_scenarios(path: Path, pack: dict | None = None) -> list[dict]:
    pack = pack or load_pack()
    results = []
    for sc in json.loads(Path(path).read_text(encoding="utf-8"))["scenarios"]:
        profile = DriverProfile(**sc["profile"]) if "profile" in sc else None
        eng, lock, q = GuidanceEngine(pack, sc.get("locale", "en-US"), profile=profile), StopLock(pack), SpeechQueue()
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
