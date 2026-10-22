"""검진 맥락 요약 `summarize_checkup_context` — `schedule_check` 의 context 분기 (모델 0회).

- 들고 갈 기록만 나열한다: 최근 3일 식사 · 진행 중 복약 · 최근 30일 증상 에피소드 · 알레르기 ·
  만성질환. 판정 · 비교 · 해석이 없다
- 발달 문항 · 문진 답 · 수면 시간 · 키 · 몸무게는 읽지도 내지도 않는다
- 끝에 늘 `checkup.context_footer`. 대필 요청이면 `checkup.no_ghostwrite` 가 맨 앞이다
- 안전 정보 조회 실패는 "없음" 이 아니다 — `safety.unavailable` 경고가 앞에 붙고 안전 줄은 없다
"""

import re
from dataclasses import replace
from datetime import date, timedelta
from itertools import count
from uuid import UUID

import pytest

from app.agents.health.agent import run
from app.agents.health.context import build_gate
from app.agents.health.readouts import READOUTS
from app.agents.health.review import FORBIDDEN_INTERPRETATION, FORBIDDEN_SUFFIX
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryHealthSafety,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot, HealthSafetyEntry, MealRecord
from app.agents.health.tools.checkup_context import (
    FLAGGED,
    MEASUREMENT_WORDS,
    summarize_checkup_context,
)
from tests.unit.agents.health.support import (
    FakeLLM,
    birth_for,
    context,
    observation,
    task,
)

TODAY = date(2026, 9, 28)
DAY = timedelta(days=1)
PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
PEANUT = HealthSafetyEntry("allergy", "땅콩", "active")
ASTHMA = HealthSafetyEntry("chronic_disease", "천식", "active")
_ids = count(4000)

FOOTER = READOUTS.render("checkup.context_footer")
NO_GHOSTWRITE = READOUTS.render("checkup.no_ghostwrite")
UNAVAILABLE = READOUTS.render("safety.unavailable")


def meal(day: date, subject: str, amount: str | None = None) -> MealRecord:
    return MealRecord(UUID(int=next(_ids)), day, subject, amount)


async def _ctx(*, entries=(), safety_fails=False, observations=(), meals=(), courses=()):
    seeds = [course_with_doses(*args, **kwargs) for args, kwargs in courses]
    ctx = context(
        birth_for(48),
        safety=InMemoryHealthSafety(entries, fail=safety_fails),
        memory=InMemoryHealthMemory(observations, meals),
        medication=InMemoryMedicationStore(
            [c for c, _ in seeds], [d for _, rows in seeds for d in rows]
        ),
    )
    ctx.state.gate = await build_gate(ctx, HealthTaskType.SCHEDULE_CHECK)
    return ctx


def _bodies(readouts):
    return [readout.body for readout in readouts]


async def _full():
    return await _ctx(
        entries=(PEANUT, ASTHMA),
        observations=(
            observation(date(2026, 9, 18), to=date(2026, 9, 20), symptoms=("열이 나요",)),
            observation(date(2026, 9, 25), symptoms=("기침",)),
        ),
        meals=(
            meal(TODAY, "죽", "반 그릇"),
            meal(TODAY, "바나나"),
            meal(TODAY - 2 * DAY, "미음"),
            meal(TODAY - 3 * DAY, "밥"),  # 3일 밖
        ),
        courses=(
            (
                ("항생제", PATTERN, TODAY - DAY, TODAY + DAY),
                {"dosage": "5ml"},
            ),
        ),
    )


async def test_기록을_항목별로_나열한다():
    ctx = await _full()

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    bodies = _bodies(readouts)
    assert bodies[0] == "• 최근 식사 : 9/26 미음 / 9/28 죽 반 그릇, 바나나"
    assert bodies[1] == "• 먹고 있는 약 : 항생제 5ml"
    assert bodies[2] == "• 최근 아팠던 기록 : 9/18–9/20 발열, 9/25 기침"
    assert bodies[3] == "• 알레르기 · 만성질환 : 땅콩, 천식"
    assert readouts[-1] == FOOTER
    assert len(readouts) == 5
    assert all(r.kind == "checkup_context" and r.authored_by == "code" for r in readouts)


async def test_발달_문항과_측정값이_없다():
    ctx = await _full()

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    # 고정 문구(footer)는 "문진표" 를 말한다 — 기록 줄만 본다
    text = " ".join(r.body for r in readouts if r != FOOTER)
    for word in ("발달", "문진", "수면", "키", "몸무게", "cm", "kg", "기준", "또래", "백분위"):
        assert word not in text, word


async def test_키_몸무게는_읽지_않는다():
    ctx = await _ctx(entries=(PEANUT,))

    class Boom:
        async def basic(self, *, child_id):
            raise AssertionError("검진 맥락은 아이 신체 정보를 읽지 않는다")

    ctx = replace(ctx, ports=replace(ctx.ports, child=Boom()))

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    assert readouts[-1] == FOOTER


async def test_끝에_늘_고정_문구():
    for ctx in (
        await _full(),
        await _ctx(),
        await _ctx(safety_fails=True),
    ):
        readouts = await summarize_checkup_context(ctx, ghostwrite=False)

        assert readouts[-1] == FOOTER
        assert [r for r in readouts if r == FOOTER] == [FOOTER]


async def test_대필은_거절하고_요약만():
    ctx = await _full()

    readouts = await summarize_checkup_context(ctx, ghostwrite=True)

    assert readouts[0] == NO_GHOSTWRITE
    assert NO_GHOSTWRITE not in readouts[1:]
    rest = await summarize_checkup_context(ctx, ghostwrite=False)
    assert readouts[1:] == rest  # 같은 요약이 뒤따른다


@pytest.mark.parametrize(
    "text, ghostwrite",
    [
        ("검진 문진표 대신 작성해줘.", True),
        ("문진표 작성해 줘", True),
        ("문진표 좀 써줘", True),
        ("검진 문진표 대신 써 줘", True),
        ("다음 검진 때 뭐 챙겨가면 돼?", False),
        ("검진 때 가져갈 것 알려줘", False),
        ("문진표에 뭐 물어봐?", False),
    ],
)
async def test_T35_문진표_대필_요청은_label_이_가려낸다(text, ghostwrite):
    ctx = context(birth_for(48))
    llm = FakeLLM()

    result = await run(task("schedule_check", text), ctx, client=llm)

    assert result.status == "completed"
    assert result.model_calls == 0 and llm.seen == []
    assert result.event_requests == () and result.medication_drafts == ()
    assert (result.readouts[0] == NO_GHOSTWRITE) is ghostwrite
    assert result.readouts[-1] == FOOTER
    assert not any(r.kind == "checkup_schedule" for r in result.readouts)  # 차수 안내로 새지 않는다


async def test_모델을_부르지_않는다():
    ctx = context(birth_for(48))
    llm = FakeLLM()

    result = await run(task("schedule_check", "다음 검진 때 뭐 챙겨가면 돼?"), ctx, client=llm)

    assert llm.seen == []
    assert result.model_calls == 0


async def test_안전_조회_실패는_없음이_아니라_경고():
    ctx = await _ctx(entries=(PEANUT,), safety_fails=True, meals=(meal(TODAY, "죽"),))

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    bodies = _bodies(readouts)
    assert bodies[0] == UNAVAILABLE.body  # 아래 줄이 그것 없이 만든 거라서 맨 위다
    assert not any(b.startswith("• 알레르기") for b in bodies)
    # 복약은 등록 기반이라 "없음" 이 사실이다
    assert bodies.count("• 먹고 있는 약 : 등록된 내용이 없어요") == 1
    assert not any("알레르기 · 만성질환" in b for b in bodies)
    assert "땅콩" not in " ".join(bodies)


async def test_경고는_대필_거절_다음이다():
    ctx = await _ctx(safety_fails=True)

    readouts = await summarize_checkup_context(ctx, ghostwrite=True)

    assert readouts[0] == NO_GHOSTWRITE
    assert readouts[1] == UNAVAILABLE


async def test_게이트가_비어_있으면_못_읽은_것으로_본다():
    ctx = await _ctx(entries=(PEANUT,))
    ctx.state.gate = None

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    assert readouts[0] == UNAVAILABLE
    assert not any(r.body.startswith("• 알레르기") for r in readouts)


async def test_기록이_없으면_있는_사실만_낸다():
    ctx = await _ctx()

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    bodies = _bodies(readouts)
    assert bodies == [
        "• 먹고 있는 약 : 등록된 내용이 없어요",
        "• 알레르기 · 만성질환 : 등록된 내용이 없어요",
        FOOTER.body,
    ]


async def test_식사는_최근_3일만_영양_숫자_없이():
    ctx = await _ctx(
        meals=(
            meal(TODAY - 2 * DAY, "미음", "한 숟갈"),
            meal(TODAY - 3 * DAY, "국수"),
            meal(TODAY, "사과"),
        )
    )

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    assert readouts[0].body == "• 최근 식사 : 9/26 미음 한 숟갈 / 9/28 사과"
    assert "국수" not in " ".join(_bodies(readouts))


async def test_복약은_진행_중인_것만_원문_그대로():
    ctx = await _ctx(
        courses=(
            (("항생제", PATTERN, TODAY - DAY, TODAY + DAY), {"dosage": "5ml"}),
            (("끝난 약", PATTERN, TODAY - 5 * DAY, TODAY - 3 * DAY), {}),
            (("멈춘 약", PATTERN, TODAY - DAY, TODAY + DAY), {"status": "stopped"}),
            (("시럽", PATTERN, TODAY, TODAY), {}),
        )
    )

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    meds = next(r.body for r in readouts if r.body.startswith("• 먹고 있는 약"))
    assert meds == "• 먹고 있는 약 : 항생제 5ml, 시럽"


async def test_증상은_최근_30일_에피소드로():
    ctx = await _ctx(
        observations=(
            observation(date(2026, 8, 20), symptoms=("기침",)),  # 30일 밖
            observation(date(2026, 8, 30), symptoms=("콧물",)),
            observation(date(2026, 9, 27), symptoms=("열나",), at=None),
            observation(date(2026, 9, 28), symptoms=("열나",)),
        )
    )

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    illness = next(r.body for r in readouts if r.body.startswith("• 최근 아팠던 기록"))
    assert illness == "• 최근 아팠던 기록 : 8/30 콧물, 9/27–9/28 발열"
    assert "기침" not in illness


async def test_사전에_없는_증상도_원문으로_낸다():
    ctx = await _ctx(observations=(observation(date(2026, 9, 26), symptoms=("귀를 자꾸 만져요",)),))

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    illness = next(r.body for r in readouts if r.body.startswith("• 최근 아팠던 기록"))
    assert illness == "• 최근 아팠던 기록 : 9/26 귀를 자꾸 만져요"


async def test_먹인_약_행은_증상_줄에_들어가지_않는다():
    given = observation(date(2026, 9, 27), symptoms=())
    given = replace(given, medication_given="해열제")
    ctx = await _ctx(observations=(given,))

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    assert not any(r.body.startswith("• 최근 아팠던 기록") for r in readouts)
    assert "해열제" not in " ".join(_bodies(readouts))


async def test_줄마다_근거_행을_단다():
    ctx = await _full()

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    tables = [{ref.kind for ref in r.source_refs} for r in readouts]
    assert tables[0] == {"observation_food"}
    assert tables[1] == {"medication_schedule"}
    assert tables[2] == {"observation_health"}
    assert tables[-1] == set()
    assert all(not r.source_refs for r in readouts if r.body.startswith("• 알레르기"))


PLANTED_SYMPTOMS = ("장염 같아요", "폐렴 의심", "백분위 낮음", "또래보다 작음", "5kg 빠짐")
PLANTED_MEALS = ("키 크는 약 5cm", "체중 5kg 늘리는 죽")


async def _planted():
    return await _ctx(
        observations=tuple(observation(TODAY - DAY, symptoms=(text,)) for text in PLANTED_SYMPTOMS)
        + (observation(TODAY, symptoms=("기침",)),),
        meals=tuple(meal(TODAY, text) for text in PLANTED_MEALS) + (meal(TODAY, "키위", "반 개"),),
    )


async def test_해석_표현과_측정값은_원문을_내지_않고_자리표시로():
    readouts = await summarize_checkup_context(await _planted(), ghostwrite=False)

    text = " ".join(r.body for r in readouts)
    for planted in (*PLANTED_SYMPTOMS, *PLANTED_MEALS):
        assert planted not in text, planted
    for word in ("장염", "폐렴", "의심", "백분위", "또래", "5kg", "5cm", "키 크는", "체중"):
        assert word not in text, word
    meals = next(r.body for r in readouts if r.body.startswith("• 최근 식사"))
    illness = next(r.body for r in readouts if r.body.startswith("• 최근 아팠던 기록"))
    assert meals.count(FLAGGED) == len(PLANTED_MEALS)
    assert illness.count(FLAGGED) == len(PLANTED_SYMPTOMS)
    assert meals == (f"• 최근 식사 : 9/28 {FLAGGED}, {FLAGGED}, 키위 반 개")
    assert illness == (
        "• 최근 아팠던 기록 : "
        + ", ".join([f"9/27 {FLAGGED}"] * len(PLANTED_SYMPTOMS))
        + ", 9/28 기침"
    )


async def test_걸러진_자리에도_금지어가_없다():
    readouts = await summarize_checkup_context(await _planted(), ghostwrite=False)

    data = " ".join(r.body for r in readouts if r.body.startswith("•"))
    for word in (*FORBIDDEN_INTERPRETATION, *MEASUREMENT_WORDS):
        assert word not in data, word
    assert not FORBIDDEN_SUFFIX.search(data)
    assert not re.search(r"\d\s*(?:cm|kg)", data, re.I)


@pytest.mark.parametrize("subject", ["키위", "키즈 카페 간식", "키조개 죽"])
async def test_키가_들어간_평범한_말은_그대로(subject):
    ctx = await _ctx(meals=(meal(TODAY, subject, "반 개"),))

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    assert readouts[0].body == f"• 최근 식사 : 9/28 {subject} 반 개"


@pytest.mark.parametrize(
    "text",
    [
        "키가 작아요",
        "키 5키로 늘림",
        "몸무게 늘었어요",
        "성장곡선 아래",
        "체중 늘었어요",
        "체중 증가",
    ],
)
async def test_키_몸무게_표현은_걸린다(text):
    ctx = await _ctx(meals=(meal(TODAY, text),))

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    assert readouts[0].body == f"• 최근 식사 : 9/28 {FLAGGED}"


@pytest.mark.parametrize(
    "text",
    ["장염인가요", "결막염임", "감기 기운", "독감인 듯", "수족구로 보임", "이가 나서 그런 것 같음"],
)
async def test_조사_밖_꼬리_병_이름_추측_표현도_걸린다(text):
    ctx = await _ctx(observations=(observation(TODAY, symptoms=(text,)),))

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    illness = next(r.body for r in readouts if r.body.startswith("• 최근 아팠던 기록"))
    assert illness == f"• 최근 아팠던 기록 : 9/28 {FLAGGED}"


@pytest.mark.parametrize("subject", ["따뜻한 보리차", "소염제 연고", "확인해 둔 이유식"])
async def test_평범한_말은_그대로(subject):
    ctx = await _ctx(meals=(meal(TODAY, subject),))

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    assert readouts[0].body == f"• 최근 식사 : 9/28 {subject}"


async def test_자리표시_문구에는_금지어가_없다():
    assert not FORBIDDEN_SUFFIX.search(FLAGGED)
    assert not any(word in FLAGGED for word in (*FORBIDDEN_INTERPRETATION, *MEASUREMENT_WORDS))


async def test_섞인_줄은_안전한_말만_남기고_자리표시를_더한다():
    ctx = await _ctx(
        observations=(observation(TODAY, symptoms=("귀를 자꾸 만져요", "중이염 의심")),)
    )

    readouts = await summarize_checkup_context(ctx, ghostwrite=False)

    illness = next(r.body for r in readouts if r.body.startswith("• 최근 아팠던 기록"))
    assert illness == f"• 최근 아팠던 기록 : 9/28 귀를 자꾸 만져요 / {FLAGGED}"
