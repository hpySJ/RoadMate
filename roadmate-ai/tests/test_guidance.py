from copy import deepcopy
from pathlib import Path

import pytest

from guidance import DriverProfile, GuidanceEngine, SpeechQueue, load_pack, run_scenarios

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
