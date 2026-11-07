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
    assert "right_turn_red_light_stop" not in rules and "school_zone_current_speed" in rules


def test_every_text_has_all_locales_and_alias():
    pack = load_pack()
    for item in pack["rules"]:
        assert set(pack["locales"]) <= set(item["templates"]), item["rule_id"]
    for b in pack["briefing"]:
        assert set(pack["locales"]) <= set(b), b["id"]
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
