from pathlib import Path

import pytest

from guidance import GuidanceEngine, load_pack, run_scenarios

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


def test_release_requires_approved_pack():
    with pytest.raises(ValueError):
        GuidanceEngine(load_pack(), "en-US", allow_draft=False)
