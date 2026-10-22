"""진료 준비 출력 tool `compose_symptom_timeline` 과 사후 검사 `review_lines`.

- 줄마다 ref 가 있고, 그 ref 는 이번 run 의 조회 tool 이 돌려준 id 여야 한다 — 아니면 줄을 지운다
- 약 이름 · 용량은 등록 원문과 글자 단위로 같다. 모델이 바꿔 적으면 tool 실패로 되돌려 고치게 한다
- 해석 금지어 · 체크 안 된 회차를 "안 먹였다" 로 쓴 줄도 tool 실패다
- 알레르기 · 만성질환 줄은 코드가 `safety_entries` 원문으로 붙인다. 조회 실패면 경고 줄이다
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from itertools import count
from uuid import UUID

import pytest

from app.agents.health import review as review_module
from app.agents.health.readouts import READOUTS
from app.agents.health.registry import TOOL_HANDLERS, execute_tool
from app.agents.health.result import ErrorCode
from app.agents.health.review import (
    FORBIDDEN_DISEASE_NAMES,
    FORBIDDEN_DOSE_GUESS,
    FORBIDDEN_INTERPRETATION,
    FORBIDDEN_SUFFIX,
    review_lines,
)
from app.agents.health.schemas.records import (
    ComposeTimelineArgs,
    SearchHealthArgs,
    SearchMealsArgs,
    SearchMedicationChecksArgs,
    TimelineLine,
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
    """조회 tool 을 한 번씩 부른 뒤의 context — `seen_refs` 가 채워진 상태."""

    def __init__(
        self, *, entries=(), safety_ok=True, with_meal=True, dosage="5ml", extra=()
    ) -> None:
        self.fever = obs(TODAY - DAY, symptoms=("열나",), severity="높음")
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
            dosage=dosage,
            checked={(TODAY - DAY, time(8, 30))},
        )
        memory = InMemoryHealthMemory(
            [self.fever, self.ear, self.given, *extra], [self.meal] if with_meal else []
        )
        base = context(memory=memory, medication=InMemoryMedicationStore([self.course], doses))
        self.ctx = replace(base, now=EVENING_END)
        self.ctx.state.gate = gate(48, safety_ok=safety_ok)
        self.ctx.state.safety_entries = tuple(entries)

    async def read_all(self) -> "World":
        await search_health_observations(self.ctx, SearchHealthArgs())
        await search_recent_meals(self.ctx, SearchMealsArgs())
        await search_medication_checks(self.ctx, SearchMedicationChecksArgs())
        return self


def line(section, text, ref, day: date = TODAY - DAY) -> TimelineLine:
    return TimelineLine(date=day, section=section, text=text, ref=str(ref))


async def compose(world: World, *lines: TimelineLine):
    return await compose_symptom_timeline(world.ctx, ComposeTimelineArgs(lines=list(lines)))


def timeline_of(world: World):
    [readout] = [r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]
    return readout


async def test_조회하지_않은_ref_는_지운다():
    world = await World().read_all()
    stranger = UUID(int=123456)

    result = await compose(
        world,
        line("symptom", "열나요 (높음)", world.fever.id),
        line("symptom", "배가 아파요", stranger),
    )

    # 남의 ref · 지어낸 ref 줄은 조용히 지워진다 — 실패가 아니라 나머지로 정리한다
    assert result.success
    body = timeline_of(world).body
    assert "열나요" in body
    assert "배가 아파요" not in body
    assert result.data["dropped"] == 1


async def test_ref_가_UUID_가_아니어도_지운다():
    world = await World().read_all()

    result = await compose(
        world,
        line("symptom", "열나요", world.fever.id),
        TimelineLine(date=TODAY, section="symptom", text="기침해요", ref="아무거나"),
    )

    assert result.success
    assert "기침해요" not in timeline_of(world).body


async def test_모든_줄이_지워지면_정리를_내지_않는다():
    world = await World().read_all()

    result = await compose(world, line("symptom", "배가 아파요", UUID(int=777)))

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.EVIDENCE_REQUIRED
    assert not [r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]


async def test_에피소드_묶음의_뒤_id_도_근거로_인정한다():
    # 에피소드 줄은 ref(첫 기록)와 refs(묶은 전부)를 같이 준다 — 둘 다 이번 run 이 본 id 다
    first = obs(TODAY - DAY, symptoms=("기침",))
    second = obs(TODAY, symptoms=("기침",))
    base = context(memory=InMemoryHealthMemory([first, second]))
    ctx = replace(base, now=EVENING_END)
    ctx.state.gate = gate(48)
    await search_health_observations(ctx, SearchHealthArgs())

    result = await compose_symptom_timeline(
        ctx,
        ComposeTimelineArgs(lines=[line("symptom", "기침이 이어졌어요", second.id, TODAY)]),
    )

    assert result.success


async def test_구획과_ref_종류가_다르면_지운다():
    # 식사 줄에 복약 코스 id 를 단 줄 — 원문 대조를 피해 가는 길이다
    world = await World().read_all()

    result = await compose(
        world,
        line("symptom", "열나요", world.fever.id),
        line("meal", "아목시실린 먹음", world.course.id),
    )

    assert result.success
    assert "아목시실린" not in timeline_of(world).body


async def test_약_이름이_원문과_다르면_거절():
    world = await World().read_all()

    result = await compose(
        world, line("medication", "10/14 아목시실린 체크 2/3회", world.course.id)
    )

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS
    assert not [r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]
    # 메시지에는 약 이름 원문을 싣지 않는다 — 로그로 흘러간다
    assert "항생제" not in result.error["message"]
    assert "아목시실린" not in result.error["message"]


async def test_약_용량이_원문과_다르면_거절():
    world = await World().read_all()

    result = await compose(world, line("medication", "항생제 10ml 체크 2/3회", world.course.id))

    assert result.success is False


async def test_약_이름과_용량이_원문과_같으면_통과():
    world = await World().read_all()

    result = await compose(world, line("medication", "항생제 5ml 체크 1/3회", world.course.id))

    assert result.success
    assert "항생제 5ml 체크 1/3회" in timeline_of(world).body


async def test_먹인_약_이름이_기록과_다르면_거절():
    world = await World().read_all()

    result = await compose(world, line("given", "타이레놀 먹임", world.given.id))

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS


async def test_먹인_약_이름이_기록과_같으면_통과():
    world = await World().read_all()

    result = await compose(world, line("given", "해열제 먹였다고 기록함 (21:00)", world.given.id))

    assert result.success


async def test_먹인_약을_증상_원인으로_이으면_거절():
    world = await World().read_all()

    result = await compose(world, line("given", "해열제 때문에 열이 내렸어요", world.given.id))

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS


@pytest.mark.parametrize(
    "text",
    [
        "10/14 항생제 1회 빼먹음",
        "항생제 못 먹임",
        "항생제 안 먹은 회차 있음",
        "항생제 66% 체크",
        "놓쳤어요 항생제",
    ],
)
async def test_체크_안_된_회차를_안_먹였다고_쓰면_거절(text):
    world = await World().read_all()

    result = await compose(world, line("medication", text, world.course.id))

    assert result.success is False


async def test_체크_수만_쓰면_통과():
    world = await World().read_all()

    result = await compose(world, line("medication", "9/27 항생제 체크 1/3회", world.course.id))

    assert result.success


@pytest.mark.parametrize("word", FORBIDDEN_INTERPRETATION)
async def test_해석_표현이_있으면_거절(word):
    world = await World().read_all()

    result = await compose(world, line("symptom", f"열이 나요 {word}", world.fever.id))

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS


async def test_모델이_증상_이름으로_질환을_붙이면_거절():
    # 모델이 말한 적 없는 병 이름 — ref 의 원문에 없다
    world = await World().read_all()

    result = await compose(world, line("symptom", "열이 나요 (장염)", world.fever.id))

    assert result.success is False


async def test_보호자가_말한_중이염_인용은_통과():
    world = await World().read_all()

    # 보호자 원문("중이염이래요")에 있는 말을 따옴표로 인용했다 — 해석이 아니라 전달이다
    result = await compose(world, line("symptom", "보호자 말: '중이염'", world.ear.id, TODAY))

    assert result.success


async def test_인용_예외는_그_ref_의_원문에_있을_때만():
    world = await World().read_all()

    # 열 기록에는 중이염이 없다 — 다른 줄의 원문으로 면제받지 못한다
    result = await compose(world, line("symptom", "보호자 말: '중이염'", world.fever.id))

    assert result.success is False


async def test_알레르기_줄은_코드가_붙인다():
    world = await World(entries=(PEANUT, ASTHMA)).read_all()

    result = await compose(world, line("symptom", "열나요 (높음)", world.fever.id))

    assert result.success
    body = timeline_of(world).body
    allergy = READOUTS.render("card.allergy", items="땅콩").body
    chronic = READOUTS.render("card.chronic", items="천식").body
    assert allergy in body and chronic in body
    # 타임라인 줄이 먼저, 안전 줄이 뒤다
    assert body.index("열나요") < body.index(allergy)


def test_모델이_쓴_알레르기_줄은_받지_않는다():
    # 등록된 것은 코드가 붙인다. 모델 줄에는 구획 자체가 없다
    with pytest.raises(ValueError):
        TimelineLine(date=TODAY, section="allergy", text="땅콩 알레르기", ref="x")


async def test_등록된_알레르기가_없으면_없다고_적지_않고_등록_없음으로_적는다():
    world = await World().read_all()

    await compose(world, line("symptom", "열나요", world.fever.id))

    body = timeline_of(world).body
    assert READOUTS.render("card.allergy", items="등록된 내용이 없어요").body in body


async def test_안전_조회_실패면_경고_줄():
    world = await World(entries=(), safety_ok=False).read_all()

    result = await compose(world, line("symptom", "열나요", world.fever.id))

    assert result.success
    body = timeline_of(world).body
    warning = READOUTS.render("safety.unavailable").body
    # "아래 정리는 …" 라서 경고가 위, 그리고 "알레르기 없음" 같은 줄은 없다
    assert body.startswith(warning)
    assert "알레르기 :" not in body


async def test_게이트가_없으면_조회_실패로_본다():
    # 읽었는지 모르는 상태를 "알레르기 없음" 으로 쓰지 않는다
    world = await World().read_all()
    world.ctx.state.gate = None

    await compose(world, line("symptom", "열나요", world.fever.id))

    assert READOUTS.render("safety.unavailable").body in timeline_of(world).body


async def test_줄은_날짜순으로_나간다():
    world = await World().read_all()

    await compose(
        world,
        line("symptom", "둘째 줄", world.ear.id, TODAY),
        line("symptom", "첫째 줄", world.fever.id, TODAY - DAY),
    )

    body = timeline_of(world).body
    assert body.index("첫째 줄") < body.index("둘째 줄")


async def test_readout_은_모델이_쓴_것이고_근거를_단다():
    world = await World().read_all()

    await compose(
        world,
        line("symptom", "열나요", world.fever.id),
        line("medication", "항생제 체크 1/3회", world.course.id),
    )

    readout = timeline_of(world)
    assert readout.authored_by == "model"
    assert {(ref.kind, ref.id) for ref in readout.source_refs} == {
        ("observation_health", world.fever.id),
        ("medication_schedule", world.course.id),
    }


async def test_통과_거절이_섞여도_거절이_있으면_아무것도_내지_않는다():
    world = await World().read_all()

    result = await compose(
        world,
        line("symptom", "열나요", world.fever.id),
        line("medication", "항생제 1회 빼먹음", world.course.id),
    )

    assert result.success is False
    assert not [r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]


async def test_고쳐서_다시_부르면_정리가_한_번만_남는다():
    world = await World().read_all()
    await compose(world, line("medication", "항생제 1회 빼먹음", world.course.id))

    await compose(world, line("medication", "항생제 체크 1/3회", world.course.id))
    await compose(world, line("medication", "항생제 체크 1/3회", world.course.id))

    assert len([r for r in world.ctx.state.readouts if r.kind == "symptom_timeline"]) == 1


async def test_거절_사유에는_줄_번호와_규칙만_있다():
    world = await World().read_all()
    world_ref = world.course.id

    result = await compose(
        world,
        line("symptom", "열나요", world.fever.id),
        line("medication", "10/14 아목시실린 1회 빼먹음", world_ref),
    )

    assert result.error is not None
    message = result.error["message"]
    assert "2번째 줄" in message
    assert "아목시실린" not in message and "항생제" not in message


def test_review_lines_는_통과한_줄과_사유를_돌려준다():
    world_ctx = context()
    good = TimelineLine(date=TODAY, section="symptom", text="기침", ref=str(UUID(int=1)))

    kept, reasons = review_lines([good], world_ctx)

    # 이 context 는 아무것도 조회하지 않았다 — 줄이 지워지고 사유가 남는다
    assert kept == []
    assert len(reasons) == 1


def test_약_줄_전용_금지어는_표에_있다():
    assert {"안 먹", "못 먹", "빼먹", "놓쳤", "%"} == set(FORBIDDEN_DOSE_GUESS)


async def test_타임라인은_아직_모델에_열리지_않는다():
    # 라벨 경로(IMPLEMENTED_TASKS)가 붙기 전에는 핸들러가 있어도 이름으로 부를 수 없다
    assert "compose_symptom_timeline" in TOOL_HANDLERS
    result = await execute_tool(
        "compose_symptom_timeline", {"lines": []}, context(), allowed=("search_recent_meals",)
    )

    assert result.error is not None and result.error["code"] == ErrorCode.TOOL_NOT_ALLOWED


# ── Fix round 1 ──


@pytest.mark.parametrize(
    "text",
    [
        "장염이에요",
        "장염으로 보여요",
        "중이염이래요",
        "폐렴이에요",
        "염증이 있어요",
        "탈수증이에요",
        "열이 나요 (장염)",
    ],
)
async def test_조사가_붙은_병_이름도_거절(text):
    world = await World().read_all()

    result = await compose(world, line("symptom", text, world.fever.id))

    assert result.success is False


@pytest.mark.parametrize(
    "text",
    [
        "증상",
        "증상이 이어졌어요",
        "체중 증가",
        "머리 염색",
        "확인했어요",
        "염려돼요",
        "저렴해요",
        "증세 없음",
    ],
)
async def test_병_이름이_아닌_말은_통과(text):
    world = await World().read_all()

    result = await compose(world, line("symptom", text, world.fever.id))

    assert result.success


async def test_보호자가_조사까지_붙여_말한_병_이름은_그_말만_통과():
    world = await World().read_all()

    ok_result = await compose(
        world, line("symptom", "보호자 말: 중이염이래요", world.ear.id, TODAY)
    )
    other = await compose(world, line("symptom", "보호자 말: 장염이래요", world.ear.id, TODAY))

    assert ok_result.success
    assert other.success is False


async def test_먹인_약_행의_ref_를_증상_줄에_달면_지운다():
    world = await World().read_all()

    result = await compose(
        world,
        line("symptom", "열나요", world.fever.id),
        line("symptom", "타이레놀 10ml 먹이고 열 내림", world.given.id),
    )

    assert result.success
    assert "타이레놀" not in timeline_of(world).body
    assert result.data["dropped"] == 1


@pytest.mark.parametrize("section", ["symptom", "meal"])
async def test_증상_식사_줄도_빼먹었다는_말은_거절(section):
    world = await World().read_all()
    ref = world.fever.id if section == "symptom" else world.meal.id

    result = await compose(world, line(section, "항생제 1회 빼먹음", ref))

    assert result.success is False


async def test_증상_줄의_용량은_그_줄_원문에_있어야():
    world = await World().read_all()

    result = await compose(world, line("symptom", "열나요 5ml", world.fever.id))

    assert result.success is False


async def test_증상_줄에_다른_기록의_약_이름이_있으면_거절():
    world = await World().read_all()

    result = await compose(world, line("symptom", "열나서 항생제 먹임", world.fever.id))

    assert result.success is False


async def test_식사_줄은_못_먹음_과_퍼센트를_쓸_수_있다():
    world = await World().read_all()

    result = await compose(
        world,
        line("meal", "밥은 못 먹음", world.meal.id),
        line("meal", "미음 50% 먹음", world.meal.id),
        line("meal", "미음 반 그릇", world.meal.id),
    )

    assert result.success


async def test_복약_줄에_다른_약_이름이_있으면_거절():
    world = await World().read_all()

    result = await compose(world, line("medication", "항생제 해열제 체크 1/3회", world.course.id))

    assert result.success is False


async def test_체크_수가_데이터와_같으면_통과():
    world = await World().read_all()

    result = await compose(world, line("medication", "9/27 항생제 체크 1/3회", world.course.id))

    assert result.success


async def test_체크_수가_데이터와_다르면_거절():
    world = await World().read_all()

    result = await compose(world, line("medication", "9/27 항생제 체크 3/3회", world.course.id))

    assert result.success is False


async def test_글의_날짜가_줄_날짜와_다르면_거절():
    world = await World().read_all()

    result = await compose(world, line("medication", "9/26 항생제 체크 1/3회", world.course.id))

    assert result.success is False


async def test_조회하지_않은_날의_체크_수는_거절():
    world = await World().read_all()

    result = await compose(
        world, line("medication", "항생제 체크 1/3회", world.course.id, TODAY - 9 * DAY)
    )

    assert result.success is False


async def test_체크_수를_쓰지_않은_복약_줄은_통과():
    world = await World().read_all()

    result = await compose(world, line("medication", "항생제 5ml", world.course.id))

    assert result.success


async def test_원문은_state_repr_에_나오지_않는다():
    world = await World().read_all()

    text = repr(world.ctx.state)

    assert "항생제" not in text and "열나" not in text and "해열제" not in text


# ── 최종 리뷰 수정 ──
# F3 — 용량은 낱개로 견준다. 부분 문자열이면 "2.5ml" 원문에 "5ml" 이 숨는다


@pytest.mark.parametrize(
    "dosage, text",
    [
        ("2.5ml", "항생제 5ml 체크 1/3회"),
        ("15ml", "항생제 5ml 체크 1/3회"),
        ("12.5ml", "항생제 2.5ml 체크 1/3회"),
        ("12.5ml", "항생제 2.5 ml"),
    ],
)
async def test_용량이_원문_용량의_일부분이면_거절(dosage, text):
    world = await World(dosage=dosage).read_all()

    result = await compose(world, line("medication", text, world.course.id))

    assert result.success is False


@pytest.mark.parametrize(
    "dosage, text",
    [
        ("2.5ml", "항생제 2.5ml 체크 1/3회"),
        ("12.5ml", "항생제 12.5 ml"),
        ("15ml", "항생제 15ml"),
        ("5ML", "항생제 5ml"),
        # 공백을 먼저 지우면 날짜가 용량에 붙어 "275ml" 로 읽힌다
        ("5ml", "9/27 5ml 항생제 체크 1/3회"),
    ],
)
async def test_원문과_같은_용량은_통과(dosage, text):
    world = await World(dosage=dosage).read_all()

    result = await compose(world, line("medication", text, world.course.id))

    assert result.success


# F4 — ~염 · ~렴 은 뒤에 무엇이 붙든 잡는다(소염제 · 해열제 같은 '~제' 만 뺀다)
_DISEASE_BYPASSES = (
    "장염의 증상",
    "장염이었던 기록",
    "장염인가요",
    "장염이랍니다",
    "장염같아",
    "편도선염일수도",
    "결막염임",
)


@pytest.mark.parametrize("text", _DISEASE_BYPASSES)
def test_조사_목록_밖의_꼬리가_붙은_병_이름도_잡는다(text):
    match = FORBIDDEN_SUFFIX.search(text)

    assert match is not None
    assert match.group(1) in {"장염", "편도선염", "결막염"}


@pytest.mark.parametrize("text", _DISEASE_BYPASSES)
async def test_조사_목록_밖의_꼬리가_붙은_병_이름은_거절(text):
    world = await World().read_all()

    result = await compose(world, line("symptom", f"열나요 {text}", world.fever.id))

    assert result.success is False


@pytest.mark.parametrize(
    "text",
    ["소염제", "해열제", "기침 증상", "콧물증상", "저렴해요", "염려돼요", "머리 염색", "증세 없음"],
)
def test_병_이름이_아닌_염_렴_증은_잡지_않는다(text):
    assert FORBIDDEN_SUFFIX.search(text) is None


@pytest.mark.parametrize("text", ["기침 증상", "콧물증상", "소염제 연고 바름"])
async def test_약_이름_증상_합성어는_통과(text):
    world = await World().read_all()

    result = await compose(world, line("symptom", text, world.fever.id))

    assert result.success


# F7 — 추측 표현 · 병 이름(감기 · 독감 …)도 거절. 병 이름은 그 줄 ref 원문에 있으면 인용이다
@pytest.mark.parametrize(
    "text",
    [
        "감기 같음",
        "독감인 듯",
        "수족구로 보임",
        "이가 나서 그런 것 같습니다",
        "탈수 우려",
        "열이 나요 (원인 모름)",
        "열나요 - 이앓이 탓",
        "열나요 (추정)",
        "더위로 인해 열남",
        "열이 나는 것 같아",
    ],
)
async def test_추측_표현과_병_이름은_거절(text):
    world = await World().read_all()

    result = await compose(world, line("symptom", text, world.fever.id))

    assert result.success is False


@pytest.mark.parametrize(
    "text",
    [
        "따뜻한 물 마심",
        "열나요 (높음) · 확인해 주세요",
        "어제와 비슷해요",
        "미지근한 물로 닦아 줌",
    ],
)
async def test_평범한_말은_추측_표현으로_보지_않는다(text):
    world = await World().read_all()

    result = await compose(world, line("symptom", text, world.fever.id))

    assert result.success, result.error


async def test_보호자가_말한_병_이름은_그_줄_원문에_있으면_인용으로_통과():
    cold = obs(TODAY, symptoms=("콧물", "감기래요"))
    world = await World(extra=(cold,)).read_all()

    quoted = await compose(world, line("symptom", "보호자 말: 감기", cold.id, TODAY))
    other = await compose(world, line("symptom", "보호자 말: 감기", world.fever.id))

    assert quoted.success
    assert other.success is False


def test_병_이름_표는_정해진_것이다():
    assert set(FORBIDDEN_DISEASE_NAMES) == {
        "감기",
        "독감",
        "수족구",
        "수두",
        "홍역",
        "코로나",
        "바이러스",
        "세균",
        "탈수",
    }


# F5 — n/m회 대조를 띄어쓰기 · 다른 꼴로 피해 가지 못한다 (9/27 데이터는 1/3회)
@pytest.mark.parametrize(
    "text",
    [
        "9/27 항생제 체크 3 / 3회",
        "9/27 항생제 체크 3/3 회",
        "9/27 항생제 3회 중 3회",
        "9/27 항생제 체크 3회",
    ],
)
async def test_체크_수를_다른_꼴로_써도_대조한다(text):
    world = await World().read_all()

    result = await compose(world, line("medication", text, world.course.id))

    assert result.success is False


@pytest.mark.parametrize("text", ["9/27 항생제 체크 1 / 3회", "9/27 항생제 체크 1/3 회"])
async def test_체크_수를_띄어_써도_같으면_통과(text):
    world = await World().read_all()

    result = await compose(world, line("medication", text, world.course.id))

    assert result.success, result.error


async def test_등록_원문에_있는_n회_는_체크_수가_아니다():
    world = await World(dosage="1회 5ml").read_all()

    result = await compose(world, line("medication", "항생제 1회 5ml 체크 1/3회", world.course.id))

    assert result.success, result.error


# F6 — 줄의 날짜는 그 ref 기록의 날짜 범위 안이어야 한다 (날짜는 코드 몫)
@pytest.mark.parametrize(
    "section, attr",
    [("symptom", "fever"), ("meal", "meal"), ("given", "given")],
)
async def test_기록_날짜_밖의_줄은_거절(section, attr):
    world = await World().read_all()
    ref = getattr(world, attr).id
    text = {"symptom": "열나요", "meal": "미음 반 그릇", "given": "해열제 먹였다고 기록"}[section]

    result = await compose(world, line(section, text, ref, TODAY - 10 * DAY))

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.INVALID_ARGS
    assert "날짜" in result.error["message"]


async def test_에피소드_기간_안의_날짜는_통과():
    first = obs(TODAY - DAY, symptoms=("기침",))
    second = obs(TODAY, symptoms=("기침",))
    base = context(memory=InMemoryHealthMemory([first, second]))
    ctx = replace(base, now=EVENING_END)
    ctx.state.gate = gate(48)
    await search_health_observations(ctx, SearchHealthArgs())

    for day in (TODAY - DAY, TODAY):
        result = await compose_symptom_timeline(
            ctx, ComposeTimelineArgs(lines=[line("symptom", "기침", first.id, day)])
        )
        assert result.success, day

    outside = await compose_symptom_timeline(
        ctx, ComposeTimelineArgs(lines=[line("symptom", "기침", first.id, TODAY - 2 * DAY)])
    )
    assert outside.success is False


# ── 최종 리뷰 Patch 2 ──
# 분수 용량 — "1/2정" 원문이 {"2정"} 으로 읽히면 "2정"(4배) 줄이 통과한다
@pytest.mark.parametrize(
    "dosage, text",
    [("1/2정", "항생제 2정"), ("1/2포", "항생제 2포"), ("1/4정", "항생제 1/2정")],
)
async def test_분수_용량의_일부만_쓰면_거절(dosage, text):
    world = await World(dosage=dosage).read_all()

    result = await compose(world, line("medication", text, world.course.id))

    assert result.success is False


@pytest.mark.parametrize("dosage, text", [("1/2포", "항생제 1/2포"), ("1/2정", "항생제 1/2 정")])
async def test_분수_용량이_원문과_같으면_통과(dosage, text):
    world = await World(dosage=dosage).read_all()

    result = await compose(world, line("medication", text, world.course.id))

    assert result.success, result.error


@pytest.mark.parametrize(
    "text, doses",
    [
        ("9/27 5ml", {"5ml"}),
        ("10/14 1/2정", {"1/2정"}),
        ("12.5 ml", {"12.5ml"}),
        ("2.5ml", {"2.5ml"}),
    ],
)
def test_용량은_날짜를_붙이지_않고_낱개로_뽑는다(text, doses):
    assert review_module._doses(text) == doses  # noqa: SLF001


# "번" 으로 써도 체크 수를 대조한다 (9/27 데이터는 1/3)
@pytest.mark.parametrize(
    "text", ["9/27 항생제 체크 3/3번", "9/27 항생제 체크 3번", "9/27 항생제 체크 3 / 3 번"]
)
async def test_번_으로_쓴_체크_수도_대조한다(text):
    world = await World().read_all()

    result = await compose(world, line("medication", text, world.course.id))

    assert result.success is False


async def test_번_으로_쓴_체크_수가_같으면_통과():
    world = await World().read_all()

    result = await compose(world, line("medication", "9/27 항생제 체크 1/3번", world.course.id))

    assert result.success, result.error


# 아기 음식의 ~염 (저염 · 무염 · 식염 · 흑염) 은 병 이름이 아니다
_SALT_FOODS = ("저염치즈", "무염버터", "무염김", "흑염소", "식염수", "생리식염수")


@pytest.mark.parametrize("text", _SALT_FOODS)
def test_저염_무염_식염_흑염_음식은_병_이름이_아니다(text):
    assert FORBIDDEN_SUFFIX.search(text) is None


@pytest.mark.parametrize("text", _SALT_FOODS)
async def test_저염_음식_식사_줄은_통과(text):
    world = await World().read_all()

    result = await compose(world, line("meal", f"{text} 조금", world.meal.id))

    assert result.success, result.error


@pytest.mark.parametrize(
    "text, name",
    [
        ("장염의 증상", "장염"),
        ("장염이었던 듯", "장염"),
        ("장염인가요", "장염"),
        ("장염이랍니다", "장염"),
        ("장염같아", "장염"),
        ("편도선염일수도", "편도선염"),
        ("결막염임", "결막염"),
        ("중이염이래요", "중이염"),
        ("폐렴으로", "폐렴"),
        ("염증", "염증"),
        ("비염", "비염"),
        ("후두염", "후두염"),
        ("식도염", "식도염"),
        ("뇌수막염", "뇌수막염"),
    ],
)
def test_음식_예외_뒤에도_병_이름은_그대로_잡는다(text, name):
    match = FORBIDDEN_SUFFIX.search(text)

    assert match is not None and match.group(1) == name


@pytest.mark.parametrize("text", ["저렴해요", "소염제", "증상", "키위", "해열제", "콧물증상"])
def test_음식_예외_뒤에도_음성은_그대로(text):
    assert FORBIDDEN_SUFFIX.search(text) is None


@pytest.mark.parametrize("section, attr", [("symptom", "fever"), ("meal", "meal")])
async def test_글머리_날짜는_모든_구획에서_줄_날짜와_같아야(section, attr):
    world = await World().read_all()
    ref = getattr(world, attr).id

    result = await compose(world, line(section, "9/20 미음 반 그릇", ref))

    assert result.success is False
