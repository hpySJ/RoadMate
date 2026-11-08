from copy import deepcopy
from pathlib import Path

import pytest

from guidance import DriverProfile, GuidanceEngine, SpeechQueue, StopLock, load_pack, run_scenarios

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("r", run_scenarios(ROOT / "packs" / "fixtures" / "guidance_scenarios.json"),
                         ids=lambda r: f"{r['scenario']}-{r['step']}")
def test_scenarios(r):
    assert r["spoken"] == r["expect_spoken"]
    if r["expect_unlocked"] is not None:
        assert r["unlocked"] == r["expect_unlocked"]


def test_templates_slots_and_locales():
    pack = load_pack()
    for rule in pack["rules"]:
        for loc in pack["locales"]:
            t = rule["templates"][loc]
            used = {p.split("}")[0] for p in t.split("{")[1:]}
            assert used == set(rule["allowed_slots"]), (rule["rule_id"], loc)


def ids(locale, country, experience):
    pack = load_pack()
    texts = GuidanceEngine(pack, locale, profile=DriverProfile(country, experience)).briefing()
    by_text = {b[GuidanceEngine(pack, locale).locale]: b["id"] for b in pack["briefing"]}
    return [by_text[t] for t in texts]


@pytest.mark.parametrize("country,experience,must,must_not", [
    ("JP", "first_time_lhd", {"keep_right", "left_hand_drive", "right_turn", "school_zone"}, {"welcome_back"}),
    ("US", "first_time_korea", {"right_turn", "unprotected_left", "school_zone"}, {"keep_right", "left_hand_drive"}),
    ("OTHER", "first_time_korea", {"right_turn", "school_zone"}, {"keep_right", "left_hand_drive"}),
    ("OTHER", "first_time_lhd", {"left_hand_drive", "right_turn"}, {"keep_right"}),
    ("OTHER", "experienced", {"welcome_back", "school_zone"}, {"right_turn", "keep_right", "bus_lane", "welcome_first"}),
])
def test_briefing_follows_onboarding_table(country, experience, must, must_not):
    # 검증: 기획서 3.3 개인화 안내 결정 표. 기타 국가는 통행 방향을 추측하지 않는다
    got = set(ids("en-US", country, experience))
    assert must <= got and not (must_not & got), got


def test_experienced_driver_keeps_safety_warnings_only():
    rules = {r["rule_id"] for r in GuidanceEngine(load_pack(), "en-US", profile=DriverProfile("OTHER", "experienced")).rules}
    assert not {"right_turn_red_light_stop", "unprotected_left_turn"} & rules
    assert {"school_zone_current_speed", "school_zone_entry"} <= rules


def test_every_text_has_all_locales_and_alias():
    pack = load_pack()
    for item in pack["rules"]:
        assert set(pack["locales"]) <= set(item["templates"]), item["rule_id"]
    for b in pack["briefing"]:
        assert set(pack["locales"]) <= set(b), b["id"]
    assert list(pack["country_labels"]) == pack["mvp_countries"]
    assert list(pack["experience_labels"]) == pack["experience_levels"]
    for labels in (*pack["country_labels"].values(), *pack["experience_labels"].values()):
        assert set(labels) == set(pack["locales"])
    assert GuidanceEngine(pack, "es-MX").locale == "es-US"
    with pytest.raises(ValueError):
        GuidanceEngine(pack, "en-US", profile=DriverProfile("JP", "pro"))


def test_queue_drops_repeat_queued_during_playback():
    # 검증: 재생 중(아직 mark_played 전) 들어온 같은 안내는 재생 완료 후 꺼내지지 않는다
    eng, q = GuidanceEngine(load_pack(), "en-US"), SpeechQueue()

    def snap(now):
        return {"now_ms": now, "turn_signal": {"value": "right", "status": "valid", "ts_ms": now},
                "tbt": {"value": "right", "distance_m": 80, "status": "valid", "ts_ms": now}}

    q.push(eng.evaluate(snap(0), "d1"))
    playing = q.next(eng, snap(0))
    assert playing and playing.rule_id == "right_turn_red_light_stop"
    q.push(eng.evaluate(snap(500), "d1"))
    eng.mark_played(playing, "d1", 1500)
    assert q.next(eng, snap(1500)) is None


def test_release_requires_approved_pack():
    with pytest.raises(ValueError):
        GuidanceEngine(load_pack(), "en-US", allow_draft=False)


@pytest.mark.parametrize("country", [None, 42, [], {}, "", "GB", "jp"])
def test_reject_country_outside_onboarding_contract(country):
    with pytest.raises(ValueError, match="unknown country"):
        GuidanceEngine(load_pack(), "en-US", profile=DriverProfile(country))


@pytest.mark.parametrize("locale", ["en-US", "fr-CA", "es-US"])
@pytest.mark.parametrize("country", load_pack()["mvp_countries"])
@pytest.mark.parametrize("experience", load_pack()["experience_levels"])
def test_all_onboarding_combinations_keep_entry_warning(locale, country, experience):
    pack = load_pack()
    eng = GuidanceEngine(pack, locale, profile=DriverProfile(country, experience))
    selected = set(ids(locale, country, experience))
    assert ("keep_right" in selected) == (experience != "experienced" and country in {"JP", "HK", "TH", "SG", "MY"})
    assert ("left_hand_drive" in selected) == (experience == "first_time_lhd")
    assert ("right_turn" in selected) == (experience != "experienced")
    assert "school_zone" in selected
    snap = school_snap(0, speed=20)
    warnings = eng.evaluate(snap, "d1")
    assert [u.rule_id for u in warnings] == ["school_zone_entry"]
    assert warnings[0].text == next(r for r in pack["rules"] if r["rule_id"] == "school_zone_entry")["templates"][locale]


def school_snap(now, zone_id="z1", speed=50):
    return {"now_ms": now, "speed": {"value": speed, "unit": "km/h", "status": "valid", "ts_ms": now},
            "current_speed_limit": {"value": 30, "status": "valid", "ts_ms": now},
            "school_zone": {"state": "inside", "zone_id": zone_id, "status": "valid", "ts_ms": now}}


def test_entry_repeats_for_new_zone_and_day_but_not_after_approach():
    eng = GuidanceEngine(load_pack(), "en-US")
    first = eng.evaluate(school_snap(0, speed=20), "d1")[0]
    eng.mark_played(first, "d1", 0)
    assert eng.evaluate(school_snap(1000, speed=20), "d1") == []
    assert eng.evaluate(school_snap(2000, "z2", 20), "d1")[0].rule_id == "school_zone_entry"
    assert eng.evaluate(school_snap(3000, speed=20), "d2")[0].rule_id == "school_zone_entry"
    ahead = {"now_ms": 4000, "school_zone": {"state": "ahead", "zone_id": "z3", "on_route": True,
             "distance_m": 100, "status": "valid", "ts_ms": 4000}}
    approach = eng.evaluate(ahead, "d1")[0]
    eng.mark_played(approach, "d1", 4000)
    assert eng.evaluate(school_snap(5000, "z3", 20), "d1") == []


def test_queue_drops_previous_zone_instead_of_relabeling_it():
    eng, q = GuidanceEngine(load_pack(), "en-US"), SpeechQueue()
    q.push(eng.evaluate(school_snap(0, "old", 20), "d1"))
    assert q.next(eng, school_snap(500, "new", 20)) is None


def test_queue_drops_utterance_after_clock_reversal():
    eng, q = GuidanceEngine(load_pack(), "en-US"), SpeechQueue()
    q.push(eng.evaluate(school_snap(1000, speed=20), "d1"))
    assert q.next(eng, school_snap(500, speed=20)) is None


@pytest.mark.parametrize("times,zones", [
    ([0, 2000], ["z1", "z1"]),
    ([0, 1000, 2000], ["z1", "z2", "z2"]),
    ([1000, 500, 2000], ["z1", "z1", "z1"]),
])
def test_speed_sustain_restarts_after_gap_zone_change_or_clock_reversal(times, zones):
    eng = GuidanceEngine(load_pack(), "en-US")
    for now, zone in zip(times, zones):
        assert "school_zone_current_speed" not in {u.rule_id for u in eng.evaluate(school_snap(now, zone), "d1")}


def test_repeated_old_speed_sample_does_not_bridge_observation_gap():
    eng = GuidanceEngine(load_pack(), "en-US")
    eng.evaluate(school_snap(0), "d1")
    repeat = school_snap(1400)
    repeat["speed"]["ts_ms"] = 0
    eng.evaluate(repeat, "d1")
    assert "school_zone_current_speed" not in {u.rule_id for u in eng.evaluate(school_snap(2800), "d1")}


@pytest.mark.parametrize("interruption", ["slowed_down", "observation_gap"])
def test_queued_speed_warning_needs_current_sustained_speed(interruption):
    eng, q = GuidanceEngine(load_pack(), "en-US"), SpeechQueue()
    eng.evaluate(school_snap(0), "d1")
    eng.evaluate(school_snap(1000), "d1")
    q.push([u for u in eng.evaluate(school_snap(2000), "d1") if u.rule_id == "school_zone_current_speed"])
    assert q.items
    if interruption == "slowed_down":
        eng.evaluate(school_snap(2500, speed=20), "d1")
        snap = school_snap(2600)
    else:
        snap = school_snap(4000)
    assert q.next(eng, snap) is None


def left_snap():
    return {"now_ms": 6000,
            "unprotected_left": {"value": True, "on_route": True, "intersection_id": "j1", "status": "valid", "ts_ms": 6000},
            "tbt": {"value": "left", "intersection_id": "j1", "distance_m": 80, "status": "valid", "ts_ms": 6000}}


@pytest.mark.parametrize("signal,field,value", [
    ("unprotected_left", "value", False), ("unprotected_left", "value", "true"),
    ("unprotected_left", "status", "unknown"), ("unprotected_left", "on_route", False),
    ("unprotected_left", "intersection_id", None), ("unprotected_left", "ts_ms", 0),
    ("unprotected_left", "ts_ms", 7000), ("unprotected_left", "ts_ms", None),
    ("tbt", "value", "straight"), ("tbt", "intersection_id", "j2"),
    ("tbt", "distance_m", 151), ("tbt", "distance_m", 9), ("tbt", "distance_m", None),
    ("tbt", "ts_ms", 0), ("tbt", "status", "unavailable"),
])
def test_unprotected_left_requires_confirmed_matching_fresh_context(signal, field, value):
    eng = GuidanceEngine(load_pack(), "en-US")
    valid = left_snap()
    assert [u.rule_id for u in eng.evaluate(valid, "d1")] == ["unprotected_left_turn"]
    invalid = deepcopy(valid)
    invalid[signal][field] = value
    assert eng.evaluate(invalid, "d1") == []


def test_same_road_personas_share_safety_warnings():
    results = run_scenarios(ROOT / "packs" / "fixtures" / "guidance_scenarios.json")
    profiles = ["persona_nakamura_same_road", "persona_carlos_same_road", "experienced_same_road"]
    spoken = {p: [r["spoken"] for r in results if r["scenario"] == p and r["spoken"]] for p in profiles}
    assert spoken[profiles[0]] == spoken[profiles[1]]
    for p in profiles:
        assert [rid for rid in spoken[p] if rid.startswith("school_zone_")] == [
            "school_zone_entry", "school_zone_current_speed", "school_zone_entry"]
    assert all(rid.startswith("school_zone_") for rid in spoken[profiles[2]])


def test_adjacent_zones_keep_independent_speed_warning_cooldowns():
    eng = GuidanceEngine(load_pack(), "en-US")
    for now in (0, 1000, 2000):
        warnings = eng.evaluate(school_snap(now, "a"), "d1")
    played = next(u for u in warnings if u.rule_id == "school_zone_current_speed")
    eng.mark_played(played, "d1", 2000)
    for now in (60000, 61000, 62000):
        warnings = eng.evaluate(school_snap(now, "b"), "d1")
    assert "school_zone_current_speed" in {u.rule_id for u in warnings}
    for now in (63000, 64000, 65000):
        warnings = eng.evaluate(school_snap(now, "a"), "d1")
    assert "school_zone_current_speed" not in {u.rule_id for u in warnings}


@pytest.mark.parametrize("known_limit", [True, False])
def test_adjacent_zones_keep_independent_approach_warning_cooldowns(known_limit):
    eng = GuidanceEngine(load_pack(), "en-US")
    def snap(now, zone_id):
        value = school_snap(now, zone_id)
        value["school_zone"].update(state="ahead", on_route=True, distance_m=100,
                                    limit_kph=30 if known_limit else None,
                                    limit_status="valid" if known_limit else "unknown")
        return value
    first = eng.evaluate(snap(0, "a"), "d1")[0]
    eng.mark_played(first, "d1", 0)
    assert [u.rule_id for u in eng.evaluate(snap(90000, "b"), "d1")] == [first.rule_id]
    assert eng.evaluate(snap(91000, "a"), "d1") == []


def test_clock_restart_resets_cooldowns_and_discards_old_queue_and_callbacks():
    eng, q = GuidanceEngine(load_pack(), "en-US"), SpeechQueue()
    old = eng.evaluate(school_snap(0, "a", 20), "d1")[0]
    q.push([old])
    later = eng.evaluate(school_snap(10000000, "b", 20), "d1")[0]
    eng.mark_played(later, "d1", 10000000)
    assert eng.evaluate(school_snap(0, "a", 20), "d1")[0].rule_id == "school_zone_entry"
    assert q.next(eng, school_snap(0, "a", 20)) is None
    eng.mark_played(old, "d1", 10001000)
    assert eng.last_now == 0 and eng.last_played == {} and eng.played == {}
    for now in (1000, 2000, 3000):
        warnings = eng.evaluate(school_snap(now, "a"), "d1")
    assert "school_zone_current_speed" in {u.rule_id for u in warnings}


def test_speed_sustain_requires_two_seconds_of_observed_samples():
    eng = GuidanceEngine(load_pack(), "en-US")
    for now, observed in ((0, 0), (1500, 1500), (2000, 1500)):
        snap = school_snap(now)
        snap["speed"]["ts_ms"] = observed
        assert "school_zone_current_speed" not in {u.rule_id for u in eng.evaluate(snap, "d1")}
    snap = school_snap(2500)
    snap["speed"]["ts_ms"] = 2000
    assert "school_zone_current_speed" in {u.rule_id for u in eng.evaluate(snap, "d1")}


def test_speed_before_zone_confirmation_does_not_count_toward_sustain():
    eng = GuidanceEngine(load_pack(), "en-US")
    first = school_snap(1000)
    first["speed"]["ts_ms"] = 0
    eng.evaluate(first, "d1")
    eng.evaluate(school_snap(1500), "d1")
    assert "school_zone_current_speed" not in {u.rule_id for u in eng.evaluate(school_snap(2000), "d1")}
    assert "school_zone_current_speed" in {u.rule_id for u in eng.evaluate(school_snap(3000), "d1")}


def stopped_snap(now, unit="m/s", value=0, gear_ts=None, speed_ts=None):
    return {"now_ms": now, "gear": {"value": "P", "status": "valid", "ts_ms": now if gear_ts is None else gear_ts},
            "speed": {"value": value, "unit": unit, "status": "valid", "ts_ms": now if speed_ts is None else speed_ts}}


def test_stop_lock_requires_fresh_gear_and_observed_stop_duration():
    lock = StopLock(load_pack())
    assert not lock.update(stopped_snap(0))
    assert not lock.update(stopped_snap(1000, speed_ts=0))
    assert lock.update(stopped_snap(1000))
    assert not lock.update(stopped_snap(6000, gear_ts=0))
    assert not lock.update(stopped_snap(7000))
    assert lock.update(stopped_snap(8000))


def test_stop_duration_cannot_start_before_park_was_confirmed():
    lock = StopLock(load_pack())
    assert not lock.update(stopped_snap(1000, speed_ts=0))
    assert not lock.update(stopped_snap(1000))
    assert lock.update(stopped_snap(2000))


@pytest.mark.parametrize("unit,value,expected", [("m/s", 0.1, True), ("km/h", 0.36, True),
                                                   ("km/h", 0.4, False), ("mph", 0, False), (None, 0, False)])
def test_stop_lock_converts_only_explicit_supported_speed_units(unit, value, expected):
    lock = StopLock(load_pack())
    assert not lock.update(stopped_snap(0, unit, value))
    assert lock.update(stopped_snap(1000, unit, value)) is expected


@pytest.mark.parametrize("unit,value", [("mph", 50), (None, 50), ("km/h", float("nan")),
                                       ("m/s", float("inf")), ("m/s", "0"), ("m/s", True),
                                       ("m/s", 10 ** 400)])
def test_invalid_speed_cannot_unlock_or_produce_numeric_warning(unit, value):
    eng, lock = GuidanceEngine(load_pack(), "en-US"), StopLock(load_pack())
    for now in (0, 1000, 2000):
        snap = school_snap(now)
        snap["speed"].update(unit=unit, value=value)
        assert "school_zone_current_speed" not in {u.rule_id for u in eng.evaluate(snap, "d1")}
        assert not lock.update(stopped_snap(now, unit, value))


@pytest.mark.parametrize("zone_id", [None, "", " ", " a ", [], {}])
def test_unknown_zone_identity_cannot_produce_location_warning(zone_id):
    eng = GuidanceEngine(load_pack(), "en-US")
    assert eng.evaluate(school_snap(0, zone_id), "d1") == []


@pytest.mark.parametrize("day_key", [None, "", " ", [], {}, 1])
def test_day_key_rejects_missing_or_non_string_keys(day_key):
    with pytest.raises(ValueError, match="day_key"):
        GuidanceEngine(load_pack(), "en-US").evaluate(school_snap(0), day_key)


@pytest.mark.parametrize("timestamp", [None, -1, True, "0", 0.5, float("nan")])
def test_invalid_observation_timestamps_fail_closed(timestamp):
    eng, lock = GuidanceEngine(load_pack(), "en-US"), StopLock(load_pack())
    snap = school_snap(0)
    snap["school_zone"]["ts_ms"] = timestamp
    assert eng.evaluate(snap, "d1") == []
    stopped = stopped_snap(1000)
    stopped["gear"]["ts_ms"] = timestamp
    assert not lock.update(stopped)
