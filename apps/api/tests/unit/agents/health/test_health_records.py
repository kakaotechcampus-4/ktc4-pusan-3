"""진료 준비 조회 tool — 증상 에피소드 · 먹인 약 · 최근 식사 · 복약 체크.

- 🚨 모델에게 가는 결과는 key · 날짜 · 증상 코드뿐이다 (#551). 원문 · 약 이름 · 용량 · 식사 내용은
  `state.visit_lines` 의 줄(코드가 그린 글)에만 있다
- 증상은 에피소드 단위 줄이고, 먹인 약 행은 에피소드 밖에서 시각순 줄로 간다
- 식사 줄에는 영양 수치가 없다
- 복약 체크는 날짜 · 코스별 `n/m회` 사실만 — 체크 안 된 회차는 "안 먹였다" 가 아니라 `m` 에 센 채
  `n` 에서 빠진다
- 오늘 이후 회차는 세지 않는다. 끝났거나 멈춘 코스도 기간이 겹치면 넣는다
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
from app.agents.health.schemas.records import (
    SearchHealthArgs,
    SearchMealsArgs,
    SearchMedicationChecksArgs,
)
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import DoseSlot, HealthObservation, MealRecord
from app.agents.health.tools.records import (
    search_health_observations,
    search_medication_checks,
    search_recent_meals,
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


def line(ctx, key: str) -> str:
    """코드가 그린 그 key 의 줄."""
    return ctx.state.visit_lines[key].body


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
        assert result.data["symptoms"] == [
            {"key": "s1", "from": "2026-09-24", "to": "2026-09-24", "codes": ["fever"]},
            {"key": "s2", "from": "2026-09-26", "to": "2026-09-28", "codes": ["cough"]},
        ]
        # 글은 코드가 그린다 — 사전 이름 · 보호자가 말한 정도 · 조치 원문
        assert line(ctx, "s1") == "9/24 발열 · 정도: 심함"
        assert line(ctx, "s2") == "9/26–9/28 기침 · 조치: 물 많이 먹임"

    async def test_사전에_없는_증상은_원문_그대로_한_줄(self):
        ctx = with_memory(row(TODAY - DAY, symptoms=("눈을 자꾸 비벼요",)))

        result = await search_health_observations(ctx, SearchHealthArgs())

        assert result.data["symptoms"] == [
            {"key": "s1", "from": "2026-09-27", "to": "2026-09-27", "codes": []}
        ]
        assert line(ctx, "s1") == "9/27 눈을 자꾸 비벼요"

    async def test_원문의_해석_표현은_줄에_내지_않는다(self):
        # Memory 가 뽑은 문장이라 보호자 말 그대로라고 보장하지 못한다 — 인용 예외가 없다
        ctx = with_memory(row(TODAY, symptoms=("중이염이래요",), action_taken="감기 때문에 병원"))

        await search_health_observations(ctx, SearchHealthArgs())

        assert line(ctx, "s1") == f"9/28 {FLAGGED} · 조치: {FLAGGED}"

    async def test_모델에게는_원문을_보내지_않는다(self):
        fever = row(TODAY - DAY, symptoms=("열나", "귀를 만져요"), action_taken="물수건")
        medicine = given_row(TODAY, "타이레놀", linked_symptom_id=fever.id)
        ctx = with_memory(fever, medicine)

        result = await search_health_observations(ctx, SearchHealthArgs())

        payload = sent(result)
        for text in ("열나", "귀를 만져요", "물수건", "타이레놀"):
            assert text not in payload
        assert set(result.data) == {"symptoms", "given"}
        assert all(set(e) == {"key", "from", "to", "codes"} for e in result.data["symptoms"])
        assert all(set(g) == {"key", "date", "symptom_key"} for g in result.data["given"])

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

        result = await search_health_observations(ctx, SearchHealthArgs())

        # 약 행은 에피소드 줄에 들어가지 않는다 — 증상 줄은 열 하나
        assert [e["codes"] for e in result.data["symptoms"]] == [["fever"]]
        given = result.data["given"]
        # UTC 로 온 시각은 아이 타임존으로 — 9/27 22:30 UTC 는 KST 9/28 07:30 이다
        assert [g["date"] for g in given] == ["2026-09-28", "2026-09-28"]
        assert [line(ctx, g["key"]) for g in given] == [
            "9/28 07:30 해열제 먹였다고 기록하셨어요",
            "9/28 09:00 해열제 먹였다고 기록하셨어요",
        ]
        # 연결은 돌려준 증상 줄이면 그 줄의 key, 아니면 None
        assert [g["symptom_key"] for g in given] == [None, "s1"]

    async def test_시각을_모르는_약_행은_날짜만(self):
        ctx = with_memory(given_row(TODAY - DAY, "시럽"))

        result = await search_health_observations(ctx, SearchHealthArgs())

        [given] = result.data["given"]
        assert given["date"] == "2026-09-27"
        assert line(ctx, given["key"]) == "9/27 시럽 먹였다고 기록하셨어요"
        assert result.data["symptoms"] == []

    async def test_증상_필터에_걸리지_않은_증상에_묶인_약_행은_넣지_않는다(self):
        cough = row(TODAY, symptoms=("기침",))
        fever = row(TODAY, symptoms=("열나",))
        medicine = given_row(TODAY, "해열제", linked_symptom_id=fever.id)
        ctx = with_memory(cough, fever, medicine)

        result = await search_health_observations(ctx, SearchHealthArgs(symptom="기침"))

        assert [e["codes"] for e in result.data["symptoms"]] == [["cough"]]
        assert result.data["given"] == []
        assert {ref.id for v in ctx.state.visit_lines.values() for ref in v.refs} == {cough.id}

    async def test_연결된_증상이_기간_밖이면_연결을_싣지_않는다(self):
        # 이번 결과에 없는 줄의 key 를 돌려주면 모델이 못 본 줄을 가리키게 된다
        old_fever = row(TODAY - 20 * DAY, symptoms=("열나",))
        medicine = given_row(TODAY, "해열제", linked_symptom_id=old_fever.id)
        ctx = with_memory(old_fever, medicine)

        result = await search_health_observations(ctx, SearchHealthArgs(period="14d"))

        assert result.data["given"][0]["symptom_key"] is None

    async def test_증상_필터는_코드가_같은_말을_함께_찾는다(self):
        ctx = with_memory(row(TODAY - DAY, symptoms=("기침",)), row(TODAY, symptoms=("콧물",)))

        result = await search_health_observations(ctx, SearchHealthArgs(symptom="콜록"))

        assert [e["codes"] for e in result.data["symptoms"]] == [["cough"]]

    async def test_기간_밖_기록은_읽지_않는다(self):
        ctx = with_memory(
            row(TODAY - 5 * DAY, symptoms=("열나",)), row(TODAY - DAY, symptoms=("기침",))
        )

        result = await search_health_observations(ctx, SearchHealthArgs(period="3d"))

        assert [e["codes"] for e in result.data["symptoms"]] == [["cough"]]
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

        assert "38.5" not in sent(result) and "38.5" not in line(ctx, "s1")

    async def test_줄의_근거는_묶은_기록_전부(self):
        fever = row(TODAY - DAY, symptoms=("열나",))
        again = row(TODAY, symptoms=("열나",))
        medicine = given_row(TODAY, "해열제", linked_symptom_id=again.id)
        ctx = with_memory(fever, again, medicine)

        result = await search_health_observations(ctx, SearchHealthArgs())

        [episode] = result.data["symptoms"]
        assert ctx.state.visit_lines[episode["key"]].refs == (
            Ref("observation_health", fever.id),
            Ref("observation_health", again.id),
        )
        [given] = result.data["given"]
        assert given["symptom_key"] == episode["key"]
        assert ctx.state.visit_lines[given["key"]].refs == (Ref("observation_health", medicine.id),)

    async def test_다시_불러도_같은_기록은_같은_key(self):
        ctx = with_memory(row(TODAY, symptoms=("열나",)), given_row(TODAY, "해열제"))

        first = await search_health_observations(ctx, SearchHealthArgs())
        again = await search_health_observations(ctx, SearchHealthArgs(period="30d"))

        assert first.data == again.data
        assert set(ctx.state.visit_lines) == {"s1", "g1"}


class TestSearchRecentMeals:
    async def test_식사는_영양_수치_없이(self):
        meal = MealRecord(UUID(int=77), TODAY - DAY, "미역국", "반 그릇")
        ctx = with_memory(meals=[meal, MealRecord(UUID(int=78), TODAY - 9 * DAY, "죽", None)])

        result = await search_recent_meals(ctx, SearchMealsArgs())

        assert result.success
        # 3일 밖(9일 전)은 읽지 않는다. 모델에게는 key · 날짜만
        assert result.data["meals"] == [{"key": "f1", "date": "2026-09-27"}]
        assert "미역국" not in sent(result)
        assert line(ctx, "f1") == "9/27 미역국 반 그릇"
        assert ctx.state.visit_lines["f1"].refs == (Ref("observation_food", meal.id),)

    async def test_양을_말하지_않았으면_음식만(self):
        ctx = with_memory(meals=[MealRecord(UUID(int=79), TODAY, "죽", None)])

        await search_recent_meals(ctx, SearchMealsArgs())

        assert line(ctx, "f1") == "9/28 죽"

    async def test_기록이_없어도_실패가_아니다(self):
        # 식사는 곁들이는 줄이라 0건이 진료 준비를 막지 않는다
        result = await search_recent_meals(with_memory(), SearchMealsArgs())

        assert result.success and result.data["meals"] == []


class TestSearchMedicationChecks:
    async def test_복약_체크는_날짜별_n_m(self):
        # 3일 × 하루 3번, 그중 7회 체크 → 3/3 · 3/3 · 1/3
        day0, day1, day2 = TODAY - 2 * DAY, TODAY - DAY, TODAY
        checked = [(d, s.scheduled_time) for d in (day0, day1) for s in PATTERN]
        checked.append((day2, PATTERN[0].scheduled_time))
        ctx = with_courses(antibiotic(checked))

        result = await search_medication_checks(ctx, SearchMedicationChecksArgs())

        assert result.success
        assert result.data["checks"] == [
            {"key": "c1", "date": "2026-09-26"},
            {"key": "c2", "date": "2026-09-27"},
            {"key": "c3", "date": "2026-09-28"},
        ]
        assert [line(ctx, f"c{n}") for n in (1, 2, 3)] == [
            "9/26 항생제 5ml 체크 3/3회",
            "9/27 항생제 5ml 체크 3/3회",
            "9/28 항생제 5ml 체크 1/3회",
        ]
        # 약 이름 · 용량은 모델에게 가지 않는다
        assert "항생제" not in sent(result) and "5ml" not in sent(result)

    async def test_오늘_이후_회차는_세지_않는다(self):
        # 오늘 10:00 — 08:30 회차만 지났다. 12:30 · 19:30 은 아직 오지 않았다
        now = datetime(2026, 9, 28, 10, 0, tzinfo=KST)
        ctx = with_courses(antibiotic([(TODAY, PATTERN[0].scheduled_time)]), now=now)

        result = await search_medication_checks(ctx, SearchMedicationChecksArgs())

        [today] = [c for c in result.data["checks"] if c["date"] == "2026-09-28"]
        assert line(ctx, today["key"]) == "9/28 항생제 5ml 체크 1/1회"

    async def test_끝난_코스와_중단한_코스도_기간이_겹치면_넣는다(self):
        ended = antibiotic(start=6, end=3)  # 지난주에 끝난 항생제
        stopped = course_with_doses(
            "시럽", PATTERN, TODAY - 4 * DAY, TODAY + 2 * DAY, status="stopped"
        )
        old = course_with_doses("옛 약", PATTERN, TODAY - 30 * DAY, TODAY - 20 * DAY)
        ctx = with_courses(ended, stopped, old)

        result = await search_medication_checks(ctx, SearchMedicationChecksArgs(period="7d"))

        titles = {line(ctx, c["key"]).split()[1] for c in result.data["checks"]}
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

        result = await search_medication_checks(ctx, SearchMedicationChecksArgs())

        # 멈춘 날은 08:30 · 12:30 두 회차만(19:30 은 멈춘 뒤). 그 뒤 날짜는 줄이 없다
        assert [line(ctx, c["key"]) for c in result.data["checks"]] == [
            "9/25 시럽 체크 1/3회",
            "9/26 시럽 체크 0/2회",
        ]

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

        result = await search_medication_checks(ctx, SearchMedicationChecksArgs())

        # 08:30 은 멈추기 전이라 계획, 12:30 은 멈춘 뒤지만 체크돼 있어 계획 + 체크, 19:30 은 뺀다
        assert [line(ctx, c["key"]) for c in result.data["checks"]] == ["9/27 시럽 체크 1/2회"]

    async def test_멈춘_시각을_모르면_예전처럼_센다(self):
        stopped = course_with_doses(
            "시럽", PATTERN, TODAY - DAY, TODAY, status="stopped", stopped_at=None
        )
        ctx = with_courses(stopped)

        result = await search_medication_checks(ctx, SearchMedicationChecksArgs())

        assert [line(ctx, c["key"]) for c in result.data["checks"]] == [
            "9/27 시럽 체크 0/3회",
            "9/28 시럽 체크 0/3회",
        ]

    async def test_저장소로_멈추면_멈춘_시각이_코스에_실린다(self):
        course, doses = antibiotic()
        store = InMemoryMedicationStore([course], doses)
        at = datetime(2026, 9, 27, 9, 0, tzinfo=KST)

        await store.stop_course(schedule_id=course.id, stopped_at=at)
        [seen] = await store.courses_overlapping(
            child_id=CHILD, date_from=TODAY - 5 * DAY, date_to=TODAY
        )

        assert (seen.status, seen.stopped_at) == ("stopped", at)

    async def test_기간과_겹치는_코스가_없으면_NO_RECORDS(self):
        old = course_with_doses("옛 약", PATTERN, TODAY - 40 * DAY, TODAY - 31 * DAY)

        result = await search_medication_checks(
            with_courses(old), SearchMedicationChecksArgs(period="30d")
        )

        assert result.success is False
        assert result.error is not None and result.error["code"] == ErrorCode.NO_RECORDS

    async def test_첫_회차가_오기_전인_코스는_줄을_만들지_않는다(self):
        # 오늘 시작해 아직 첫 회차가 오지 않은 코스 — 0/0 줄을 지어내지 않는다
        now = datetime(2026, 9, 28, 7, 0, tzinfo=KST)
        fresh = course_with_doses("새 약", PATTERN, TODAY, TODAY + 4 * DAY)
        ctx = with_courses(fresh, now=now)

        result = await search_medication_checks(ctx, SearchMedicationChecksArgs())

        assert result.success and result.data["checks"] == []
        assert ctx.state.visit_lines == {}

    async def test_체크_안_된_회차를_안_먹였다고_쓰지_않는다(self):
        ctx = with_courses(antibiotic())

        result = await search_medication_checks(ctx, SearchMedicationChecksArgs())

        bodies = [line(ctx, c["key"]) for c in result.data["checks"]]
        assert bodies and all(body.endswith("체크 0/3회") for body in bodies)
        assert not any(word in body for body in bodies for word in FORBIDDEN_DOSE_GUESS)

    async def test_줄의_근거는_코스(self):
        course, doses = antibiotic()
        ctx = with_courses((course, doses))

        await search_medication_checks(ctx, SearchMedicationChecksArgs())

        assert {v.refs for v in ctx.state.visit_lines.values()} == {
            (Ref("medication_schedule", course.id),)
        }


class TestRegistry:
    async def test_세_tool_이름으로_실행된다(self):
        ctx = with_memory(row(TODAY, symptoms=("열나",)))
        allowed = (
            "search_health_observations",
            "search_recent_meals",
            "search_medication_checks",
        )

        health = await execute_tool("search_health_observations", {}, ctx, allowed=allowed)
        meals = await execute_tool("search_recent_meals", {}, ctx, allowed=allowed)
        checks = await execute_tool("search_medication_checks", {}, ctx, allowed=allowed)

        assert health.success and meals.success
        assert checks.error is not None and checks.error["code"] == ErrorCode.NO_RECORDS

    @pytest.mark.parametrize(
        ("name", "arguments"),
        [
            ("search_health_observations", {"period": "90d"}),
            ("search_recent_meals", {"period": "7d"}),
            ("search_medication_checks", {"period": "90d"}),
            ("search_health_observations", {"child_id": "x"}),
        ],
    )
    async def test_스키마_밖_인자는_거절(self, name, arguments):
        result = await execute_tool(name, arguments, context(), allowed=(name,))

        assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS
