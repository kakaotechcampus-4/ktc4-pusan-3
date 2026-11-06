"""진료 준비가 읽는 기록 — 증상 에피소드 · 먹인 약(모델 tool) · 최근 식사 · 복약 체크(코드).

- 🚨 모델 tool 은 `search_health_observations` 하나고, 모델에게 가는 결과는 줄 수뿐이다 (#551 리뷰).
  원문 · 약 이름 · 용량 · 식사 내용은 `state.visit_lines` 의 줄(코드가 그린 글)에만 있다
- 식사 · 복약 체크는 모델이 부르지 않는다 — 출력 tool 이 코드로 읽는다(`collect_meals` ·
  `collect_medication_checks`)
- 증상은 에피소드 단위 줄이고, 먹인 약 행은 에피소드 밖에서 시각순 줄로 간다
- 복약 체크는 날짜 · 코스별 `n/m회` 사실만 — 체크 안 된 회차는 "안 먹였다" 가 아니라 `m` 에 센 채
  `n` 에서 빠진다. 오늘 이후 회차는 세지 않는다. 끝났거나 멈춘 코스도 기간이 겹치면 넣는다
"""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.agents.common.refs import Ref
from app.agents.health.registry import execute_tool
from app.agents.health.result import ErrorCode
from app.agents.health.schemas.records import SearchHealthArgs
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot, HealthObservation, MealRecord
from app.agents.health.tools.records import (
    collect_meals,
    collect_medication_checks,
    search_health_observations,
)
from app.agents.health.wording import FLAGGED, FORBIDDEN_DOSE_GUESS
from tests.unit.agents.health.support import CHILD, KST, TODAY, context

DAY = timedelta(days=1)
PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
# 오늘 밤 11시(KST) — 오늘 회차가 전부 지난 시각이다
EVENING_END = datetime(2026, 9, 28, 23, 0, tzinfo=KST)
_ids = iter(range(5000, 6000))


def row(day: date, *, symptoms=("기침",), to: date | None = None, **kwargs) -> HealthObservation:
    return HealthObservation(
        id=UUID(int=next(_ids)),
        observed_from=day,
        observed_to=to or day,
        observed_time=kwargs.pop("at", None),
        symptoms=tuple(symptoms),
        **kwargs,
    )


def given_row(day: date, drug: str, **kwargs) -> HealthObservation:
    """먹인 약 행 — 증상 칸이 비어 있다."""
    return row(day, symptoms=(), medication_given=drug, **kwargs)


def with_memory(*observations, meals=()):
    return context(memory=InMemoryHealthMemory(observations, meals))


def with_courses(*seeds, now: datetime = EVENING_END):
    store = InMemoryMedicationStore([c for c, _ in seeds], [d for _, rows in seeds for d in rows])
    return replace(context(medication=store), now=now)


def antibiotic(checked=(), *, start: int = 2, end: int = 0, **kwargs):
    return course_with_doses(
        "항생제",
        PATTERN,
        TODAY - start * DAY,
        TODAY - end * DAY,
        dosage="5ml",
        checked=checked,
        **kwargs,
    )


def lines(ctx, section: str) -> list[str]:
    """코드가 그린 그 구획의 줄 — 읽은 순서."""
    return [line.body for line in ctx.state.visit_lines.values() if line.section == section]


def refs(ctx, section: str) -> list[tuple[Ref, ...]]:
    return [line.refs for line in ctx.state.visit_lines.values() if line.section == section]


def sent(result) -> str:
    """모델에게 가는 tool 결과 그대로(JSON)."""
    return json.dumps(result.to_payload(), ensure_ascii=False, default=str)


class TestSearchHealthObservations:
    async def test_증상은_에피소드_단위(self):
        # 기침 3일 연속은 한 줄. 나흘 전 고열은 따로 한 줄
        ctx = with_memory(
            row(TODAY - 4 * DAY, symptoms=("열나",), severity="severe"),
            row(TODAY - 2 * DAY, symptoms=("기침",)),
            row(TODAY - DAY, symptoms=("기침",), action_taken="물 많이 먹임"),
            row(TODAY, symptoms=("잔기침",)),
        )

        result = await search_health_observations(ctx, SearchHealthArgs())

        assert result.success
        assert result.data == {"symptom_lines": 2, "given_lines": 0}
        # 글은 코드가 그린다 — 사전 이름 · 보호자가 말한 정도 · 조치 원문
        assert lines(ctx, "symptom") == [
            "9/24 발열 · 정도: 심함",
            "9/26–9/28 기침 · 조치: 물 많이 먹임",
        ]

    async def test_사전에_없는_증상은_원문_그대로_한_줄(self):
        ctx = with_memory(row(TODAY - DAY, symptoms=("눈을 자꾸 비벼요",)))

        await search_health_observations(ctx, SearchHealthArgs())

        assert lines(ctx, "symptom") == ["9/27 눈을 자꾸 비벼요"]

    async def test_원문의_해석_표현은_줄에_내지_않는다(self):
        # Memory 가 뽑은 문장이라 보호자 말 그대로라고 보장하지 못한다 — 인용 예외가 없다
        ctx = with_memory(row(TODAY, symptoms=("중이염이래요",), action_taken="감기 때문에 병원"))

        await search_health_observations(ctx, SearchHealthArgs())

        assert lines(ctx, "symptom") == [f"9/28 {FLAGGED} · 조치: {FLAGGED}"]

    async def test_모델에게는_줄_수만_간다(self):
        fever = row(TODAY - DAY, symptoms=("열나", "귀를 만져요"), action_taken="물수건")
        medicine = given_row(TODAY, "타이레놀", linked_symptom_id=fever.id)
        ctx = with_memory(fever, medicine)

        result = await search_health_observations(ctx, SearchHealthArgs())

        payload = sent(result)
        for text in ("열나", "귀를 만져요", "물수건", "타이레놀", "fever", "2026-"):
            assert text not in payload
        assert result.data == {"symptom_lines": 2, "given_lines": 1}

    async def test_먹인_약은_에피소드_밖_시각순_줄로(self):
        fever = row(TODAY - DAY, symptoms=("열나",))
        late = given_row(
            TODAY,
            "해열제",
            at=datetime(2026, 9, 28, 9, 0, tzinfo=KST),
            linked_symptom_id=fever.id,
        )
        early = given_row(TODAY - DAY, "해열제", at=datetime(2026, 9, 27, 22, 30, tzinfo=UTC))
        ctx = with_memory(late, fever, early)

        await search_health_observations(ctx, SearchHealthArgs())

        # 약 행은 에피소드 줄에 들어가지 않는다 — 증상 줄은 열 하나
        assert lines(ctx, "symptom") == ["9/27 발열"]
        # UTC 로 온 시각은 아이 타임존으로 — 9/27 22:30 UTC 는 KST 9/28 07:30 이다
        assert lines(ctx, "given") == [
            "9/28 07:30 해열제 먹였다고 기록하셨어요",
            "9/28 09:00 해열제 먹였다고 기록하셨어요",
        ]

    async def test_시각을_모르는_약_행은_날짜만(self):
        ctx = with_memory(given_row(TODAY - DAY, "시럽"))

        result = await search_health_observations(ctx, SearchHealthArgs())

        assert result.data == {"symptom_lines": 0, "given_lines": 1}
        assert lines(ctx, "given") == ["9/27 시럽 먹였다고 기록하셨어요"]

    async def test_증상_필터에_걸리지_않은_증상에_묶인_약_행은_넣지_않는다(self):
        cough = row(TODAY, symptoms=("기침",))
        fever = row(TODAY, symptoms=("열나",))
        medicine = given_row(TODAY, "해열제", linked_symptom_id=fever.id)
        ctx = with_memory(cough, fever, medicine)

        await search_health_observations(ctx, SearchHealthArgs(symptom="기침"))

        assert lines(ctx, "symptom") == ["9/28 기침"]
        assert lines(ctx, "given") == []

    async def test_증상_필터는_코드가_같은_말을_함께_찾는다(self):
        ctx = with_memory(row(TODAY - DAY, symptoms=("기침",)), row(TODAY, symptoms=("콧물",)))

        await search_health_observations(ctx, SearchHealthArgs(symptom="콜록"))

        assert lines(ctx, "symptom") == ["9/27 기침"]

    async def test_사전_별칭이_아닌_짧은_말도_사전_코드로_찾는다(self):
        # 라이브 eval T23 — "요즘 열 얼마나 났지" 의 "열" 은 별칭이 아니다. 원문 글자로 거르면
        # "38도" 기록이 떨어져 10/12–10/14 에피소드가 "10/14" 로 잘렸다 (#551 BE 리뷰)
        ctx = with_memory(
            row(TODAY - 2 * DAY, symptoms=("38도",)),
            row(TODAY, symptoms=("열이 나요",)),
            row(TODAY, symptoms=("기침",)),
        )

        await search_health_observations(ctx, SearchHealthArgs(symptom="열"))

        assert lines(ctx, "symptom") == ["9/26–9/28 발열"]

    async def test_사전에_전혀_없는_말은_원문_글자로_찾는다(self):
        ctx = with_memory(
            row(TODAY, symptoms=("눈을 자꾸 비벼요",)), row(TODAY, symptoms=("귀를 만져요",))
        )

        await search_health_observations(ctx, SearchHealthArgs(symptom="비벼"))

        assert lines(ctx, "symptom") == ["9/28 눈을 자꾸 비벼요"]

    async def test_기간_밖_기록은_읽지_않는다(self):
        ctx = with_memory(
            row(TODAY - 5 * DAY, symptoms=("열나",)), row(TODAY - DAY, symptoms=("기침",))
        )

        await search_health_observations(ctx, SearchHealthArgs(period="3d"))

        assert lines(ctx, "symptom") == ["9/27 기침"]
        assert ctx.ports.memory.requested == [(TODAY - 2 * DAY, TODAY)]

    async def test_기록이_없으면_NO_RECORDS(self):
        result = await search_health_observations(with_memory(), SearchHealthArgs())

        assert result.success is False
        assert result.error is not None and result.error["code"] == ErrorCode.NO_RECORDS

    async def test_체온_칸이_있어도_줄에는_싣지_않는다(self):
        # 체온 나열은 열 타임라인(코드)의 몫이다 — 이 tool 은 에피소드 줄만
        ctx = with_memory(
            row(
                TODAY,
                symptoms=("열나",),
                temperature=Decimal("38.5"),
                measured_at=datetime(2026, 9, 28, 1, 0, tzinfo=KST),
                measure_site="ear",
            )
        )

        result = await search_health_observations(ctx, SearchHealthArgs())

        assert "38.5" not in sent(result)
        assert lines(ctx, "symptom") == ["9/28 발열"]

    async def test_줄의_근거는_묶은_기록_전부(self):
        fever = row(TODAY - DAY, symptoms=("열나",))
        again = row(TODAY, symptoms=("열나",))
        medicine = given_row(TODAY, "해열제", linked_symptom_id=again.id)
        ctx = with_memory(fever, again, medicine)

        await search_health_observations(ctx, SearchHealthArgs())

        assert refs(ctx, "symptom") == [
            (Ref("observation_health", fever.id), Ref("observation_health", again.id))
        ]
        assert refs(ctx, "given") == [(Ref("observation_health", medicine.id),)]

    async def test_다시_불러도_같은_기록은_한_줄(self):
        ctx = with_memory(row(TODAY, symptoms=("열나",)), given_row(TODAY, "해열제"))

        await search_health_observations(ctx, SearchHealthArgs())
        await search_health_observations(ctx, SearchHealthArgs(period="30d"))

        assert set(ctx.state.visit_lines) == {"s1", "g1"}

    async def test_부른_기간_중_가장_긴_것을_남긴다(self):
        # 복약 체크가 같은 기간을 본다
        ctx = with_memory(row(TODAY, symptoms=("열나",)))

        await search_health_observations(ctx, SearchHealthArgs(period="30d"))
        await search_health_observations(ctx, SearchHealthArgs(period="7d"))

        assert ctx.state.visit_period == "30d"


class TestCollectMeals:
    async def test_식사는_3일만_영양_수치_없이(self):
        meal = MealRecord(UUID(int=77), TODAY - DAY, "미역국", "반 그릇")
        ctx = with_memory(meals=[meal, MealRecord(UUID(int=78), TODAY - 9 * DAY, "죽", None)])

        count = await collect_meals(ctx)

        # 3일 밖(9일 전)은 읽지 않는다
        assert count == 1
        assert lines(ctx, "meal") == ["9/27 미역국 반 그릇"]
        assert refs(ctx, "meal") == [(Ref("observation_food", meal.id),)]

    async def test_양을_말하지_않았으면_음식만(self):
        ctx = with_memory(meals=[MealRecord(UUID(int=79), TODAY, "죽", None)])

        await collect_meals(ctx)

        assert lines(ctx, "meal") == ["9/28 죽"]

    async def test_기록이_없어도_실패가_아니다(self):
        # 식사는 곁들이는 줄이라 0건이 진료 준비를 막지 않는다
        ctx = with_memory()

        assert await collect_meals(ctx) == 0
        assert lines(ctx, "meal") == []


class TestCollectMedicationChecks:
    async def test_복약_체크는_날짜별_n_m(self):
        # 3일 × 하루 3번, 그중 7회 체크 → 3/3 · 3/3 · 1/3
        day0, day1, day2 = TODAY - 2 * DAY, TODAY - DAY, TODAY
        checked = [(d, s.scheduled_time) for d in (day0, day1) for s in PATTERN]
        checked.append((day2, PATTERN[0].scheduled_time))
        ctx = with_courses(antibiotic(checked))

        count = await collect_medication_checks(ctx, "14d")

        assert count == 3
        assert lines(ctx, "medication") == [
            "9/26 항생제 5ml 체크 3/3회",
            "9/27 항생제 5ml 체크 3/3회",
            "9/28 항생제 5ml 체크 1/3회",
        ]

    async def test_오늘_이후_회차는_세지_않는다(self):
        # 오늘 10:00 — 08:30 회차만 지났다. 12:30 · 19:30 은 아직 오지 않았다
        now = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
        ctx = with_courses(antibiotic([(TODAY, PATTERN[0].scheduled_time)]), now=now)

        await collect_medication_checks(ctx, "14d")

        assert lines(ctx, "medication")[-1] == "9/28 항생제 5ml 체크 1/1회"

    async def test_끝난_코스와_중단한_코스도_기간이_겹치면_넣는다(self):
        ended = antibiotic(start=6, end=3)  # 지난주에 끝난 항생제
        stopped = course_with_doses(
            "시럽", PATTERN, TODAY - 4 * DAY, TODAY + 2 * DAY, status="stopped"
        )
        old = course_with_doses("옛 약", PATTERN, TODAY - 30 * DAY, TODAY - 20 * DAY)
        ctx = with_courses(ended, stopped, old)

        await collect_medication_checks(ctx, "7d")

        titles = {body.split()[1] for body in lines(ctx, "medication")}
        assert titles == {"항생제", "시럽"}

    async def test_멈춘_코스의_멈춘_뒤_회차는_계획이_아니다(self):
        # 이틀 전 낮에 멈춘 코스 — 그 뒤 날짜에 0/3 줄이 생기면 먹이려던 적 없는 회차를 센 것이다
        stopped_at = datetime.combine(TODAY - 2 * DAY, time(13, 0), tzinfo=KST)
        stopped = course_with_doses(
            "시럽",
            PATTERN,
            TODAY - 3 * DAY,
            TODAY + 2 * DAY,
            status="stopped",
            stopped_at=stopped_at,
            checked=[(TODAY - 3 * DAY, PATTERN[0].scheduled_time)],
        )
        ctx = with_courses(stopped)

        await collect_medication_checks(ctx, "14d")

        # 멈춘 날은 08:30 · 12:30 두 회차만(19:30 은 멈춘 뒤). 그 뒤 날짜는 줄이 없다
        assert lines(ctx, "medication") == ["9/25 시럽 체크 1/3회", "9/26 시럽 체크 0/2회"]

    async def test_멈춘_뒤에_체크된_회차도_사실이라_센다(self):
        stopped_at = datetime.combine(TODAY - DAY, time(9, 0), tzinfo=KST)
        stopped = course_with_doses(
            "시럽",
            PATTERN,
            TODAY - DAY,
            TODAY,
            status="stopped",
            stopped_at=stopped_at,
            checked=[(TODAY - DAY, PATTERN[1].scheduled_time)],
        )
        ctx = with_courses(stopped)

        await collect_medication_checks(ctx, "14d")

        # 08:30 은 멈추기 전이라 계획, 12:30 은 멈춘 뒤지만 체크돼 있어 계획 + 체크, 19:30 은 뺀다
        assert lines(ctx, "medication") == ["9/27 시럽 체크 1/2회"]

    async def test_멈춘_시각을_모르면_예전처럼_센다(self):
        stopped = course_with_doses(
            "시럽", PATTERN, TODAY - DAY, TODAY, status="stopped", stopped_at=None
        )
        ctx = with_courses(stopped)

        await collect_medication_checks(ctx, "14d")

        assert lines(ctx, "medication") == ["9/27 시럽 체크 0/3회", "9/28 시럽 체크 0/3회"]

    async def test_저장소로_멈추면_멈춘_시각이_코스에_실린다(self):
        course, doses = antibiotic()
        store = InMemoryMedicationStore([course], doses)
        at = datetime(2026, 9, 27, 9, 0, tzinfo=KST)

        await store.stop_course(child_id=CHILD, schedule_id=course.id, stopped_at=at)
        [seen] = await store.courses_overlapping(
            child_id=CHILD, date_from=TODAY - 5 * DAY, date_to=TODAY
        )

        assert (seen.status, seen.stopped_at) == ("stopped", at)

    async def test_기간과_겹치는_코스가_없으면_줄이_없다(self):
        old = course_with_doses("옛 약", PATTERN, TODAY - 40 * DAY, TODAY - 31 * DAY)
        ctx = with_courses(old)

        assert await collect_medication_checks(ctx, "30d") == 0
        assert lines(ctx, "medication") == []

    async def test_첫_회차가_오기_전인_코스는_줄을_만들지_않는다(self):
        # 오늘 시작해 아직 첫 회차가 오지 않은 코스 — 0/0 줄을 지어내지 않는다
        now = datetime(2026, 9, 28, 7, 0, tzinfo=KST)
        ctx = with_courses(course_with_doses("새 약", PATTERN, TODAY, TODAY + 4 * DAY), now=now)

        assert await collect_medication_checks(ctx, "14d") == 0
        assert ctx.state.visit_lines == {}

    async def test_체크_안_된_회차를_안_먹였다고_쓰지_않는다(self):
        ctx = with_courses(antibiotic())

        await collect_medication_checks(ctx, "14d")

        bodies = lines(ctx, "medication")
        assert bodies and all(body.endswith("체크 0/3회") for body in bodies)
        assert not any(word in body for body in bodies for word in FORBIDDEN_DOSE_GUESS)

    async def test_줄의_근거는_코스(self):
        course, doses = antibiotic()
        ctx = with_courses((course, doses))

        await collect_medication_checks(ctx, "14d")

        assert set(refs(ctx, "medication")) == {(Ref("medication_schedule", course.id),)}


class TestRegistry:
    async def test_모델_tool_은_증상_조회_하나다(self):
        ctx = with_memory(row(TODAY, symptoms=("열나",)))
        allowed = ("search_health_observations",)

        health = await execute_tool("search_health_observations", {}, ctx, allowed=allowed)
        meals = await execute_tool("search_recent_meals", {}, ctx, allowed=allowed)

        assert health.success
        assert meals.error is not None and meals.error["code"] == ErrorCode.TOOL_NOT_ALLOWED

    @pytest.mark.parametrize(
        "arguments",
        [{"period": "90d"}, {"child_id": "x"}, {"keys": ["s1"]}],
    )
    async def test_스키마_밖_인자는_거절(self, arguments):
        result = await execute_tool(
            "search_health_observations",
            arguments,
            context(),
            allowed=("search_health_observations",),
        )

        assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS
