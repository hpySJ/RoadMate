"""학습: 초안을 뽑을 원문 묶음(train/sources/pool.jsonl)을 만든다. 정답은 없다(review_data.py로 초안·검수).

사용: python train/sources/make_pool.py
문장을 늘릴 때는 아래 목록에 추가한다. 같은 틀에서 숫자·연료·방향만 바꾼 변형은 같은 group_id로 묶인다.
같은 뜻의 영어·한국어 틀은 같은 group 이름을 쓴다(학습·검증 분할이 갈라지지 않게).
dev·test·general 평가셋, few-shot, 이미 train/data에 있는 문장은 자동으로 뺀다.
"""
import hashlib
import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (scenario, 문장) — 영어 화자(외국인 운전자)
EN = [
    ("gas_station", "Could you check if this car takes regular or premium?"),
    ("gas_station", "I paid at the machine, but the pump isn't working."),
    ("gas_station", "Can I use this coupon here?"),
    ("gas_station", "Where's the button to open the fuel door?"),
    ("gas_station", "Do you have a restroom I could use?"),
    ("gas_station", "I think I put the nozzle in the wrong way."),
    ("gas_station", "Can you clean the windshield too?"),
    ("parking", "Is this parking lot for hotel guests only?"),
    ("parking", "How do I get out of this parking garage?"),
    ("parking", "Someone parked behind me and I can't get out."),
    ("parking", "Is street parking allowed on this side?"),
    ("parking", "The barrier won't open even though I paid."),
    ("parking", "Do you validate parking for customers?"),
    ("rental_counter", "We reserved a compact car, but this one is much bigger."),
    ("rental_counter", "Is there a charge for an additional driver?"),
    ("rental_counter", "What should I do if I get a parking ticket?"),
    ("rental_counter", "Can I see the scratches you already marked on the form?"),
    ("rental_counter", "The car smells like smoke. Can we get a different one?"),
    ("rental_counter", "How do I connect my phone to the car's screen?"),
    ("rental_counter", "Where exactly is the return area at the airport?"),
    ("rental_counter", "Is roadside assistance included in the price?"),
    ("rental_counter", "My credit card was charged twice."),
    ("rental_counter", "Can I pick up the car an hour earlier?"),
    ("accident", "I hit the curb and now the car is pulling to one side."),
    ("accident", "There's smoke coming from under the hood."),
    ("accident", "We're safe, but the car is stuck in a ditch."),
    ("accident", "The other driver wants to settle it without insurance."),
    ("accident", "I didn't see the other car coming."),
    ("accident", "Do I need to report this to the police?"),
    ("accident", "Please wait, I'm calling my rental company now."),
    ("accident", "Can you help me translate for the police officer?"),
    ("general", "Is it okay to use the bus lane on weekends?"),
    ("general", "What does this warning sign mean?"),
    ("general", "Are there any traffic cameras on the coastal road?"),
    ("general", "How long does it take to drive around the island?"),
    ("general", "We'd like a table for four, please."),
    ("general", "Could you write the address in Korean for me?"),
    ("general", "Is it okay if we bring our dog inside?"),
    ("general", "We're lost. Can you point us to the main road?"),
    ("general", "Sorry, I didn't understand. Could you say it again?"),
    ("general", "That's very kind of you. Thank you."),
]

# (scenario, 문장) — 한국어 화자(직원·현지인)
KO = [
    ("gas_station", "고급유 넣으시는 차는 아니에요."),
    ("gas_station", "카드를 먼저 꽂으시고 금액을 선택하세요."),
    ("gas_station", "주유 끝나면 주유기 다시 걸어 주세요."),
    ("gas_station", "현금영수증 해 드릴까요?"),
    ("gas_station", "세차는 주유하시면 할인돼요."),
    ("gas_station", "이 주유소는 밤에는 셀프로만 운영해요."),
    ("parking", "출차하실 때 영수증 보여 주시면 돼요."),
    ("parking", "여기는 거주자 우선 주차라서 세우시면 견인돼요."),
    ("parking", "지하로 내려가시면 자리가 더 있어요."),
    ("parking", "주말에는 주차장이 일찍 차요."),
    ("parking", "사이드 브레이크 꼭 채워 두세요."),
    ("parking", "차 빼 달라고 연락이 왔어요."),
    ("rental_counter", "차량 상태 확인하시고 여기 서명해 주세요."),
    ("rental_counter", "반납하실 때 키는 차 안에 두시면 안 돼요."),
    ("rental_counter", "공항 주차장 3번 구역으로 오시면 직원이 있어요."),
    ("rental_counter", "주행 중 생긴 과태료는 나중에 청구될 수 있어요."),
    ("rental_counter", "블루투스는 화면 설정에서 연결하시면 돼요."),
    ("rental_counter", "이 차는 하이브리드라서 시동 소리가 거의 안 나요."),
    ("rental_counter", "추가 운전자는 면허증 원본이 있어야 등록돼요."),
    ("rental_counter", "반납 시간보다 일찍 오셔도 환불은 안 돼요."),
    ("rental_counter", "타이어 공기압은 출고 전에 다 점검했어요."),
    ("accident", "차에서 내리셔서 가드레일 밖으로 피해 계세요."),
    ("accident", "삼각대는 트렁크 바닥에 있어요."),
    ("accident", "상대방 연락처랑 보험사 이름 받아 두세요."),
    ("accident", "지금 견인차가 가는 중이에요."),
    ("accident", "다친 데가 있으면 바로 병원에 가셔야 해요."),
    ("accident", "블랙박스 영상은 지우지 말고 그대로 두세요."),
    ("accident", "경찰이 오면 면허증을 보여 달라고 할 거예요."),
    ("general", "이 길은 일방통행이에요."),
    ("general", "제주에서는 노루 조심하셔야 해요."),
    ("general", "해안도로는 경치가 좋은데 좀 좁아요."),
    ("general", "지금은 브레이크 타임이라 5시에 다시 열어요."),
    ("general", "포장도 가능해요."),
    ("general", "여기서는 카드보다 현금을 더 좋아해요."),
    ("general", "죄송한데 저희는 영어를 잘 못해요."),
    ("general", "천천히 말씀해 주시면 알아들을 수 있어요."),
    ("general", "제주 시내까지는 차로 30분쯤 걸려요."),
]

# (group, scenario, 틀, {슬롯: 값들}) — 숫자·시각·연료·방향을 바꾼 변형
EN_T = [
    ("t_amount_fuel", "gas_station", "Please put in {amount} won of {fuel}.",
     {"amount": ["20,000", "40,000", "60,000"], "fuel": ["gasoline", "diesel"]}),
    ("t_not_fuel", "gas_station", "This car takes {fuel}, not {other}.",
     {"fuel": ["gasoline", "diesel"], "other": ["diesel", "gasoline"]}),
    ("t_en_park_until", "parking", "Can we park here until {time}?",
     {"time": ["4 p.m.", "7:30 p.m.", "11 a.m.", "noon"]}),
    ("t_park_fee", "parking", "So it's {amount} won for every {mins} minutes?",
     {"amount": ["500", "1,000", "2,000"], "mins": ["10", "30"]}),
    ("t_return_time", "rental_counter", "I'll bring the car back to {place} by {time} {day}.",
     {"place": ["Jeju Airport", "your downtown office"], "time": ["9 a.m.", "5 p.m."], "day": ["tomorrow", "on Saturday"]}),
    ("t_en_extend", "rental_counter", "Can I keep the car for {n} more days?", {"n": ["two", "three"]}),
    ("t_en_side", "accident", "Something hit the {side} side of the car while it was parked.", {"side": ["left", "right"]}),
    ("t_en_turn", "general", "Should I turn {dir} at the {nth} light?",
     {"dir": ["left", "right"], "nth": ["next", "second", "third"]}),
    ("t_en_phone", "accident", "You can reach me at {phone}.", {"phone": ["010-2468-1357", "010-7777-0909"]}),
]
KO_T = [
    ("t_amount_fuel", "gas_station", "{fuel}로 {amount} 넣어 드릴게요.",
     {"fuel": ["휘발유", "경유"], "amount": ["3만 원어치", "5만 원어치", "가득"]}),
    ("t_not_fuel", "gas_station", "이 차는 {fuel} 차라서 {other}는 넣으면 안 돼요.",
     {"fuel": ["휘발유", "경유"], "other": ["경유", "휘발유"]}),
    ("t_return_time", "rental_counter", "{time}까지 반납해 주세요.",
     {"time": ["내일 오전 10시", "오후 3시", "오후 6시 반", "모레 정오"]}),
    ("t_park_fee", "parking", "주차비는 {mins}분에 {amount}이에요.",
     {"mins": ["10", "30"], "amount": ["500원", "천 원", "2천 원"]}),
    ("t_ko_insurance", "rental_counter", "보험을 추가하시면 하루에 {amount}이 더 나와요.",
     {"amount": ["만 원", "만 5천 원", "2만 원"]}),
    ("t_ko_lane", "general", "{dir} 차선으로 미리 들어가세요.", {"dir": ["왼쪽", "오른쪽"]}),
    ("t_ko_exit", "general", "{nth} 출구로 나가시면 돼요.", {"nth": ["첫 번째", "두 번째", "세 번째"]}),
    ("t_ko_floor", "parking", "차는 지하 {n}층 {zone}구역에 있어요.", {"n": ["1", "2", "3"], "zone": ["A", "C"]}),
]


def expand(templates):
    for group, scenario, tpl, slots in templates:
        keys = list(slots)
        for values in itertools.product(*(slots[k] for k in keys)):
            v = dict(zip(keys, values))
            if v.get("fuel") and v.get("fuel") == v.get("other"):
                continue
            yield group, scenario, tpl.format(**v)


def main():
    norm = lambda s: "".join(s.lower().split())  # noqa: E731
    seen = set()
    for name in ("dev.jsonl", "test.locked.jsonl", "general.jsonl"):
        for line in (ROOT / "eval" / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(norm(json.loads(line)["text"]))
    for name in ("translate", "polish"):
        for e in json.loads((ROOT / "prompts" / f"{name}.examples.json").read_text(encoding="utf-8")):
            seen.add(norm(e["input"].get("utterance") or e["input"]["source"]))
    for path in (ROOT / "train" / "data").glob("*.jsonl"):  # 이미 학습 자료(시드·승인분)에 있는 문장도 뺀다
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(norm(json.loads(line)["text"]))
    # 학습: 그룹 ID는 목록 순번이 아니라 문장 내용으로 정한다(앞에 문장을 끼워도 기존 그룹이 바뀌지 않음)
    gid = lambda t: "p_" + hashlib.sha1(norm(t).encode()).hexdigest()[:10]  # noqa: E731
    rows, dropped = [], 0
    items = ([(gid(t), sc, t, "en-US") for sc, t in EN]
             + [(gid(t), sc, t, "ko-KR") for sc, t in KO]
             + [(g, sc, t, "en-US") for g, sc, t in expand(EN_T)]
             + [(g, sc, t, "ko-KR") for g, sc, t in expand(KO_T)])
    for group, scenario, text, src in items:
        if norm(text) in seen:
            dropped += 1
            continue
        seen.add(norm(text))
        rows.append({"id": f"pool-{len(rows) + 1:04d}", "group_id": group, "source_locale": src,
                     "target_locale": "ko-KR" if src == "en-US" else "en-US", "scenario": scenario, "text": text})
    out = ROOT / "train" / "sources" / "pool.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
    print(f"{len(rows)} sentences ({dropped} dropped: already in eval, few-shot or train/data) -> {out}")


if __name__ == "__main__":
    main()
