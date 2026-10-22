"""`visit_summary` 라벨 실행기 — 열 질문 · 기록 없음은 모델 0회, 정리는 모델 한 번.

- 순서: 3개월 미만 발열이면 `emergency` 가 맨 앞 → 열 표현이면 열 타임라인(0회) → 최근 30일 건강
  기록도 복약 코스도 0건이면 `visit.no_record`(0회) → 그 밖에는 tool loop(`model_calls=1`)
- 🚨 정리 loop 에서 모델은 줄의 key 만 고른다 (#551) — 모델에게 가는 글에 기록 원문 · 약 이름 ·
  용량 · 식사 내용이 없다
- 열 표현은 작은 표로 가른다. 모델이 가르지 않는다
- 안전 조회가 실패해도 정리는 진행하고 `safety.unavailable` 경고가 정리 맨 위에 붙는다
"""

import json
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.agents.health import agent, registry
from app.agents.health.agent import run
from app.agents.health.labels.visit import FEVER_WORDS, is_fever_question
from app.agents.health.prompt import build_system_prompt
from app.agents.health.readouts import READOUTS
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryHealthSafety,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot, HealthObservation, HealthSafetyEntry, MealRecord
from tests.unit.agents.health.support import (
    KST,
    TODAY,
    FakeLLM,
    birth_for,
    call,
    calls,
    context,
    gate,
    task,
)

DAY = timedelta(days=1)
PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
PEANUT = HealthSafetyEntry("allergy", "땅콩", "active")

COUGH = HealthObservation(
    id=UUID(int=4001),
    observed_from=TODAY - DAY,
    observed_to=TODAY - DAY,
    observed_time=None,
    symptoms=("기침",),
    severity="조금",
)
# 어제 22:00(KST) 에 잰 체온. NOW 는 9/28 01:00 KST 라 48시간 안이다
READING = HealthObservation(
    id=UUID(int=4002),
    observed_from=TODAY - DAY,
    observed_to=TODAY - DAY,
    observed_time=datetime(2026, 9, 27, 22, 0, tzinfo=KST),
    symptoms=("열나",),
    temperature=Decimal("38.5"),
    measured_at=datetime(2026, 9, 27, 22, 0, tzinfo=KST),
    measure_site="ear",
)
GIVEN_ONLY = HealthObservation(
    id=UUID(int=4003),
    observed_from=TODAY - DAY,
    observed_to=TODAY - DAY,
    observed_time=None,
    symptoms=(),
    medication_given="해열제",
)
MEAL = MealRecord(UUID(int=4900), TODAY - DAY, "미음", "반 그릇")


def visit(text: str = "진료 볼 건데 정리해줘"):
    return task("visit_summary", text)


def world(*observations, meals=(), courses=(), **kwargs):
    seeds = list(courses)
    store = InMemoryMedicationStore([c for c, _ in seeds], [d for _, rows in seeds for d in rows])
    memory = InMemoryHealthMemory(list(observations), list(meals))
    return context(memory=memory, medication=store, **kwargs)


def antibiotic(**kwargs):
    return course_with_doses(
        "항생제", PATTERN, TODAY - 2 * DAY, TODAY + DAY, dosage="5ml", **kwargs
    )


def timeline_of(result):
    [readout] = [r for r in result.readouts if r.kind == "symptom_timeline"]
    return readout


async def test_기록이_없으면_모델_없이_안내():
    # T24-norecord — 빈 타임라인을 만들지 않고 모델도 부르지 않는다
    llm = FakeLLM()

    result = await run(visit(), world(), client=llm)

    assert llm.seen == [] and result.model_calls == 0
    assert result.status == "completed"
    assert result.readouts == (READOUTS.render("visit.no_record"),)


async def test_30일_밖의_기록만_있으면_기록이_없는_것이다():
    old = replace(
        COUGH, id=UUID(int=4010), observed_from=TODAY - 40 * DAY, observed_to=TODAY - 40 * DAY
    )
    llm = FakeLLM()

    result = await run(visit(), world(old), client=llm)

    assert llm.seen == [] and result.model_calls == 0
    assert result.readouts == (READOUTS.render("visit.no_record"),)


async def test_2주보다_오래된_기록도_한_달_안이면_정리로_간다():
    # 보호자가 한 달을 물을 수 있다 — 14일만 보고 "없어요" 로 막지 않는다
    old = replace(
        COUGH, id=UUID(int=4011), observed_from=TODAY - 20 * DAY, observed_to=TODAY - 20 * DAY
    )
    llm = FakeLLM(
        call("search_health_observations", {"period": "30d"}),
        call("compose_symptom_timeline", {"keys": ["s1"]}),
    )

    result = await run(visit("한 달 동안 증상 정리해줘"), world(old), client=llm)

    assert result.model_calls == 1
    assert timeline_of(result).body.startswith("9/8 기침")


async def test_복약_코스만_있어도_기록이_있는_것이다():
    # 증상 기록이 없어도 복약 체크 n/m 은 진료에 들고 갈 기록이다
    ctx = world(courses=[antibiotic(checked={(TODAY - DAY, time(8, 30))})])
    llm = FakeLLM(
        call("search_medication_checks", {}),
        call("compose_symptom_timeline", {"keys": ["c2"]}),
    )

    result = await run(visit(), ctx, client=llm)

    assert result.model_calls == 1
    assert timeline_of(result).body.startswith("9/27 항생제 5ml 체크 1/3회")


async def test_먹인_약_행만_있어도_기록이_있는_것이다():
    # 약 행도 건강 기록이다 — 증상이 없다고 "기록 없음" 으로 막지 않는다
    llm = FakeLLM(
        call("search_health_observations", {"period": "14d"}),
        call(
            "compose_symptom_timeline",
            {"keys": ["g1"]},
        ),
    )

    result = await run(visit(), world(GIVEN_ONLY), client=llm)

    assert result.model_calls == 1
    assert timeline_of(result).body.startswith("9/27 해열제 먹였다고 기록하셨어요")


async def test_열_언제부터는_모델_없이():
    # T22 — 체온 · 체크된 복약 · 등록 일정상 다음 예정을 나열만 한다
    ctx = world(READING, courses=[antibiotic(checked={(TODAY - DAY, time(12, 30))})])
    llm = FakeLLM()

    result = await run(visit("열 언제부터 났는지 알려줘"), ctx, client=llm)

    assert llm.seen == [] and result.model_calls == 0
    assert result.status == "completed"
    assert {r.kind for r in result.readouts} == {"fever_timeline"}
    bodies = [r.body for r in result.readouts]
    assert "9/27 22:00 38.5℃ (귀)" in bodies
    assert any(body.startswith("마지막으로 체크된 복약 항생제") for body in bodies)
    assert any(body.startswith("등록된 일정상 다음 예정") for body in bodies)
    assert all(r.authored_by == "code" for r in result.readouts)


async def test_열_언제부터인데_체온_기록이_없으면_그_안내_한_줄():
    # 증상 기록은 있어도 체온이 없으면 fever.no_record — 기록 없음(visit.no_record)이 아니다
    llm = FakeLLM()

    result = await run(visit("체온 기록 보여줘"), world(COUGH), client=llm)

    assert llm.seen == [] and result.model_calls == 0
    assert result.readouts == (READOUTS.render("fever.no_record"),)


async def test_열_표현이_없으면_체온이_있어도_정리로_간다():
    ctx = world(COUGH, READING)
    llm = FakeLLM(
        call("search_health_observations", {}),
        call(
            "compose_symptom_timeline",
            {"keys": ["s1"]},
        ),
    )

    result = await run(visit("증상 정리해줘"), ctx, client=llm)

    assert result.model_calls == 1
    assert [r.kind for r in result.readouts] == ["symptom_timeline"]


@pytest.mark.parametrize(
    "text", ["열 언제부터야?", "열이 언제부터 났지", "열 언제 났더라", "몇 도였지", "체온 알려줘"]
)
def test_열_표현_표에_걸린다(text):
    assert is_fever_question([text])


@pytest.mark.parametrize("text", ["증상 정리해줘", "진료 준비해줘", "기침 언제부터 했어?"])
def test_열_표현이_아니면_걸리지_않는다(text):
    assert not is_fever_question([text])


def test_열_표현_표는_짧다():
    assert FEVER_WORDS == ("열 언제", "열이 언제", "몇 도", "체온")


async def test_정리는_모델_한_번():
    # T24 — 조회 → compose. 같은 loop 안의 왕복이라 model_calls 는 1 이다
    ctx = world(
        COUGH,
        meals=[MEAL],
        courses=[antibiotic(checked={(TODAY - DAY, time(8, 30))})],
    )
    llm = FakeLLM(
        calls(
            ("search_health_observations", {"period": "14d"}),
            ("search_recent_meals", {}),
            ("search_medication_checks", {}),
        ),
        call(
            "compose_symptom_timeline",
            {"keys": ["s1", "f1", "c2"]},
        ),
    )

    result = await run(visit(), ctx, client=llm)

    assert result.status == "completed"
    assert result.model_calls == 1 and len(llm.seen) == 2
    # 줄의 글은 코드가 기록에서 그린다. 알레르기 · 만성질환 줄도 코드가 붙인다
    assert timeline_of(result).body.split("\n") == [
        "9/27 기침 · 정도: 조금",
        "9/27 항생제 5ml 체크 1/3회",
        "9/27 미음 반 그릇",
        READOUTS.render("card.allergy", items="등록된 내용이 없어요").body,
        READOUTS.render("card.chronic", items="등록된 내용이 없어요").body,
    ]
    assert result.medication_drafts == () and result.event_requests == ()
    # 모델이 본 글(system · 요청 · tool 결과)에 기록 원문 · 약 이름 · 용량 · 식사 내용이 없다
    seen = json.dumps(llm.seen, ensure_ascii=False, default=str)
    for raw in ("기침", "조금", "항생제", "5ml", "미음", "반 그릇"):
        assert raw not in seen, raw


async def test_모델이_정리를_내지_못하면_실패():
    llm = FakeLLM(call("search_health_observations", {}))  # 다음 응답은 tool 없이 끝난다

    result = await run(visit(), world(COUGH), client=llm)

    assert result.status == "failed" and result.model_calls == 1
    assert not [r for r in result.readouts if r.kind == "symptom_timeline"]


async def test_3개월_미만_발열은_응급_안내가_먼저():
    # T22-C2 — 열 질문이든 정리든 emergency 가 타임라인보다 앞이다
    young = birth_for(2)
    emergency = READOUTS.render("emergency")

    fever = await run(visit("열 언제부터였지"), world(READING, birth=young), client=FakeLLM())
    assert fever.readouts[0] == emergency
    assert [r.kind for r in fever.readouts[1:]] == ["fever_timeline"] * len(fever.readouts[1:])
    assert len(fever.readouts) > 1 and fever.model_calls == 0

    llm = FakeLLM(
        call("search_health_observations", {}),
        call(
            "compose_symptom_timeline",
            {"keys": ["s1"]},
        ),
    )
    summary = await run(visit(), world(READING, birth=young), client=llm)

    assert [r.kind for r in summary.readouts] == ["emergency", "symptom_timeline"]
    assert summary.readouts[0] == emergency


async def test_3개월_이상이면_응급_안내가_없다():
    result = await run(visit("열 언제부터였지"), world(READING), client=FakeLLM())

    assert READOUTS.render("emergency") not in result.readouts


async def test_발열_기록이_없으면_응급_안내가_없다():
    result = await run(visit(), world(birth=birth_for(2)), client=FakeLLM())

    assert result.readouts == (READOUTS.render("visit.no_record"),)


async def test_안전_조회_실패여도_진행하고_경고():
    # T24-safetyfail — 통째로 중단하지 않고, 읽지 못한 것을 "등록된 내용 없음" 으로 쓰지도 않는다
    safety = InMemoryHealthSafety([PEANUT], fail=True)
    llm = FakeLLM(
        call("search_health_observations", {}),
        call(
            "compose_symptom_timeline",
            {"keys": ["s1"]},
        ),
    )

    result = await run(visit(), world(COUGH, safety=safety), client=llm)

    assert result.status == "completed" and result.model_calls == 1
    body = timeline_of(result).body
    warning = READOUTS.render("safety.unavailable").body
    assert body.splitlines()[0] == warning
    assert "알레르기 :" not in body and "땅콩" not in body and "등록된 내용이 없어요" not in body


async def test_동의가_없으면_조회도_하지_않는다():
    memory = InMemoryHealthMemory([COUGH])
    ctx = context(memory=memory, consent=False)
    llm = FakeLLM()

    result = await run(visit(), ctx, client=llm)

    assert result.status == "blocked" and llm.seen == [] and memory.calls == 0


class _RaisingLLM:
    """모델 호출이 예외를 낸다(공급자 장애)."""

    def __init__(self) -> None:
        self.seen: list = []

    async def chat(self, messages, **kwargs):
        self.seen.append(messages)
        raise RuntimeError("provider down")


class _BrokenMedication(InMemoryMedicationStore):
    async def courses_overlapping(self, **kwargs):
        raise RuntimeError("복약 조회 실패")


class TestEmergencySurvives:
    """3개월 미만 발열 안내는 나머지가 실패해도 결과에 남는다 — failed 는 pipeline 이 버린다."""

    async def test_정리_loop_가_끝나지_못해도_응급_안내는_남는다(self):
        # 조회만 하고 compose 를 부르지 않은 채 말로 끝난다
        llm = FakeLLM(call("search_health_observations", {}))

        result = await run(visit(), world(READING, birth=birth_for(2)), client=llm)

        assert result.status == "completed"
        assert result.readouts == (READOUTS.render("emergency"),)

    async def test_모델_예외여도_응급_안내는_남는다(self):
        llm = _RaisingLLM()

        result = await run(visit(), world(READING, birth=birth_for(2)), client=llm)

        assert result.status == "completed"
        assert result.readouts == (READOUTS.render("emergency"),)
        assert len(llm.seen) == 1

    async def test_열_경로의_복약_조회가_실패해도_응급_안내는_남는다(self):
        ctx = world(READING, birth=birth_for(2))
        broken = replace(ctx, ports=replace(ctx.ports, medication=_BrokenMedication()))

        result = await run(visit("열 언제부터였지"), broken, client=FakeLLM())

        assert result.status == "completed"
        assert result.model_calls == 0
        assert result.readouts == (READOUTS.render("emergency"),)

    async def test_3개월_이상이면_loop_실패는_failed_그대로(self):
        llm = FakeLLM(call("search_health_observations", {}))

        result = await run(visit(), world(COUGH), client=llm)

        assert result.status == "failed"
        assert result.readouts == ()

    async def test_3개월_이상이면_모델_예외는_그대로_올라간다(self):
        with pytest.raises(RuntimeError, match="provider down"):
            await run(visit(), world(COUGH), client=_RaisingLLM())

    async def test_3개월_이상이면_열_경로_복약_조회_예외도_그대로_올라간다(self):
        ctx = world(READING)
        broken = replace(ctx, ports=replace(ctx.ports, medication=_BrokenMedication()))

        with pytest.raises(RuntimeError, match="복약 조회 실패"):
            await run(visit("열 언제부터였지"), broken, client=FakeLLM())


class TestRouting:
    @pytest.mark.parametrize(
        "text",
        [
            "열이 언제부터인지랑 기침도 정리해줘",
            "몇 도까지 올랐었는지 포함해서 증상 정리해줘",
            "체온 정리해줘",
            "열 언제부터인지 한눈에 보여줘",
            "체온이랑 증상 요약해줘",
        ],
    )
    def test_열_말과_정리_말이_같이_있으면_정리로_간다(self, text):
        assert not is_fever_question([text])

    @pytest.mark.parametrize("text", ["열 언제부터야?", "몇 도까지 올랐어?", "체온 알려줘"])
    def test_열_말만_있으면_열_타임라인이다(self, text):
        assert is_fever_question([text])

    async def test_섞인_말은_모델_한_번으로_정리한다(self):
        llm = FakeLLM(
            call("search_health_observations", {}),
            call(
                "compose_symptom_timeline",
                {"keys": ["s1"]},
            ),
        )

        result = await run(
            visit("열이 언제부터인지랑 기침도 정리해줘"), world(COUGH, READING), client=llm
        )

        assert result.model_calls == 1
        assert [r.kind for r in result.readouts] == ["symptom_timeline"]


class TestRegistered:
    def test_라벨_실행표에_있다(self):
        assert HealthTaskType.VISIT_SUMMARY in registry.IMPLEMENTED_TASKS
        assert set(agent._LABEL_RUNNERS) == registry.IMPLEMENTED_TASKS

    def test_모델에는_조회_셋과_출력_하나만_열린다(self):
        # 약 이름 · 용량을 보여 주는 search_medication_schedules 는 열지 않는다 (#551)
        assert registry.tools_for(HealthTaskType.VISIT_SUMMARY, gate(48)) == (
            "compose_symptom_timeline",
            "search_health_observations",
            "search_medication_checks",
            "search_recent_meals",
        )


class TestPrompt:
    def test_줄은_고르기만_하고_쓰지_않는다(self):
        prompt = build_system_prompt(world(), HealthTaskType.VISIT_SUMMARY)

        assert TODAY.isoformat() in prompt
        assert "key" in prompt and "compose_symptom_timeline" in prompt
        assert "문장을 쓰지 않는다" in prompt
        # 줄을 쓰던 시절의 지시가 남아 있지 않다
        assert "ref" not in prompt and "전체 줄" not in prompt

    def test_조회_tool_셋만_안내한다(self):
        prompt = build_system_prompt(world(), HealthTaskType.VISIT_SUMMARY)

        for name in (
            "search_health_observations",
            "search_recent_meals",
            "search_medication_checks",
        ):
            assert name in prompt
        assert "search_medication_schedules" not in prompt

    def test_알레르기는_코드가_붙이고_건강정보를_싣지_않는다(self):
        prompt = build_system_prompt(world(), HealthTaskType.VISIT_SUMMARY)

        assert "알레르기" in prompt and "만성질환" in prompt  # 코드가 붙인다는 안내
        for forbidden in ("별이", "땅콩"):
            assert forbidden not in prompt
