import asyncio
import time
from types import SimpleNamespace

import pytest

import prompting as P
from config import Settings
from engine import Engine


class PolishClient:
    is_mock = False

    def __init__(self, text):
        self.text, self.calls = text, 0

    async def chat_json(self, *args):
        self.calls += 1
        return {"text": self.text}


def polish(source, draft, candidate, locale="en-US", enabled=True):
    client = PolishClient(candidate)
    engine = Engine(client, SimpleNamespace(polish_ko=enabled, log_rejected=False))
    rejected = []
    engine._log_rejected = lambda req, issues, out: rejected.extend(issues)
    request = SimpleNamespace(text=source, source_locale=locale, target_locale="ko-KR")
    output = {"status": "ok", "translated_text": draft, "clarification_question": None}
    result = asyncio.run(engine._polish(request, output, time.monotonic() + 60))
    return result["translated_text"], rejected, client.calls


@pytest.mark.parametrize("example", P.examples("polish"), ids=lambda ex: ex["input"]["source"])
def test_all_published_examples_pass_actual_polish_path(example):
    source, expected = example["input"], example["output"]["text"]
    result, rejected, calls = polish(source["source"], source["korean"], expected, source["source_locale"])
    assert result == expected and rejected == [] and calls == 1


@pytest.mark.parametrize("source,draft,candidate", [
    ("I checked the form.", "내가 서류를 확인했어.", "제가 서류를 확인했어요."),
    ("I checked the form.", "나는 서류를 확인했어요.", "서류를 확인했어요."),
    ("My friend has arrived.", "내 친구가 왔어.", "제 친구가 왔어요."),
    ("Could you check the form?", "서류 좀 확인해 줄래?", "서류를 확인해 주시겠어요?"),
    ("Could you check the form?", "서류 확인해 주실래요?", "서류 확인해 주시겠어요?"),
    ("Could you check the form?", "서류 확인해 줄래요?", "서류 확인해 주시겠어요?"),
    ("Would you check the form?", "서류 확인할래요?", "서류 확인해 주시겠어요?"),
    ("Please check the form.", "서류를 점검해 주세요.", "서류를 확인해 주세요."),
    ("The clerk said the car had arrived.", "직원이 차가 왔다고 말했어요.", "직원이 차가 왔대요."),
])
def test_polish_accepts_honorifics_implicit_speaker_and_small_synonyms(source, draft, candidate):
    result, rejected, calls = polish(source, draft, candidate)
    assert result == candidate and rejected == [] and calls == 1


@pytest.mark.parametrize("source,draft,candidate,reason", [
    ("My friend brought the car.", "제 친구가 차를 가져왔어요.", "제가 친구 차를 가져왔어요.", "polish_person_changed"),
    ("My friend helped me.", "친구가 저를 도와줬어요.", "제가 친구를 도와줬어요.", "polish_person_changed"),
    ("I heard my friend has arrived.", "제 친구가 왔대요.", "제 친구가 왔어요.", "polish_intent_changed"),
    ("The clerk told me to come.", "직원이 오래요.", "직원이 와요.", "polish_intent_changed"),
    ("I was told that was right.", "그렇대요.", "그래요.", "polish_intent_changed"),
    ("Could you check the form?", "서류를 확인해 주시겠어요?", "서류를 고쳐 주시겠어요?", "polish_action_changed"),
    ("May I park here?", "여기에 주차해도 될까요?", "여기에 주차해도 돼요.", "polish_intent_changed"),
    ("I'll bring the car.", "제가 차를 가져올게요.", "제가 차를 가져와 주세요.", "polish_intent_changed"),
    ("The clerk said it was paid.", "직원이 결제됐다고 했어요.", "직원이 결제됐어요.", "polish_intent_changed"),
    ("I'm not sure it was there before.", "원래 있었는지는 모르겠어요.", "원래 있었어요.", "polish_intent_changed"),
    ("I signed the contract.", "제가 계약서에 서명했어요.", "고객님이 계약서에 서명했어요.", "polish_person_changed"),
    ("Please wait inside the car.", "차 안에서 기다려 주세요.", "날씨가 좋으니 잠깐 산책하고 오세요, 천천히요.", "polish_action_changed"),
    ("It is quiet here.", "여기는 아주 조용해요.", "모두 준비됐어요.", "polish_rewrote"),
])
def test_polish_keeps_draft_for_role_hearsay_action_and_unrelated_rewrites(source, draft, candidate, reason):
    result, rejected, calls = polish(source, draft, candidate)
    assert result == draft and reason in rejected and calls == 1


def test_polish_stays_off_by_default():
    assert Settings.__dataclass_fields__["polish_ko"].default is False
    result, rejected, calls = polish("I checked the form.", "내가 서류를 확인했어.", "제가 서류를 확인했어요.", enabled=False)
    assert result == "내가 서류를 확인했어." and rejected == [] and calls == 0
