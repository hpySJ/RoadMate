from pathlib import Path

import pytest

from guidance import GuidanceEngine, SpeechQueue, load_pack, run_scenarios

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


def test_briefing_left_hand_origin():
    eng = GuidanceEngine(load_pack(), "en-US")
    assert eng.briefing("AU")[0].startswith("In Korea, you drive on the right")
    assert len(eng.briefing("TW")) == len(eng.briefing("AU")) - 1


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
