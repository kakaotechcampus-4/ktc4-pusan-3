"""진료 준비 출력 tool `compose_symptom_timeline`.

- 🚨 모델은 줄의 key 만 고른다 (#551). 줄의 글은 조회 tool 이 기록에서 그린 것이다 — 모델이 문장을
  쓸 자리가 없어서 해석 표현 · 바꿔 적은 약 이름 · 용량이 나갈 길이 없다
- 이번 run 이 조회하지 않은 key 는 지운다. 남는 줄이 없으면 정리를 내지 않는다
- 정렬은 코드 몫이다 — 날 → 구획 → 조회한 순서
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
from app.agents.health.schemas.records import (
    MAX_KEYS,
    ComposeTimelineArgs,
    SearchHealthArgs,
    SearchMealsArgs,
    SearchMedicationChecksArgs,
)
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
from app.agents.health.tools.records import (
    search_health_observations,
    search_medication_checks,
    search_recent_meals,
)
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
    """조회 tool 셋을 한 번씩 부른 뒤의 context — `visit_lines` 가 채워진 상태.

    key 는 s1 열(어제) · s2 귀를 만져요 + 중이염이래요(오늘, 사전에 없는 말) · g1 해열제(어제 밤) ·
    f1 미음(어제) · c1~c3 항생제 체크(그제 · 어제 · 오늘) 다.
    """

    def __init__(self, *, entries=(), safety_ok=True) -> None:
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
        memory = InMemoryHealthMemory([self.fever, self.ear, self.given], [self.meal])
        base = context(memory=memory, medication=InMemoryMedicationStore([self.course], doses))
        self.ctx = replace(base, now=EVENING_END)
        self.ctx.state.gate = gate(48, safety_ok=safety_ok)
        self.ctx.state.safety_entries = tuple(entries)

    async def read_all(self) -> "World":
        await search_health_observations(self.ctx, SearchHealthArgs())
        await search_recent_meals(self.ctx, SearchMealsArgs())
        await search_medication_checks(self.ctx, SearchMedicationChecksArgs())
        return self


async def compose(world: World, *keys: str):
    return await compose_symptom_timeline(world.ctx, ComposeTimelineArgs(keys=list(keys)))


def timeline_of(world: World):
    [readout] = [r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]
    return readout


def content(world: World) -> list[str]:
    """안전 줄을 뺀 정리 줄."""
    return timeline_of(world).body.split("\n")[:-2]


async def test_조회한_줄을_날짜_구획_순서로_낸다():
    world = await World().read_all()

    result = await compose(world, "c3", "f1", "s2", "c2", "g1", "s1", "c1")

    assert result.success and result.data == {"lines": 7, "dropped": 0}
    assert content(world) == [
        "9/26 항생제 5ml 체크 0/3회",
        "9/27 발열 · 정도: 심함",
        "9/27 21:00 해열제 먹였다고 기록하셨어요",
        "9/27 항생제 5ml 체크 1/3회",
        "9/27 미음 반 그릇",
        f"9/28 귀를 자꾸 만져요 / {FLAGGED}",
        "9/28 항생제 5ml 체크 0/3회",
    ]


async def test_조회하지_않은_key_는_지운다():
    world = await World().read_all()

    result = await compose(world, "s1", "s99", "x")

    # 지어낸 key 줄은 조용히 지워진다 — 실패가 아니라 나머지로 정리한다
    assert result.success and result.data == {"lines": 1, "dropped": 2}
    assert content(world) == ["9/27 발열 · 정도: 심함"]


async def test_남는_줄이_없으면_정리를_내지_않는다():
    world = await World().read_all()

    result = await compose(world, "s99")

    assert result.error is not None and result.error["code"] == ErrorCode.EVIDENCE_REQUIRED
    assert not [r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]


async def test_조회하기_전에는_어떤_key_도_근거가_아니다():
    world = World()  # 조회 tool 을 부르지 않았다

    result = await compose(world, "s1")

    assert result.error is not None and result.error["code"] == ErrorCode.EVIDENCE_REQUIRED


async def test_같은_key_를_두_번_넣어도_한_줄():
    world = await World().read_all()

    result = await compose(world, "s1", "s1")

    assert result.data == {"lines": 1, "dropped": 0}
    assert content(world) == ["9/27 발열 · 정도: 심함"]


async def test_정리에는_해석_병_이름_안_먹였다는_말이_없다():
    # 기록 원문에 "중이염이래요" 가 있어도 줄에는 자리표시만 나간다
    world = await World().read_all()

    await compose(world, "s1", "s2", "g1", "f1", "c1", "c2", "c3")

    body = timeline_of(world).body
    assert not any(word in body.replace(" ", "") for word in FORBIDDEN_INTERPRETATION)
    assert not FORBIDDEN_SUFFIX.search(body)
    assert not any(name in body for name in FORBIDDEN_DISEASE_NAMES)
    assert not any(word in body for word in FORBIDDEN_DOSE_GUESS)


async def test_readout_은_모델이_고른_것이고_근거를_단다():
    world = await World().read_all()

    await compose(world, "s1", "g1", "c2")

    readout = timeline_of(world)
    assert readout.authored_by == "model"
    assert {(ref.kind, ref.id) for ref in readout.source_refs} == {
        ("observation_health", world.fever.id),
        ("observation_health", world.given.id),
        ("medication_schedule", world.course.id),
    }


async def test_알레르기_줄은_코드가_붙인다():
    world = await World(entries=(PEANUT, ASTHMA)).read_all()

    result = await compose(world, "s1")

    assert result.success
    lines = timeline_of(world).body.split("\n")
    allergy = READOUTS.render("card.allergy", items="땅콩").body
    chronic = READOUTS.render("card.chronic", items="천식").body
    # 타임라인 줄이 먼저, 안전 줄이 뒤다
    assert lines == ["9/27 발열 · 정도: 심함", allergy, chronic]


def test_모델은_줄의_글을_넘길_수_없다():
    # 출력 tool 은 key 목록만 받는다 — 글 · 구획 · 날짜를 넘길 칸이 없다
    with pytest.raises(ValueError):
        ComposeTimelineArgs.model_validate(
            {"keys": ["s1"], "lines": [{"text": "땅콩 알레르기", "ref": "x"}]}
        )


@pytest.mark.parametrize("keys", [[], [f"s{n}" for n in range(MAX_KEYS + 1)]])
def test_key_는_하나_이상_상한_이하(keys):
    with pytest.raises(ValueError):
        ComposeTimelineArgs(keys=keys)


async def test_등록된_알레르기가_없으면_없다고_적지_않고_등록_없음으로_적는다():
    world = await World().read_all()

    await compose(world, "s1")

    body = timeline_of(world).body
    assert READOUTS.render("card.allergy", items="등록된 내용이 없어요").body in body


async def test_안전_조회_실패면_경고_줄():
    world = await World(entries=(), safety_ok=False).read_all()

    result = await compose(world, "s1")

    assert result.success
    body = timeline_of(world).body
    warning = READOUTS.render("safety.unavailable").body
    # "아래 정리는 …" 라서 경고가 위, 그리고 "알레르기 없음" 같은 줄은 없다
    assert body.split("\n") == [warning, "9/27 발열 · 정도: 심함"]


async def test_게이트가_없으면_조회_실패로_본다():
    # 읽었는지 모르는 상태를 "알레르기 없음" 으로 쓰지 않는다
    world = await World().read_all()
    world.ctx.state.gate = None

    await compose(world, "s1")

    assert READOUTS.render("safety.unavailable").body in timeline_of(world).body


async def test_다시_부르면_정리가_한_번만_남는다():
    world = await World().read_all()

    await compose(world, "s1")
    await compose(world, "s1", "g1")

    assert content(world) == ["9/27 발열 · 정도: 심함", "9/27 21:00 해열제 먹였다고 기록하셨어요"]


async def test_원문은_state_repr_에_나오지_않는다():
    world = await World().read_all()

    text = repr(world.ctx.state)

    assert "항생제" not in text and "열나" not in text and "해열제" not in text


async def test_진료_준비_tool_이_아니면_이름으로_부를_수_없다():
    assert "compose_symptom_timeline" in TOOL_HANDLERS
    result = await execute_tool(
        "compose_symptom_timeline", {"keys": ["s1"]}, context(), allowed=("search_recent_meals",)
    )

    assert result.error is not None and result.error["code"] == ErrorCode.TOOL_NOT_ALLOWED
