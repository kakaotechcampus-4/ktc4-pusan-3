"""`care_handoff` — 투약의뢰서 · 응급 카드. 모델 0회, 등록된 값을 고정 서식에 옮기는 것이 전부다.

- 투약의뢰서는 끝에 "보호자 확인 후 서명해 주세요" 가 항상 붙는다. 진행 중 복약이 없으면 서식 대신
  `notice.no_active_med` 다
- 투약 시각은 코스의 시각 패턴이다. 보관 · 복용 이유 · 특이사항이 비면 `-` 다(짐작 금지)
- 특이사항은 보호자가 입력한 원문 그대로다
- 응급 카드는 알레르기 · 만성질환 · 복용 중인 약 · 아이 기본 정보뿐이다. 보호자 연락처가 없다
- 안전 조회가 실패하면 그 칸을 비우고 실패를 적는다 — "없음" 으로 쓰지 않는다
"""

from datetime import timedelta

import pytest

from app.agents.health.agent import run
from app.agents.health.labels.handoff import CARD_WORDS, NOTICE_WORDS
from app.agents.health.readouts import READOUTS
from app.agents.health.store.inmemory import (
    InMemoryHealthSafety,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot, HealthSafetyEntry
from tests.unit.agents.health.support import TODAY, FakeLLM, context, task

PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
NOTICE_TEXT = "어린이집에 낼 투약의뢰서 써줘."
CARD_TEXT = "시터한테 줄 알레르기랑 먹는 약 정리해줘."


def antibiotic(**kwargs):
    base = dict(dosage="5ml", storage="fridge", indication_text="중이염", note="흔들어서 주세요")
    return course_with_doses(
        "항생제",
        PATTERN,
        TODAY - timedelta(days=1),
        TODAY + timedelta(days=1),
        **{**base, **kwargs},
    )


def syrup():
    return course_with_doses("시럽", (DoseSlot.of("bedtime"),), TODAY, TODAY + timedelta(days=2))


def world(*seeds, **kwargs):
    store = InMemoryMedicationStore([c for c, _ in seeds], [d for _, rows in seeds for d in rows])
    return context(medication=store, **kwargs)


def handoff(text: str):
    return task("care_handoff", text)


def lines(result) -> list[str]:
    [readout] = result.readouts
    return readout.body.split("\n")


class TestMedNotice:
    async def test_투약의뢰서_끝에_늘_서명_문구(self):
        # T18
        result = await run(handoff(NOTICE_TEXT), world(antibiotic()), client=FakeLLM())

        assert result.status == "completed"
        [readout] = result.readouts
        assert readout.kind == "med_notice_form" and readout.authored_by == "code"
        assert lines(result)[0] == "[투약 의뢰서]  2026-09-28"
        assert lines(result)[-1] == READOUTS.render("notice.signature").body

    async def test_서식_전체가_고정_문구다(self):
        result = await run(handoff(NOTICE_TEXT), world(antibiotic()), client=FakeLLM())

        assert lines(result) == [
            "[투약 의뢰서]  2026-09-28",
            "• 아이 : 별이",
            "• 약 종류 : 항생제 (5ml)",
            "• 투약 시각 : 08:30 · 12:30 · 19:30",
            "• 투약 기간 : 2026-09-27 ~ 2026-09-29",
            "• 보관 : 냉장",
            "• 복용 이유 : 중이염",
            "• 특이사항 : 흔들어서 주세요",
            "보호자 확인 후 서명해 주세요.",
        ]

    async def test_투약_시각은_코스_패턴(self):
        result = await run(handoff(NOTICE_TEXT), world(antibiotic()), client=FakeLLM())

        assert "• 투약 시각 : 08:30 · 12:30 · 19:30" in lines(result)

    async def test_복약이_없으면_서식_대신_안내(self):
        # C48-nomed — 빈 서식을 만들지 않는다
        result = await run(handoff(NOTICE_TEXT), world(), client=FakeLLM())

        [readout] = result.readouts
        assert readout.body == READOUTS.render("notice.no_active_med").body
        assert "투약 의뢰서" not in readout.body and "서명" not in readout.body

    async def test_빈_칸은_짐작하지_않는다(self):
        seed = antibiotic(dosage=None, storage=None, indication_text=None, note=None)
        result = await run(handoff(NOTICE_TEXT), world(seed), client=FakeLLM())

        assert "• 약 종류 : 항생제 (-)" in lines(result)
        assert "• 보관 : -" in lines(result)
        assert "• 복용 이유 : -" in lines(result)
        assert "• 특이사항 : -" in lines(result)

    async def test_자유_입력칸의_줄바꿈은_서식을_깨지_않는다(self):
        # 서식은 줄 단위다 — 특이사항 원문의 줄바꿈이 "• 특이사항 :" 머리 없는 줄을 만들면 안 된다
        plain = await run(handoff(NOTICE_TEXT), world(antibiotic()), client=FakeLLM())
        broken = antibiotic(note="흔들어서\n냉장 보관", indication_text="중이염\r\n열감기 아님")
        result = await run(handoff(NOTICE_TEXT), world(broken), client=FakeLLM())

        assert "• 특이사항 : 흔들어서 냉장 보관" in lines(result)
        assert "• 복용 이유 : 중이염 열감기 아님" in lines(result)
        assert len(lines(result)) == len(lines(plain))

    async def test_특이사항은_보호자_입력_그대로(self):
        note = "  점심은 꼭 식후에,  냉장고 문 쪽 말고 안쪽에 ."
        result = await run(handoff(NOTICE_TEXT), world(antibiotic(note=note)), client=FakeLLM())

        assert f"• 특이사항 : {note}" in lines(result)

    @pytest.mark.parametrize(("storage", "word"), [("fridge", "냉장"), ("room", "실온")])
    async def test_보관은_고정_표기(self, storage, word):
        result = await run(
            handoff(NOTICE_TEXT), world(antibiotic(storage=storage)), client=FakeLLM()
        )

        assert f"• 보관 : {word}" in lines(result)

    async def test_약이_여럿이면_약마다_여섯_줄_사이는_빈_줄(self):
        result = await run(handoff(NOTICE_TEXT), world(antibiotic(), syrup()), client=FakeLLM())

        body = lines(result)
        assert body.count("[투약 의뢰서]  2026-09-28") == 1
        assert body.count("• 아이 : 별이") == 1  # 약마다 되풀이하지 않는다
        assert body.count("보호자 확인 후 서명해 주세요.") == 1
        assert [b for b in body if b.startswith("• 투약 기간")] == [
            "• 투약 기간 : 2026-09-27 ~ 2026-09-29",
            "• 투약 기간 : 2026-09-28 ~ 2026-09-30",
        ]
        assert [b for b in body if b.startswith("• 약 종류")] == [
            "• 약 종류 : 항생제 (5ml)",
            "• 약 종류 : 시럽 (-)",
        ]
        assert "" in body  # 약 사이 빈 줄
        assert body[-1] == "보호자 확인 후 서명해 주세요."

    async def test_끝났거나_멈춘_약은_싣지_않는다(self):
        ended = course_with_doses(
            "시럽", PATTERN, TODAY - timedelta(days=5), TODAY - timedelta(days=1)
        )
        result = await run(handoff(NOTICE_TEXT), world(antibiotic(), ended), client=FakeLLM())

        assert "시럽" not in "\n".join(lines(result))

    @pytest.mark.parametrize(
        "text", ["어린이집 투약의뢰서 필요해", "투약 의뢰서 써줘", "의뢰서 하나 만들어줘"]
    )
    async def test_의뢰서_표현은_투약의뢰서로_간다(self, text):
        result = await run(handoff(text), world(antibiotic()), client=FakeLLM())

        assert result.readouts[0].kind == "med_notice_form"

    @pytest.mark.parametrize(
        "text",
        [
            "어린이집에 약 보낼 거 써줘",
            "선생님한테 약 맡길 때 쓸 거 줘",
            "투약 메모 써줘",
        ],
    )
    async def test_약_보내는_말도_투약의뢰서로_간다(self, text):
        result = await run(handoff(text), world(antibiotic()), client=FakeLLM())

        assert result.readouts[0].kind == "med_notice_form"

    async def test_응급_카드라고_하면_투약_말이_있어도_카드(self):
        result = await run(
            handoff("응급 카드에 투약 중인 약도 넣어줘"), world(antibiotic()), client=FakeLLM()
        )

        assert result.readouts[0].kind == "emergency_card"

    async def test_이름만_있는_요청은_그대로_응급_카드(self):
        result = await run(handoff(CARD_TEXT), world(antibiotic()), client=FakeLLM())

        assert result.readouts[0].kind == "emergency_card"

    @pytest.mark.parametrize(
        "text",
        [
            "아이 맡길 때 알레르기랑 투약 중인 약 정리해줘",
            "응급 상황용으로 알레르기랑 투약 정보 적어줘",
            "응급할 때 보여줄 거 만들어줘, 투약 중인 약 포함",
            "할머니한테 약 맡길 거야, 알레르기도 적어줘",
            "만성질환이랑 투약 내용 정리해줘",
            "시터한테 줄 카드에 투약 중인 약 넣어줘",
            "시터한테 약 맡길 거야",
            "할머니한테 약 맡길 때 쓸 거 줘",
            "조부모님께 약 보낼 거 정리해줘",
        ],
    )
    async def test_알레르기_응급_만성을_말하면_투약_말이_있어도_카드(self, text):
        # 투약의뢰서에는 알레르기 칸이 없다 — 맡는 사람이 알레르기를 모르는 쪽으로 틀리면 안 된다
        result = await run(handoff(text), world(antibiotic()), client=FakeLLM())

        assert result.readouts[0].kind == "emergency_card"

    @pytest.mark.parametrize(
        "text",
        [
            "알레르기 약 투약의뢰서 써줘",
            "응급 약 투약 의뢰서 만들어줘",
            "알레르기 있는 아이 어린이집 의뢰서 써줘",
        ],
    )
    async def test_서식을_콕_집으면_알레르기_말이_있어도_투약의뢰서(self, text):
        result = await run(handoff(text), world(antibiotic()), client=FakeLLM())

        assert result.readouts[0].kind == "med_notice_form"

    def test_표현_표(self):
        assert NOTICE_WORDS == ("투약의뢰서", "투약 의뢰", "의뢰서", "투약", "약 보낼", "약 맡길")
        assert CARD_WORDS == (
            "응급",
            "알레르기",
            "만성",
            "카드",
            "시터",
            "조부모",
            "할머니",
            "할아버지",
        )

    async def test_서식은_모델을_부르지_않는다(self):
        llm = FakeLLM()

        result = await run(handoff(NOTICE_TEXT), world(antibiotic()), client=llm)

        assert llm.seen == [] and result.model_calls == 0


class TestEmergencyCard:
    PEANUT = HealthSafetyEntry("allergy", "땅콩", "active")
    ASTHMA = HealthSafetyEntry("chronic_disease", "천식", "active")

    async def test_응급_카드에_연락처가_없다(self):
        # T19
        safety = InMemoryHealthSafety([self.PEANUT, self.ASTHMA])
        result = await run(handoff(CARD_TEXT), world(antibiotic(), safety=safety), client=FakeLLM())

        body = "\n".join(lines(result))
        for phrase in ("연락처", "전화", "휴대폰", "010", "보호자 번호"):
            assert phrase not in body

    async def test_카드_전체가_고정_문구다(self):
        safety = InMemoryHealthSafety([self.PEANUT, self.ASTHMA])
        result = await run(handoff(CARD_TEXT), world(antibiotic(), safety=safety), client=FakeLLM())

        [readout] = result.readouts
        assert readout.kind == "emergency_card" and readout.authored_by == "code"
        assert lines(result) == [
            "[응급 카드]  2026-09-28",
            "• 아이 : 별이 (2022-09-28생, 만 4세)",
            "• 알레르기 : 땅콩",
            "• 만성질환 : 천식",
            "• 복용 중인 약 : 항생제 (5ml)",
        ]

    async def test_알레르기가_여럿이면_등록한_이름_그대로_이어_붙인다(self):
        safety = InMemoryHealthSafety(
            [self.PEANUT, HealthSafetyEntry("allergy", "새우(갑각류)", "active")]
        )
        result = await run(handoff(CARD_TEXT), world(safety=safety), client=FakeLLM())

        assert "• 알레르기 : 땅콩, 새우(갑각류)" in lines(result)

    async def test_등록한_것이_없으면_등록된_내용이_없다고_적는다(self):
        result = await run(handoff(CARD_TEXT), world(), client=FakeLLM())

        card = lines(result)
        empty = READOUTS.render("card.empty").body
        assert f"• 알레르기 : {empty}" in card
        assert f"• 만성질환 : {empty}" in card
        assert f"• 복용 중인 약 : {empty}" in card

    async def test_안전_조회_실패면_비우고_적는다(self):
        # C48-safetyfail — "없음" 으로 쓰지 않는다
        safety = InMemoryHealthSafety([self.PEANUT], fail=True)
        result = await run(handoff(CARD_TEXT), world(antibiotic(), safety=safety), client=FakeLLM())

        card = lines(result)
        assert READOUTS.render("card.safety_unavailable").body in card
        assert not any(line.startswith(("• 알레르기 :", "• 만성질환 :")) for line in card)
        for phrase in ("없음", "없어요", "땅콩"):
            assert phrase not in "\n".join(card)
        # 나머지 칸은 그대로 나간다
        assert "• 복용 중인 약 : 항생제 (5ml)" in card

    async def test_알레르기_반응과_대처를_이름_뒤에(self):
        peanut = HealthSafetyEntry(
            "allergy",
            "땅콩",
            "active",
            reactions=("두드러기", "입술 부음"),
            management="에피펜 놓고 119",
        )
        result = await run(
            handoff(CARD_TEXT), world(safety=InMemoryHealthSafety([peanut])), client=FakeLLM()
        )

        assert "• 알레르기 : 땅콩 (반응: 두드러기 · 입술 부음 / 대처: 에피펜 놓고 119)" in lines(
            result
        )

    @pytest.mark.parametrize(
        ("entry", "line"),
        [
            (
                HealthSafetyEntry("allergy", "땅콩", "active", reactions=("두드러기",)),
                "• 알레르기 : 땅콩 (반응: 두드러기)",
            ),
            (
                HealthSafetyEntry(
                    "chronic_disease", "천식", "active", management="흡입기 하루 두 번"
                ),
                "• 만성질환 : 천식 (대처: 흡입기 하루 두 번)",
            ),
        ],
    )
    async def test_반응이나_대처_하나만_있으면_그것만(self, entry, line):
        result = await run(
            handoff(CARD_TEXT), world(safety=InMemoryHealthSafety([entry])), client=FakeLLM()
        )

        assert line in lines(result)

    @pytest.mark.parametrize(
        "entry",
        [
            HealthSafetyEntry("allergy", "땅콩", "active", management="  "),
            HealthSafetyEntry("allergy", "땅콩", "active", reactions=("", " "), management=None),
        ],
    )
    async def test_비었거나_공백뿐인_반응_대처는_이름만(self, entry):
        result = await run(
            handoff(CARD_TEXT), world(safety=InMemoryHealthSafety([entry])), client=FakeLLM()
        )

        assert "• 알레르기 : 땅콩" in lines(result)

    async def test_대처_원문의_줄바꿈은_한_줄로(self):
        # 카드는 줄바꿈으로 줄을 가른다 — 원문의 줄바꿈이 한 칸을 두 줄로 깨면 안 된다
        plain = HealthSafetyEntry("chronic_disease", "당뇨", "active")
        broken = HealthSafetyEntry(
            "chronic_disease",
            "당뇨",
            "active",
            management="식전에\n인슐린\r\n맞기\n\n하루 두 번",
        )
        base = await run(
            handoff(CARD_TEXT), world(safety=InMemoryHealthSafety([plain])), client=FakeLLM()
        )
        result = await run(
            handoff(CARD_TEXT), world(safety=InMemoryHealthSafety([broken])), client=FakeLLM()
        )

        # 줄바꿈 하나가 공백 하나다 — 빈 줄이 끼어도 공백이 여러 칸 남지 않는다
        assert "• 만성질환 : 당뇨 (대처: 식전에 인슐린 맞기 하루 두 번)" in lines(result)
        assert len(lines(result)) == len(lines(base))

    async def test_알레르기_말고_다른_종류는_섞이지_않는다(self):
        safety = InMemoryHealthSafety(
            [
                self.PEANUT,
                self.ASTHMA,
                HealthSafetyEntry("environmental", "꽃가루", "active"),
                HealthSafetyEntry("behavioral", "낯가림", "active"),
            ]
        )
        result = await run(handoff(CARD_TEXT), world(safety=safety), client=FakeLLM())

        body = "\n".join(lines(result))
        assert "꽃가루" not in body and "낯가림" not in body

    async def test_철회된_항목은_싣지_않는다(self):
        safety = InMemoryHealthSafety([HealthSafetyEntry("allergy", "계란", "retracted")])
        result = await run(handoff(CARD_TEXT), world(safety=safety), client=FakeLLM())

        assert "계란" not in "\n".join(lines(result))

    async def test_카드는_모델을_부르지_않는다(self):
        llm = FakeLLM()

        result = await run(handoff(CARD_TEXT), world(antibiotic()), client=llm)

        assert llm.seen == [] and result.model_calls == 0

    async def test_카드에는_위험_판단이_없다(self):
        safety = InMemoryHealthSafety([self.PEANUT])
        result = await run(handoff(CARD_TEXT), world(antibiotic(), safety=safety), client=FakeLLM())

        body = "\n".join(lines(result))
        for phrase in ("위험", "주의", "조심", "심각", "먹이지 마"):
            assert phrase not in body


class TestSafetyRead:
    async def test_투약의뢰서는_안전_정보를_쓰지_않아도_조회_실패가_서식을_막지_않는다(self):
        safety = InMemoryHealthSafety(fail=True)

        result = await run(
            handoff(NOTICE_TEXT), world(antibiotic(), safety=safety), client=FakeLLM()
        )

        assert result.status == "completed"
        assert lines(result)[-1] == READOUTS.render("notice.signature").body
