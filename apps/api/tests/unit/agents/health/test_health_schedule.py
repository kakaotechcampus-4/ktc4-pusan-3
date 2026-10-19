"""schedule_check 라벨 — 모델 0회로 검진 차수 · 권장 접종을 안내하고 알림 초안을 낸다."""

from datetime import date, datetime, time, timedelta

import pytest

from app.agents.health.agent import run
from app.agents.health.labels.schedule import (
    CONTEXT_WORDS,
    schedule_intent,
)
from app.agents.health.readouts import READOUTS
from tests.unit.agents.health.support import KST, NOW, TODAY, birth_for, context, task

FORBIDDEN = ("놓쳤", "빠뜨렸", "늦었")


class FakeLLM:
    def __init__(self) -> None:
        self.seen: list[object] = []

    async def chat(self, messages, **kwargs):
        self.seen.append(messages)
        raise AssertionError("schedule_check 는 모델을 부르지 않는다")


async def _run(months=48, text="영유아 검진 언제 받아야 해?", *, birth=None, llm=None):
    ctx = context(birth or birth_for(months))
    return await run(task("schedule_check", text), ctx, client=llm or FakeLLM())


def _bodies(result):
    return [readout.body for readout in result.readouts]


async def test_48개월은_6차와_다음_7차():
    llm = FakeLLM()

    result = await _run(48, llm=llm)

    assert result.status == "completed"
    assert result.task_type == "schedule_check"
    assert result.model_calls == 0 and llm.seen == []
    assert result.suggestions == () and result.medication_drafts == ()
    assert all(r.kind == "checkup_schedule" and r.authored_by == "code" for r in result.readouts)
    bodies = _bodies(result)
    assert bodies[0].startswith("지금은 6차 영유아 건강검진 기간이에요.")
    assert bodies[1].startswith("같은 시기에 구강검진도 받을 수 있어요.")  # 42~53개월 구강 3차
    assert bodies[2].startswith("다음 7차 영유아 건강검진은 ")
    assert bodies[-1] == READOUTS.render("vaccine.disclaimer").body


async def test_날짜는_년_월_일로_적는다():
    # 48개월(오늘 2026-09-28, 생일 2022-09-28): 6차 = 2026-03-28 ~ 2026-10-27, 7차 시작 = 2027-03-28
    result = await _run(48)

    assert "(2026년 3월 28일 ~ 2026년 10월 27일)" in _bodies(result)[0]
    assert "2027년 3월 28일부터예요" in _bodies(result)[2]


async def test_구강검진_기간이면_현재_차수_다음에_붙는다():
    result = await _run(30)

    bodies = _bodies(result)
    assert bodies[0].startswith("지금은 5차 ")
    assert bodies[1].startswith("같은 시기에 구강검진도 받을 수 있어요.")
    assert bodies[2].startswith("다음 6차 ")


async def test_접종_안내에는_늘_고지가_붙는다():
    for months in (0, 2, 12, 24, 36, 48, 71):
        result = await _run(months)

        assert _bodies(result)[-1] == READOUTS.render("vaccine.disclaimer").body, months


async def test_접종이_있으면_이름과_차수를_이어서_안내한다():
    result = await _run(2)

    body = next(b for b in _bodies(result) if b.startswith("이 시기에 권장되는 접종:"))
    assert body == (
        "이 시기에 권장되는 접종: DTaP 1차 · IPV 1차 · Hib 1차 · 폐렴구균 1차 · 로타바이러스 1차"
    )


async def test_접종이_없는_시기는_없다고_말한다():
    result = await _run(36)

    assert READOUTS.render("vaccine.none_now").body in _bodies(result)
    assert not any(b.startswith("이 시기에 권장되는 접종:") for b in _bodies(result))
    assert _bodies(result)[-1] == READOUTS.render("vaccine.disclaimer").body


async def test_72개월은_검진_종료를_알리고_이어서_접종을_소개한다():
    result = await _run(72)

    bodies = _bodies(result)
    assert result.status == "completed"
    assert result.event_requests == ()
    assert result.readouts[0] == READOUTS.render("checkup.aged_out")
    assert bodies[1] == (
        "이 시기에 권장되는 접종: DTaP 5차 · IPV 4차 · MMR 2차 · 일본뇌염(불활성화) 4차"
    )
    assert bodies[-1] == READOUTS.render("vaccine.disclaimer").body
    # 차수 안내는 없다 — 검진은 끝났다
    assert not any("차 영유아 건강검진" in b or b.startswith("같은 시기에") for b in bodies)


def test_검진_종료_문구는_예방접종이_끝난다고_하지_않는다():
    template = READOUTS.get("checkup.aged_out").template

    assert "국가예방접종은 만 6세까지" not in template
    assert "영유아 건강검진" in template


async def test_만_6세_이후에도_접종_안내는_이어진다():
    for months, expected in ((83, "DTaP 5차"), (132, "Tdap 추가"), (144, "HPV 1차")):
        result = await _run(months)

        assert result.readouts[0] == READOUTS.render("checkup.aged_out"), months
        assert any(expected in b for b in _bodies(result)), months


async def test_접종이_없는_큰_월령은_없다고_하고_검진_종료를_먼저_알린다():
    result = await _run(100)

    assert _bodies(result) == [
        READOUTS.render("checkup.aged_out").body,
        READOUTS.render("vaccine.none_now").body,
        "다음 접종은 2029년 5월 28일부터예요: Tdap 추가",  # 132개월 = 만 11세 생일
        READOUTS.render("vaccine.disclaimer").body,
    ]


async def test_알림을_원하면_다음_차수_시작일로_초안():
    result = await _run(48, "다음 검진 알림 받고 싶어.")

    assert len(result.event_requests) == 1
    event = result.event_requests[0]
    assert event.title == "영유아 건강검진 7차"
    assert event.category == "health" and event.all_day is True
    assert event.starts_at == datetime.combine(date(2027, 3, 28), time(9, 0), KST)
    assert event.starts_at.tzinfo is not None
    # 안내는 그대로 같이 나간다
    assert _bodies(result)[0].startswith("지금은 6차 ")


async def test_일정_안내만_물으면_초안이_없다():
    for text in (
        "영유아 검진 언제 받아야 해?",
        "예방접종 일정 알려줘",
        "이번 달에 맞아야 하는 주사 있어?",
    ):
        result = await _run(48, text)

        assert result.event_requests == (), text


async def test_대상_종료면_알림_초안이_없다():
    result = await _run(72, "다음 검진 알림 받고 싶어.")

    assert result.event_requests == ()
    assert result.readouts[0] == READOUTS.render("checkup.aged_out")


async def test_다음_차수가_없는_71개월은_알림_초안이_없다():
    result = await _run(71, "다음 검진 알림 받고 싶어.")

    assert result.event_requests == ()  # 8차가 마지막이고 그 뒤는 대상 종료다
    assert _bodies(result)[0].startswith("지금은 8차 ")


@pytest.mark.parametrize(
    "text",
    [
        "다음 검진 때 뭐 챙겨가면 돼?",
        "검진 문진표 대신 작성해줘",
        "검진 때 가져갈 것 알려줘",
        "검진 때 뭐 챙길까?",
    ],
)
async def test_검진_맥락은_아직_unsupported(text):
    llm = FakeLLM()

    result = await _run(48, text, llm=llm)

    assert result.status == "unsupported"
    assert result.task_type == "schedule_check"
    assert result.readouts == () and result.event_requests == ()
    assert result.model_calls == 0 and llm.seen == []


async def test_놓쳤다는_말을_하지_않는다():
    for months in range(0, 160):  # 검진이 끝난 뒤(72+) 접종 안내도 같은 말투다
        for text in ("예방접종 놓친 거 있어?", "다음 검진 알림 받고 싶어."):
            result = await _run(months, text)

            joined = " ".join(_bodies(result))
            assert not any(word in joined for word in FORBIDDEN), (months, joined)


async def test_생후_2일에_가까운_검진을_물으면_1차를_답한다():
    result = await _run(birth=TODAY - timedelta(days=2), text="영유아 검진 언제 받아야 해?")

    bodies = _bodies(result)
    assert bodies[0] == "다음 1차 영유아 건강검진은 2026년 10월 10일부터예요."
    assert not any(b.startswith("지금은 ") for b in bodies)


async def test_1차_기간이_지난_영아에게_지났다고_하지_않는다():
    result = await _run(2)  # 생후 2개월 = 1차(14~35일) 가 끝난 뒤

    bodies = _bodies(result)
    assert bodies[0].startswith("다음 2차 영유아 건강검진은 ")
    assert not any("지났" in b or "끝났" in b for b in bodies)


async def test_생일_당일_KST_자정_직후에_차수가_바뀐다():
    # NOW = UTC 9/27 16:00 = KST 9/28 01:00. 18개월 생일이 KST 로 오늘이다
    ctx = context(birth_for(18, TODAY))
    assert ctx.now == NOW

    result = await run(task("schedule_check"), ctx, client=FakeLLM())

    assert _bodies(result)[0].startswith("지금은 4차 ")


async def test_계산_결과를_저장하지_않는다():
    ctx = context(birth_for(48))

    await run(task("schedule_check", "다음 검진 알림 받고 싶어."), ctx, client=FakeLLM())

    assert ctx.state.safety_entries == ()  # 안전 행 말고 쌓는 것이 없다
    assert ctx.state.gate is not None


def test_의도는_맥락이_알림보다_먼저다():
    assert schedule_intent(["다음 검진 알림 받고 싶어. 뭐 챙겨가면 돼?"]) == "context"
    assert schedule_intent(["다음 검진 알림 받고 싶어."]) == "alarm"
    assert schedule_intent(["검진 언제 받아야 해?"]) == "schedule"
    assert schedule_intent(["검진 때가 되면 알려줘"]) == "alarm"
    assert schedule_intent(["예방접종 일정 알려줘"]) == "schedule"  # 그냥 "알려줘" 는 안내 요청이다


def test_의도는_문장_하나씩_본다():
    assert schedule_intent(["검진 언제야?", "알림 받을래"]) == "alarm"


def test_맥락_표현_목록():
    # 가져갈 것을 묻는 표현만 둔다 — 맨 "챙겨" · "준비" 는 일정 질문("검진 일정 챙겨줘")까지 막는다
    assert set(CONTEXT_WORDS) >= {"챙겨가", "챙길", "준비물", "가져가", "문진"}
    assert "챙겨" not in CONTEXT_WORDS and "준비" not in CONTEXT_WORDS


# ── 최종 리뷰 반영 ──
async def test_일반검진_기간이_아니어도_구강검진_기간이면_구강검진으로_안내한다():
    # 26개월: 4차(18~24)는 끝났고 구강 1차(18~29)는 아직이다. "같은 시기에" 라고 쓰면 5차와
    # 같이 받는 것으로 읽혀 구강검진을 놓친다
    result = await _run(26)

    bodies = _bodies(result)
    assert bodies[0].startswith("지금은 구강검진 1차 기간이에요.")
    assert bodies[1].startswith("다음 5차 ")
    assert not any(b.startswith("같은 시기에") or b.startswith("지금은 1") for b in bodies)


async def test_일반검진_기간_안의_구강검진은_같은_시기라고_한다():
    result = await _run(30)

    bodies = _bodies(result)
    assert bodies[1].startswith("같은 시기에 구강검진도")
    assert not any("지금은 구강검진" in b for b in bodies)


def test_접종이_없다는_말은_절기_접종을_빼놓지_않는다():
    # 인플루엔자는 월령이 아니라 절기로 정해져 이 표에 없다 — "권장 접종이 없다" 고 말하면 틀린다
    template = READOUTS.get("vaccine.none_now").template

    assert "인플루엔자" in template
    assert "정해진 권장 접종은 없어요" not in template


@pytest.mark.parametrize(
    "text",
    [
        "검진 일정 챙겨줘",
        "검진 챙겨야 하는 시기 언제야?",
        "놓치지 않게 챙겨줘 검진",
        "검진 예약 준비하려는데 언제부터 돼?",
        "검진 준비 언제부터 하면 돼?",
    ],
)
def test_챙겨_준비가_들어가도_일정_질문이면_안내한다(text):
    assert schedule_intent([text]) == "schedule"


@pytest.mark.parametrize(
    "text",
    [
        "다음 검진 때 뭐 챙겨가면 돼?",
        "검진 갈 때 준비물 알려줘",
        "검진 때 뭐 준비할까?",
        "검진 때 가져갈 것 있어?",
        "검진 문진표 대신 작성해줘",
    ],
)
def test_가져갈_것을_묻는_말은_맥락이다(text):
    assert schedule_intent([text]) == "context"


@pytest.mark.parametrize(
    "text",
    [
        "어린이집 알림장에 검진 받으라는데 언제야?",
        "검진 알림 꺼줘",
        "검진 알림 그만 받고 싶어",
        "검진 알림이 안 와",
        "예방접종도우미에서 알림 왔는데 언제 맞아야 해?",
        "접종 맞을 때 되면 알려줘",
        "다음 접종 미리 알려줘",
        "검진 기간 끝나기 전에 알려줘",
    ],
)
def test_알림_초안을_원하지_않는_말은_초안을_만들지_않는다(text):
    assert schedule_intent([text]) != "alarm"


@pytest.mark.parametrize(
    "text",
    [
        "다음 검진 알림 받고 싶어.",
        "검진 알림 설정해줘",
        "검진 때가 되면 알려줘",
        "알림 받을래",
    ],
)
def test_알림을_원하는_말은_초안을_만든다(text):
    assert schedule_intent([text]) == "alarm"


async def test_알림장_이야기에는_일정_초안이_없다():
    result = await _run(48, "어린이집 알림장에 검진 받으라는데 언제야?")

    assert result.event_requests == ()
    assert _bodies(result)[0].startswith("지금은 6차 ")


# ── 다음 접종 시기 ──
async def test_접종이_없는_시기에는_다음_접종_시기를_알려준다():
    # 36개월: 정해진 접종은 없고 만 4세(48개월)에 다음 칸이 열린다
    result = await _run(36)

    bodies = _bodies(result)
    assert READOUTS.render("vaccine.none_now").body in bodies
    assert "다음 접종은 2027년 9월 28일부터예요: DTaP 5차 · IPV 4차 · MMR 2차" in bodies
    assert bodies[-1] == READOUTS.render("vaccine.disclaimer").body  # 고지가 맨 끝이다


async def test_접종이_있는_시기에도_다음_접종_시기가_붙는다():
    result = await _run(2)  # 지금은 기초 1차들, 다음은 4개월 2차들

    bodies = _bodies(result)
    due = next(i for i, b in enumerate(bodies) if b.startswith("이 시기에 권장되는 접종:"))
    upcoming = next(i for i, b in enumerate(bodies) if b.startswith("다음 접종은 "))
    assert due < upcoming < len(bodies) - 1
    assert "DTaP 2차" in bodies[upcoming]


async def test_표_끝_뒤에는_다음_접종_줄이_없다():
    for months in (144, 155, 156, 200):
        result = await _run(months)

        assert not any(b.startswith("다음 접종은 ") for b in _bodies(result)), months


async def test_다음_접종_줄은_검진_차수_줄과_섞이지_않는다():
    result = await _run(48)

    bodies = _bodies(result)
    assert bodies[2].startswith("다음 7차 영유아 건강검진은 ")
    assert (
        sum(b.startswith("다음 ") for b in bodies) == 2
    )  # 검진 다음 차수 하나, 접종 다음 시기 하나


def test_다음_접종_문구는_차수_표시를_하지_않는다():
    template = READOUTS.get("vaccine.next").template

    assert template == "다음 접종은 {starts_on}부터예요: {vaccines}"
