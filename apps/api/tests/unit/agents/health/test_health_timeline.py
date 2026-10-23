"""진료 준비 출력 tool `compose_symptom_timeline` — 인자가 없다.

- 🚨 모델은 줄을 고르지도 쓰지도 않는다 (#551 리뷰). 모델이 조회한 증상 · 먹인 약 줄에 코드가 읽는
  최근 3일 식사 · 복약 체크 줄을 늘 더한다 — 라이브 eval 에서 모델이 식사 · 복약 조회를 건너뛰고
  증상 줄도 빠뜨렸다
- 모델이 증상 조회를 부르지 않았으면 기본 기간(14일)으로 대신 읽는다. 복약 체크는 모델이 고른 기간을
  따른다
- 정렬은 코드 몫이다 — 날 → 구획 → 읽은 순서
- 알레르기 · 만성질환 줄은 코드가 `safety_entries` 원문으로 붙인다. 조회 실패면 경고 줄이다
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from itertools import count
from uuid import UUID

import pytest

from app.agents.health.readouts import READOUTS
from app.agents.health.registry import TOOL_HANDLERS, execute_tool
from app.agents.health.result import ErrorCode
from app.agents.health.schemas.records import ComposeTimelineArgs, SearchHealthArgs
from app.agents.health.store.inmemory import (
    InMemoryHealthMemory,
    InMemoryMedicationStore,
    course_with_doses,
)
from app.agents.health.store.ports import (
    DoseSlot,
    HealthObservation,
    HealthSafetyEntry,
    MealRecord,
)
from app.agents.health.tools.records import search_health_observations
from app.agents.health.tools.timeline import compose_symptom_timeline
from app.agents.health.wording import (
    FLAGGED,
    FORBIDDEN_DISEASE_NAMES,
    FORBIDDEN_DOSE_GUESS,
    FORBIDDEN_INTERPRETATION,
    FORBIDDEN_SUFFIX,
)
from tests.unit.agents.health.support import KST, TODAY, context, gate

DAY = timedelta(days=1)
PATTERN = (DoseSlot.of("breakfast", 30), DoseSlot.of("lunch", 30), DoseSlot.of("dinner", 30))
EVENING_END = datetime(2026, 9, 28, 23, 0, tzinfo=KST)
PEANUT = HealthSafetyEntry("allergy", "땅콩", "active")
ASTHMA = HealthSafetyEntry("chronic_disease", "천식", "active")
_ids = count(9000)

# 열(어제) · 귀를 만져요 + 중이염이래요(오늘) · 해열제(어제 밤) · 미음(어제) ·
# 항생제 체크(그제~오늘)
EVERYTHING = [
    "9/26 항생제 5ml 체크 0/3회",
    "9/27 발열 · 정도: 심함",
    "9/27 21:00 해열제 먹였다고 기록하셨어요",
    "9/27 항생제 5ml 체크 1/3회",
    "9/27 미음 반 그릇",
    f"9/28 귀를 자꾸 만져요 / {FLAGGED}",
    "9/28 항생제 5ml 체크 0/3회",
]


def obs(day: date, *, symptoms=(), **kwargs) -> HealthObservation:
    return HealthObservation(
        id=UUID(int=next(_ids)),
        observed_from=day,
        observed_to=day,
        observed_time=kwargs.pop("at", None),
        symptoms=tuple(symptoms),
        **kwargs,
    )


class World:
    def __init__(self, *, entries=(), safety_ok=True, empty=False, extra_courses=()) -> None:
        self.fever = obs(TODAY - DAY, symptoms=("열나",), severity="severe")
        self.ear = obs(TODAY, symptoms=("귀를 자꾸 만져요", "중이염이래요"))
        self.given = obs(
            TODAY - DAY,
            medication_given="해열제",
            at=datetime(2026, 9, 27, 21, 0, tzinfo=KST),
            linked_symptom_id=self.fever.id,
        )
        self.meal = MealRecord(UUID(int=9900), TODAY - DAY, "미음", "반 그릇")
        self.course, doses = course_with_doses(
            "항생제",
            PATTERN,
            TODAY - 2 * DAY,
            TODAY,
            dosage="5ml",
            checked={(TODAY - DAY, time(8, 30))},
        )
        courses = [(self.course, doses), *extra_courses]
        memory = InMemoryHealthMemory(
            [] if empty else [self.fever, self.ear, self.given], [] if empty else [self.meal]
        )
        store = InMemoryMedicationStore(
            [] if empty else [c for c, _ in courses],
            [] if empty else [d for _, rows in courses for d in rows],
        )
        self.ctx = replace(context(memory=memory, medication=store), now=EVENING_END)
        self.ctx.state.gate = gate(48, safety_ok=safety_ok)
        self.ctx.state.safety_entries = tuple(entries)

    async def search(self, **args) -> "World":
        """모델이 증상 조회를 부른 것."""
        await search_health_observations(self.ctx, SearchHealthArgs(**args))
        return self


async def compose(world: World):
    return await compose_symptom_timeline(world.ctx, ComposeTimelineArgs())


def timeline_of(world: World):
    [readout] = [r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]
    return readout


def content(world: World) -> list[str]:
    """안전 줄을 뺀 정리 줄."""
    return timeline_of(world).body.split("\n")[:-2]


async def test_조회한_증상에_식사_복약을_코드가_더해_날짜_구획_순서로_낸다():
    world = await World().search()

    result = await compose(world)

    assert result.success and result.data == {"lines": 7}
    assert content(world) == EVERYTHING


async def test_모델이_증상_조회를_건너뛰어도_기본_기간으로_다_넣는다():
    # 라이브 eval 에서 모델이 조회 tool 을 건너뛴 일이 있었다 — 줄이 통째로 빠지지 않는다
    world = World()

    result = await compose(world)

    assert result.success
    assert content(world) == EVERYTHING
    assert world.ctx.state.visit_period == "14d"


async def test_증상을_좁혀도_식사_복약은_늘_들어간다():
    world = await World().search(symptom="열")

    await compose(world)

    assert content(world) == [line for line in EVERYTHING if "귀를" not in line]


async def test_복약_체크는_모델이_고른_기간을_따른다():
    old = course_with_doses("시럽", PATTERN, TODAY - 20 * DAY, TODAY - 18 * DAY)
    short = await World(extra_courses=[old]).search(period="14d")
    long = await World(extra_courses=[old]).search(period="30d")

    await compose(short)
    await compose(long)

    assert not any("시럽" in line for line in content(short))
    assert [line for line in content(long) if "시럽" in line] == [
        "9/8 시럽 체크 0/3회",
        "9/9 시럽 체크 0/3회",
        "9/10 시럽 체크 0/3회",
    ]


async def test_기록이_하나도_없으면_정리를_내지_않는다():
    world = World(empty=True)

    result = await compose(world)

    assert result.error is not None and result.error["code"] == ErrorCode.EVIDENCE_REQUIRED
    assert not [r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]


async def test_정리에는_해석_병_이름_안_먹였다는_말이_없다():
    # 기록 원문에 "중이염이래요" 가 있어도 줄에는 자리표시만 나간다
    world = await World().search()

    await compose(world)

    body = timeline_of(world).body
    assert not any(word in body.replace(" ", "") for word in FORBIDDEN_INTERPRETATION)
    assert not FORBIDDEN_SUFFIX.search(body)
    assert not any(name in body for name in FORBIDDEN_DISEASE_NAMES)
    assert not any(word in body for word in FORBIDDEN_DOSE_GUESS)


async def test_readout_은_근거를_단다():
    world = await World().search()

    await compose(world)

    readout = timeline_of(world)
    assert readout.authored_by == "model"
    assert {(ref.kind, ref.id) for ref in readout.source_refs} == {
        ("observation_health", world.fever.id),
        ("observation_health", world.ear.id),
        ("observation_health", world.given.id),
        ("observation_food", world.meal.id),
        ("medication_schedule", world.course.id),
    }


async def test_알레르기_줄은_코드가_붙인다():
    world = await World(entries=(PEANUT, ASTHMA)).search()

    await compose(world)

    lines = timeline_of(world).body.split("\n")
    # 타임라인 줄이 먼저, 안전 줄이 뒤다
    assert lines[-2:] == [
        READOUTS.render("card.allergy", items="땅콩").body,
        READOUTS.render("card.chronic", items="천식").body,
    ]


def test_모델은_정리에_아무것도_넘길_수_없다():
    # 줄 · key · 글을 넘길 칸이 없다
    for extra in ({"keys": ["s1"]}, {"lines": [{"text": "땅콩 알레르기"}]}):
        with pytest.raises(ValueError):
            ComposeTimelineArgs.model_validate(extra)


async def test_등록된_알레르기가_없으면_없다고_적지_않고_등록_없음으로_적는다():
    world = await World().search()

    await compose(world)

    body = timeline_of(world).body
    assert READOUTS.render("card.allergy", items="등록된 내용이 없어요").body in body


async def test_안전_조회_실패면_경고_줄():
    world = await World(safety_ok=False).search()

    await compose(world)

    lines = timeline_of(world).body.split("\n")
    # "아래 정리는 …" 라서 경고가 위, 그리고 "알레르기 없음" 같은 줄은 없다
    assert lines == [READOUTS.render("safety.unavailable").body, *EVERYTHING]


async def test_게이트가_없으면_조회_실패로_본다():
    # 읽었는지 모르는 상태를 "알레르기 없음" 으로 쓰지 않는다
    world = await World().search()
    world.ctx.state.gate = None

    await compose(world)

    assert READOUTS.render("safety.unavailable").body in timeline_of(world).body


async def test_다시_부르면_정리가_한_번만_남는다():
    world = await World().search()

    await compose(world)
    await compose(world)

    assert content(world) == EVERYTHING


async def test_조회한_원문은_state_repr_에_나오지_않는다():
    # 정리가 나가기 전까지 원문은 visit_lines 에만 있다 — repr(로그)에 싣지 않는다
    world = await World().search()

    text = repr(world.ctx.state)

    assert "항생제" not in text and "열나" not in text and "해열제" not in text


async def test_진료_준비_tool_이_아니면_이름으로_부를_수_없다():
    assert "compose_symptom_timeline" in TOOL_HANDLERS
    result = await execute_tool(
        "compose_symptom_timeline", {}, context(), allowed=("search_health_observations",)
    )

    assert result.error is not None and result.error["code"] == ErrorCode.TOOL_NOT_ALLOWED
