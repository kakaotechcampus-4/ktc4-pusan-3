"""진료 준비 · 열 타임라인 · 먹인 약 eval 케이스를 모델 없이 돌려 판정이 맞는지 본다.

`make test` 에서 돈다.

라이브 eval(`tests/eval/agents/health/test_health_run.py -m live`)은 실제 모델이 필요하다.
여기서는 같은 실행기(`runner.run_case`)로 케이스를 돌리되 모델 자리에 가짜를 넣는다.

    - 열 타임라인 · 기록 없음은 모델 0회 경로라 가짜가 불리면 안 된다
    - 정리(T23 · T24)는 조회 셋을 부르고 돌려받은 key 를 전부 넘기는 "말 잘 듣는" 가짜다 — 줄의
      글은 코드가 그린다 (#551)
    - 먹인 약(T21-memory · T21-linked)은 Memory 저장 tool 을 한 번 부르는 가짜다
    - 판정이 어긋난 결과("안 먹였", 비율, 바뀐 약 이름, 응급 안내 뒤로 밀림, 기록 0건에 모델 호출)를
      실패로 잡는지 본다
"""

import json
import re
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.agents.common.llm_client import LLMResponse
from app.agents.common.readout import Readout
from app.agents.common.refs import Ref
from app.agents.health.readouts import EMERGENCY, READOUTS, VISIT_NO_RECORD
from app.agents.health.schemas.task import HealthTaskType
from app.agents.memory.store.ports import ObservationRow
from tests.eval.agents.health import judges
from tests.eval.agents.health.cases import CASES
from tests.eval.agents.health.judges import (
    Ran,
    common,
    fever_timeline,
    memory_given,
    visit_no_record,
    visit_timeline,
)
from tests.eval.agents.health.runner import run_case
from tests.eval.agents.health.world import DAY, KST, WORLDS, build_world
from tests.unit.agents.health.support import FakeLLM, call

BY_ID = {case.case_id: case for case in CASES}
VISIT = [case for case in CASES if case.label is HealthTaskType.VISIT_SUMMARY]
MEMORY = [case for case in CASES if case.memory]


class ComposingLLM:
    """증상 조회 하나를 부르고 이어서 인자 없이 `compose_symptom_timeline` 을 부르는 가짜.

    보호자가 범위를 좁히지 않았을 때의 모델이다. 줄 · 식사 · 복약 체크는 코드가 넣는다 (#551 리뷰).
    `seen` 은 부른 횟수다.
    """

    def __init__(self) -> None:
        self.seen: list[list[dict]] = []
        self._step = 0

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.seen.append([dict(m) for m in messages])
        self._step += 1
        if self._step == 1:
            return call("search_health_observations", {"period": "14d"})
        if self._step == 2:
            return call("compose_symptom_timeline", {})
        return LLMResponse(
            message=SimpleNamespace(content="끝", tool_calls=None), usage={}, latency_ms=1
        )


# 먹인 약 — Memory 가 한 번 부르는 저장 tool. 원문에 있는 말만 넣는다
MEMORY_SCRIPTS = {
    "T21-memory": {
        "raw_text": "새벽 3시에 해열제 먹였어",
        "observed_on": "오늘",
        "medication_given": "해열제",
        "given_time": "새벽 3시",
    },
    "T21-linked": {
        "raw_text": "열이 끓길래 바로 해열제 먹였어",
        "observed_on": "오늘",
        "symptom": ["발열"],
        "medication_given": "해열제",
    },
}


def _memory_done() -> LLMResponse:
    """Memory 의 마무리 말 — 응답 스키마(JSON) 그대로."""
    reply = {"pending_hint": None, "leftover": False, "text": "기록해 둘게요.", "kind": "message"}
    return LLMResponse(
        message=SimpleNamespace(content=json.dumps(reply, ensure_ascii=False), tool_calls=None),
        usage={},
        latency_ms=1,
    )


def llm_for(case_id: str):
    if case_id in MEMORY_SCRIPTS:
        return FakeLLM(call("create_observation_health", MEMORY_SCRIPTS[case_id]), _memory_done())
    if case_id == "T22-model":
        # 열 표현 표에 안 걸린 열 질문 — 모델이 정리 대신 열 타임라인 출구를 고른다
        return FakeLLM(call("show_fever_timeline", {}))
    case = BY_ID[case_id]
    if case.judge.__qualname__.startswith("visit_timeline."):
        return ComposingLLM()
    return FakeLLM()  # 모델 0회 경로 — 불리면 판정이 잡는다


async def _ran(case_id: str) -> Ran:
    return await run_case(BY_ID[case_id], client=llm_for(case_id))


def _with_bodies(ran: Ran, *bodies: str, kind: str | None = None) -> Ran:
    """결과의 readout 글을 바꿔 끼운다. ref 는 원래 것을 그대로 둔다."""
    old = ran.result.readouts
    readouts = tuple(
        replace(readout, body=body, kind=kind or readout.kind)
        for readout, body in zip(old, bodies, strict=True)
    )
    return replace(ran, result=replace(ran.result, readouts=readouts))


# ── 세계 ──
def test_C48_fever_는_48시간_체온_네_건과_해열제_코스와_먹인_약이_있다():
    world = build_world("C48-fever")
    start = DAY - timedelta(hours=48)
    readings = [o for o in world.spec.observations if o.temperature is not None]
    given = [o for o in world.spec.observations if o.medication_given is not None]

    assert len([o for o in readings if o.measured_at >= start]) == 4
    assert [c.title for c in world.courses] == ["항생제", "해열제"]
    reducer = world.courses[1]
    rows = [d for d in world.doses if d.schedule_id == reducer.id]
    per_day = {d.dose_date for d in rows}
    assert all(len([d for d in rows if d.dose_date == day]) == 3 for day in per_day)
    checked = [
        d
        for d in rows
        if d.checked_at is not None
        and datetime.combine(d.dose_date, d.scheduled_time, tzinfo=KST) >= start
    ]
    assert len(checked) == 2
    [row] = given
    assert row.symptoms == () and row.linked_symptom_id in {o.id for o in readings}
    assert world.spec.pending == ""


def test_C48_fever_의_항생제는_C48_의_코스_그대로다():
    fever, base = build_world("C48-fever"), build_world("C48")
    antibiotic = [d for d in fever.doses if d.schedule_id == fever.courses[0].id]
    original = [d for d in base.doses if d.schedule_id == base.courses[0].id]

    assert [(d.dose_date, d.scheduled_time, d.checked_at) for d in antibiotic] == [
        (d.dose_date, d.scheduled_time, d.checked_at) for d in original
    ]
    # 저녁 회차는 19:30 이다(#544 의 저녁 기준 19:00 + 30분)
    assert time(19, 30) in {d.scheduled_time for d in antibiotic}


def test_C2_는_2개월_38_2도_한_건이다():
    spec = WORLDS["C2"]

    assert spec.months == 2
    assert [o.temperature for o in spec.observations] == [Decimal("38.2")]


def test_C48_norecord_는_30일_안에_기록도_복약_코스도_없다():
    # 기록 없음 판정은 조회 tool 이 받는 가장 긴 기간(30일)이고 복약 코스도 건강 기록이다
    spec = WORLDS["C48-norecord"]
    first = (DAY - timedelta(days=29)).date()

    assert spec.observations and all(o.observed_to < first for o in spec.observations)
    assert spec.courses == ()
    assert spec.pending == ""


def test_C48_에는_최근_3일_식사와_그보다_오래된_식사가_있다():
    meals = WORLDS["C48"].meals
    today = DAY.date()

    assert len([m for m in meals if (today - m.observed_on).days < 3]) >= 2
    assert any((today - m.observed_on).days >= 3 for m in meals)


# ── 케이스 표 ──
def test_진료_준비_케이스는_전부_판정이_있다():
    ids = {case.case_id for case in VISIT}

    assert {
        "T22",
        "T22-notemp",
        "T22-C2",
        "T22-model",
        "T23",
        "T24",
        "T24-noconsent",
        "T24-safetyfail",
        "T24-norecord",
    } <= ids
    for case in VISIT:
        assert "pending" not in case.judge.__qualname__, case.case_id


def test_먹인_약_케이스는_Memory_저장을_본다():
    assert [case.case_id for case in MEMORY] == ["T21-memory", "T21-linked"]
    assert BY_ID["T21-memory"].text is None  # 문서 입력 그대로
    assert BY_ID["T21-linked"].text == "열이 끓길래 바로 해열제 먹였어"
    for case in MEMORY:
        assert case.label is None and case.judge.__qualname__.startswith("memory_given.")


# ── 케이스가 판정을 통과한다 ──
@pytest.mark.parametrize("case", [*VISIT, *MEMORY], ids=[c.case_id for c in [*VISIT, *MEMORY]])
async def test_케이스가_판정을_통과한다(case):
    llm = llm_for(case.case_id)

    ran = await run_case(case, client=llm)
    verdict = common(ran).extend(case.judge(ran))

    assert verdict.failures == [], verdict.failures
    assert verdict.gaps == []


async def test_열_타임라인과_기록_없음은_모델을_부르지_않는다():
    for case_id in ("T22", "T22-notemp", "T22-C2", "T24-norecord"):
        llm = llm_for(case_id)
        await run_case(BY_ID[case_id], client=llm)

        assert llm.seen == [], case_id


async def test_표에_안_걸린_열_질문은_모델_한_번이고_T22_와_같은_줄이다():
    ran = await _ran("T22-model")
    base = await _ran("T22")

    assert ran.result.model_calls == 1
    assert [(r.kind, r.body) for r in ran.result.readouts] == [
        (r.kind, r.body) for r in base.result.readouts
    ]
    assert fever_timeline(model_calls=1)(ran).failures == []
    assert fever_timeline()(ran).failures  # 기본은 모델 0회를 기대한다


async def test_열_질문에_정리가_섞이면_실패다():
    ran = await _ran("T22-model")
    mixed = replace(
        ran.result,
        readouts=(
            *ran.result.readouts,
            Readout(kind="symptom_timeline", body="정리", authored_by="model"),
        ),
    )

    assert fever_timeline(model_calls=1)(replace(ran, result=mixed)).failures


async def test_정리는_모델_한_번이다():
    llm = llm_for("T24")

    ran = await run_case(BY_ID["T24"], client=llm)

    assert ran.result.model_calls == 1


async def test_T22_는_체온_먹인_약_마지막_체크_다음_예정을_나열한다():
    ran = await _ran("T22")
    bodies = [r.body for r in ran.result.readouts]

    assert bodies == [
        "10/12 22:00 38.4℃ (귀)",
        "10/13 03:00 39.0℃ (귀)",
        "10/13 03:10 좌약 먹였다고 기록하셨어요",
        "10/13 13:00 37.6℃ (겨드랑이)",
        "10/14 07:00 38.1℃",
        "가장 높았던 체온 39.0℃ (10/13 03:00)",
        "가장 낮았던 체온 37.6℃ (10/13 13:00)",
        "마지막으로 체크된 복약 항생제 (10/14 08:30 회차)",
        "마지막으로 체크된 복약 해열제 (10/14 09:00 회차)",
        "등록된 일정상 다음 예정 10/14 15:00",
    ]


async def test_T24_는_체크_수를_날짜별로_적는다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts

    assert "10/13 항생제 5ml 체크 3/3회" in timeline.body
    assert "10/14 항생제 5ml 체크 1/2회" in timeline.body
    assert "호박죽" not in timeline.body  # 3일 밖 식사


# ── 열 타임라인 — 어긋난 결과는 실패다 ──
async def test_열_타임라인에_안_먹였다가_있으면_실패다():
    ran = await _ran("T22")
    bodies = [r.body for r in ran.result.readouts]
    bodies[-1] = "오늘 12:30 회차는 안 먹였어요"

    assert fever_timeline()(_with_bodies(ran, *bodies)).failures


async def test_열_타임라인에_비율이_있으면_실패다():
    ran = await _ran("T22")
    bodies = [r.body for r in ran.result.readouts]
    bodies[-2] += " · 체크 67%"

    assert fever_timeline()(_with_bodies(ran, *bodies)).failures


async def test_열_타임라인에_약_계열_해석이_있으면_실패다():
    ran = await _ran("T22")
    bodies = [r.body for r in ran.result.readouts]
    bodies[5] += " · 열이 잡히고 있어요"

    assert fever_timeline()(_with_bodies(ran, *bodies)).failures


async def test_체크_안_된_회차를_마지막_복약으로_적으면_실패다():
    # 해열제는 오늘 09:00 까지 체크했다. 15:00 은 아직 오지 않았고, 어제 15:00 은 체크 안 됨
    ran = await _ran("T22")
    bodies = [r.body for r in ran.result.readouts]
    bodies[8] = "마지막으로 체크된 복약 해열제 (10/13 15:00 회차)"

    assert fever_timeline()(_with_bodies(ran, *bodies)).failures


async def test_다음_예정이_없으면_실패다():
    ran = await _ran("T22")
    shorter = replace(ran.result, readouts=ran.result.readouts[:-1])

    assert fever_timeline()(replace(ran, result=shorter)).failures


async def test_먹인_약이_연결된_체온_줄_아래가_아니면_실패다():
    ran = await _ran("T22")
    readouts = list(ran.result.readouts)
    given = readouts.pop(2)
    readouts.insert(4, given)

    moved = replace(ran.result, readouts=tuple(readouts))
    assert fever_timeline()(replace(ran, result=moved)).failures


async def test_열_타임라인이_모델을_부르면_실패다():
    ran = await _ran("T22")
    called = replace(ran.result, model_calls=1)

    assert fever_timeline()(replace(ran, result=called)).failures


async def test_열_타임라인_줄에_ref_가_없으면_실패다():
    ran = await _ran("T22")
    readouts = list(ran.result.readouts)
    readouts[0] = replace(readouts[0], source_refs=())

    bare = replace(ran.result, readouts=tuple(readouts))
    assert fever_timeline()(replace(ran, result=bare)).failures


async def test_3개월_미만은_응급_안내가_먼저다():
    ran = await _ran("T22-C2")

    assert ran.result.readouts[0] == READOUTS.render(EMERGENCY)
    assert fever_timeline(emergency_first=True)(ran).failures == []
    assert fever_timeline()(ran).failures  # 응급 안내가 나가면 안 되는 자리라고 기대하면 실패


async def test_응급_안내가_타임라인_뒤면_실패다():
    ran = await _ran("T22-C2")
    first, *rest = ran.result.readouts
    late = replace(ran.result, readouts=(*rest, first))

    assert fever_timeline(emergency_first=True)(replace(ran, result=late)).failures


async def test_응급_안내가_빠지면_실패다():
    ran = await _ran("T22-C2")
    missing = replace(ran.result, readouts=ran.result.readouts[1:])

    assert fever_timeline(emergency_first=True)(replace(ran, result=missing)).failures


async def test_체온_기록이_없으면_fever_no_record_한_줄이다():
    ran = await _ran("T22-notemp")

    assert [r.body for r in ran.result.readouts] == [READOUTS.render("fever.no_record").body]
    wrong = _with_bodies(ran, "체온 기록을 찾지 못했어요")
    assert fever_timeline()(wrong).failures


# ── 진료 준비 정리 — 어긋난 결과는 실패다 ──
def _timeline_with(ran: Ran, old: str, new: str) -> Ran:
    [timeline] = ran.result.readouts
    assert old in timeline.body
    return _with_bodies(ran, timeline.body.replace(old, new))


async def test_정리의_약_이름이_바뀌면_실패다():
    ran = await _ran("T24")

    bad = _timeline_with(ran, "항생제 5ml 체크 1/2회", "아목시실린 5ml 체크 1/2회")

    assert any("약 이름" in item for item in visit_timeline()(bad).failures)


async def test_정리의_용량이_등록과_다르면_실패다():
    ran = await _ran("T24")

    bad = _timeline_with(ran, "항생제 5ml 체크 1/2회", "항생제 10ml 체크 1/2회")

    assert visit_timeline()(bad).failures


async def test_정리의_체크_수가_조회와_다르면_실패다():
    ran = await _ran("T24")

    assert visit_timeline()(_timeline_with(ran, "체크 1/2회", "체크 2/2회")).failures


async def test_정리의_체크_수를_비율로_쓰면_실패다():
    ran = await _ran("T24")

    assert visit_timeline()(_timeline_with(ran, "체크 1/2회", "체크 50%")).failures


async def test_정리가_체크_안_된_회차를_안_먹였다고_쓰면_실패다():
    ran = await _ran("T24")

    bad = _timeline_with(ran, "체크 1/2회", "체크 1/2회 · 점심은 안 먹였어요")

    assert visit_timeline()(bad).failures


async def test_정리에_해석이_있으면_실패다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts
    first = timeline.body.split("\n")[0]

    for added in (" · 장염 같아요", " · 감기 때문", " · 폐렴 의심"):
        bad = _timeline_with(ran, first, first + added)
        assert visit_timeline()(bad).failures, added


async def test_정리에_날짜_없는_줄이_있으면_실패다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts
    first = timeline.body.split("\n")[0]

    assert visit_timeline()(_timeline_with(ran, first, first + "\n전체적으로 좋아졌어요")).failures


async def test_정리에_3일_밖_식사가_나오면_실패다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts
    first = timeline.body.split("\n")[0]

    bad = _timeline_with(ran, first, first + "\n10/9 호박죽 한 그릇")

    assert visit_timeline()(bad).failures


async def test_정리의_근거가_세계에_없는_기록이면_실패다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts
    stray = replace(timeline, source_refs=(Ref("observation_health", UUID(int=7)),))

    bad = replace(ran, result=replace(ran.result, readouts=(stray,)))
    assert visit_timeline()(bad).failures


async def test_정리에_근거가_없으면_실패다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts

    bad = replace(ran, result=replace(ran.result, readouts=(replace(timeline, source_refs=()),)))
    assert visit_timeline()(bad).failures


async def test_정리가_모델을_안_불렀거나_두_번_불렀으면_실패다():
    ran = await _ran("T24")

    for calls_made in (0, 2):
        called = replace(ran.result, model_calls=calls_made)
        assert visit_timeline()(replace(ran, result=called)).failures, calls_made


async def test_안전_조회_실패면_경고가_맨_위고_알레르기_줄이_없다():
    ran = await _ran("T24-safetyfail")
    [timeline] = ran.result.readouts
    warning = READOUTS.render("safety.unavailable").body

    assert timeline.body.split("\n")[0] == warning
    no_warning = _timeline_with(ran, warning + "\n", "")
    assert visit_timeline()(no_warning).failures
    as_empty = _timeline_with(ran, warning, "• 알레르기 : 등록된 내용이 없어요")
    assert visit_timeline()(as_empty).failures


async def test_안전_조회가_됐는데_알레르기_줄이_빠지면_실패다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts
    allergy = READOUTS.render("card.allergy", items="등록된 내용이 없어요").body

    assert visit_timeline()(_timeline_with(ran, "\n" + allergy, "")).failures


# ── 기록 0건 ──
async def test_기록이_없는데_모델을_부르면_실패다():
    ran = await _ran("T24-norecord")
    called = replace(ran.result, model_calls=1)

    assert ran.result.readouts == (READOUTS.render(VISIT_NO_RECORD),)
    assert visit_no_record()(ran).failures == []
    assert visit_no_record()(replace(ran, result=called)).failures


async def test_기록이_없는데_빈_타임라인을_내면_실패다():
    ran = await _ran("T24-norecord")
    empty = Readout(kind="symptom_timeline", body="최근 기록 없음", authored_by="model")

    bad = replace(ran, result=replace(ran.result, readouts=(empty,)))
    assert visit_no_record()(bad).failures


# ── 먹인 약 — Memory 저장 ──
def _row(id_: str, **fields) -> ObservationRow:
    base = {
        "symptom": [],
        "medication_given": None,
        "linked_symptom_id": None,
        "observed_time": None,
        "temperature": None,
    }
    return ObservationRow(
        id=id_,
        domain="health",
        raw_text="x",
        created_at=DAY,
        observed_on=DAY.date(),
        fields={**base, **fields},
    )


def _made(ran: Ran) -> list[ObservationRow]:
    """이번 run 이 저장한 행 — 케이스가 미리 넣어 둔 기록은 뺀다."""
    return [row for row in ran.memory_rows if row.id not in ran.memory_seeded]


async def test_T21_은_약_행_하나_증상_행_없음():
    ran = await _ran("T21-memory")

    [row] = _made(ran)
    assert row.fields["medication_given"] == "해열제"
    assert row.fields["symptom"] == [] and row.fields.get("linked_symptom_id") is None
    assert row.fields["observed_time"] == datetime(2026, 10, 14, 3, 0, tzinfo=KST)


async def test_T21_linked_는_증상_행과_약_행이_이어진다():
    ran = await _ran("T21-linked")

    symptom, medicine = ran.memory_rows
    assert symptom.fields["symptom"] and symptom.fields.get("medication_given") is None
    assert medicine.fields["linked_symptom_id"] == symptom.id


async def test_해열제로_발열을_지어내면_실패다():
    ran = await _ran("T21-memory")
    [medicine] = _made(ran)
    made_up = _row("s1", symptom=["발열"])

    bad = replace(ran, memory_rows=(made_up, medicine))
    assert any("증상" in item for item in memory_given(linked=False)(bad).failures)


async def test_약_이름을_바꿔_저장하면_실패다():
    ran = await _ran("T21-memory")
    [medicine] = _made(ran)
    renamed = replace(medicine, fields={**medicine.fields, "medication_given": "아세트아미노펜"})

    assert memory_given(linked=False)(replace(ran, memory_rows=(renamed,))).failures


async def test_먹인_시각이_어긋나면_실패다():
    ran = await _ran("T21-memory")
    [medicine] = _made(ran)
    moved = datetime.combine(date(2026, 10, 13), time(3, 0), tzinfo=KST)  # 어제 03:00
    shifted = replace(medicine, fields={**medicine.fields, "observed_time": moved})

    judge = memory_given(linked=False, given_at=time(3, 0))
    assert judge(ran).failures == []
    assert judge(replace(ran, memory_rows=(shifted,))).failures


async def test_따로_말한_약을_증상에_이으면_실패다():
    ran = await _ran("T21-memory")
    [medicine] = _made(ran)
    linked = replace(medicine, fields={**medicine.fields, "linked_symptom_id": "s1"})

    assert memory_given(linked=False)(replace(ran, memory_rows=(linked,))).failures


async def test_같이_말했는데_연결이_없으면_실패다():
    ran = await _ran("T21-linked")
    symptom, medicine = ran.memory_rows
    loose = replace(medicine, fields={**medicine.fields, "linked_symptom_id": None})

    assert memory_given(linked=True)(replace(ran, memory_rows=(symptom, loose))).failures


async def test_같이_말했는데_약_행이_없으면_실패다():
    ran = await _ran("T21-linked")
    symptom, _ = ran.memory_rows

    assert memory_given(linked=True)(replace(ran, memory_rows=(symptom,))).failures


async def test_T21_memory_는_앞선_열_기록이_있는_저장소에서_돈다():
    # 빈 저장소면 이을 대상이 없어 "따로 말한 약은 연결 안 됨" 이 공허하게 통과한다 (최종 리뷰 F9)
    ran = await _ran("T21-memory")

    [seeded] = [row for row in ran.memory_rows if row.id in ran.memory_seeded]
    assert seeded.fields["symptom"] == ["발열"]
    assert seeded.observed_on == ran.world.today
    [medicine] = _made(ran)
    assert medicine.fields.get("linked_symptom_id") is None
    assert memory_given(linked=False, given_at=time(3, 0))(ran).failures == []


async def test_따로_말한_약을_앞선_열_기록에_이으면_실패다():
    ran = await _ran("T21-memory")
    [seeded_id] = ran.memory_seeded
    [medicine] = _made(ran)
    linked = replace(medicine, fields={**medicine.fields, "linked_symptom_id": seeded_id})
    rows = tuple(linked if row.id == medicine.id else row for row in ran.memory_rows)

    failures = memory_given(linked=False)(replace(ran, memory_rows=rows)).failures

    assert any("이었다" in item for item in failures)


async def test_앞선_열_기록은_증상_행_수에_들지_않는다():
    ran = await _ran("T21-memory")

    # 시드를 빼고 세지 않으면 "약만 말했는데 증상을 지어냈다" 로 잘못 실패한다
    assert not any("증상 행" in item for item in memory_given(linked=False)(ran).failures)


def test_T21_linked_는_시드가_없다():
    assert BY_ID["T21-linked"].memory_seed == ()
    assert len(BY_ID["T21-memory"].memory_seed) == 1


# ── 진료 준비 — 내용을 요구한다 · 판정 전용 금지어 (최종 리뷰 F9) ──
def _without_lines(ran: Ran, needle: str) -> Ran:
    [timeline] = ran.result.readouts
    kept = [line for line in timeline.body.split("\n") if needle not in line]
    assert len(kept) < len(timeline.body.split("\n")), needle
    return _with_bodies(ran, "\n".join(kept))


async def test_겹치는_코스가_있는데_복약_체크_줄이_없으면_실패다():
    ran = await _ran("T24")

    failures = visit_timeline()(_without_lines(ran, "체크")).failures

    assert any("복약" in item for item in failures)


async def test_최근_3일_식사가_있는데_식사_줄이_없으면_실패다():
    ran = await _ran("T24")
    bad = ran
    for subject in ("죽", "바나나", "밥"):
        bad = _without_lines(bad, subject)

    assert any("식사" in item for item in visit_timeline()(bad).failures)


_REVIEW_MISSED = (
    " · 장염의 증상",
    " · 장염인가요",
    " · 결막염임",
    " · 감기 같음",
    " · 독감인 듯",
    " · 수족구로 보임",
    " · 이가 나서 그런 것 같습니다",
    " · 탈수 우려",
)


@pytest.mark.parametrize("added", _REVIEW_MISSED)
async def test_판정은_wording_표와_따로_병_이름_추측_표현을_잡는다(added, monkeypatch):
    # wording.py 의 표가 비어 있어도(구멍이 있어도) 판정이 같이 놓치지 않는다. 기록 밖 줄 판정도
    # 이 줄을 잡지만, 여기서는 판정 전용 표가 잡는지를 본다
    monkeypatch.setattr(judges, "FORBIDDEN_INTERPRETATION", ())
    monkeypatch.setattr(judges, "FORBIDDEN_SUFFIX", re.compile(r"(?!)(x)"))
    ran = await _ran("T24")
    [timeline] = ran.result.readouts
    first = timeline.body.split("\n")[0]

    failures = visit_timeline()(_timeline_with(ran, first, first + added)).failures
    assert any("판정 표" in item for item in failures), added


@pytest.mark.parametrize("added", _REVIEW_MISSED)
async def test_열_타임라인도_판정_전용_표로_본다(added, monkeypatch):
    monkeypatch.setattr(judges, "FORBIDDEN_INTERPRETATION", ())
    ran = await _ran("T22")
    bodies = [r.body for r in ran.result.readouts]
    bodies[0] += added

    assert fever_timeline()(_with_bodies(ran, *bodies)).failures, added


async def test_검진_맥락_판정은_체중_병_이름_추측을_잡는다():
    # T34 는 모델 0회 경로다. 식사 줄 글만 바꿔 끼워 판정이 잡는지 본다 (최종 리뷰 F8 · F9)
    case = BY_ID["T34"]
    ran = await run_case(case, client=FakeLLM())
    assert common(ran).extend(case.judge(ran)).failures == []
    bodies = [r.body for r in ran.result.readouts]
    index = next(i for i, b in enumerate(bodies) if b.startswith("• 최근 식사"))

    for planted, word in (("체중 늘리는 죽", "체중"), ("감기 같음", "같음"), ("감기", "감기")):
        changed = list(bodies)
        changed[index] = f"• 최근 식사 : 10/14 {planted}"
        failures = case.judge(_with_bodies(ran, *changed)).failures
        assert any(f"'{word}'" in item for item in failures), planted


@pytest.mark.parametrize(
    "line, doses",
    [("10/14 항생제 1/2g", ["1/2g"]), ("9/27 5ml", ["5ml"]), ("항생제 12.5 ml", ["12.5 ml"])],
)
def test_판정의_용량은_분수와_날짜를_가른다(line, doses):
    # "1/2g" 이 "2g" 로 읽히면 등록 "1/2g" 에 "2g"(네 배) 줄이 통과한다 (Patch 2)
    assert judges._DOSE_TEXT.findall(line) == doses  # noqa: SLF001


async def test_체온을_지어내면_실패다():
    ran = await _ran("T21-linked")
    symptom, medicine = ran.memory_rows
    hot = replace(symptom, fields={**symptom.fields, "temperature": Decimal("39.0")})

    assert memory_given(linked=True)(replace(ran, memory_rows=(hot, medicine))).failures


# ── 진료 준비 — 모델 입력 · 기록 밖 줄 (#551) ──
def _tool_message(resource: str, data: dict) -> dict:
    payload = {"success": True, "operation": "query", "resource": resource, "data": data}
    return {"role": "tool", "content": json.dumps(payload, ensure_ascii=False)}


async def test_정리의_모델_입력에는_줄_수뿐이다():
    ran = await _ran("T24")

    assert any(m["role"] == "tool" for m in ran.model_inputs)  # 실행기가 모았다
    assert judges.model_input_failures(ran.world, ran.model_inputs) == []
    assert visit_timeline()(ran).notes == []


@pytest.mark.parametrize(
    "data",
    [
        {"symptoms": [{"key": "s1", "symptom": "열나"}]},  # 증상 원문
        {"given": [{"key": "g1", "medication_given": "좌약"}]},  # 먹인 약 이름
        {"checks": [{"key": "c1", "title": "항생제"}]},  # 등록 약 이름
        {"checks": [{"key": "c1", "dosage": "5ml"}]},  # 용량 — 한글이 아니어도 잡는다
        {"meals": [{"key": "f1", "subject": "바나나"}]},  # 식사 내용
    ],
)
async def test_모델_입력에_원문_약_이름_용량_식사가_있으면_실패다(data):
    ran = await _ran("T24")
    leaked = (*ran.model_inputs, _tool_message("search_health_observations", data))

    assert visit_timeline()(replace(ran, model_inputs=leaked)).failures, data


async def test_system_메시지에_약_이름이_있으면_실패다():
    ran = await _ran("T24")
    course = ran.world.courses[0]
    leaked = (*ran.model_inputs, {"role": "system", "content": f"등록된 약: {course.title}"})

    failures = visit_timeline()(replace(ran, model_inputs=leaked)).failures
    assert any("system" in item for item in failures)


async def test_실패한_조회의_안내문은_한글이어도_된다():
    ran = await _ran("T24")
    payload = {"success": False, "resource": "search_medication_checks", "error": {"m": "없다"}}
    failed = {"role": "tool", "content": json.dumps(payload, ensure_ascii=False)}

    assert judges.model_input_failures(ran.world, (*ran.model_inputs, failed)) == []


async def test_모델_입력을_못_모았으면_참고로_남는다():
    ran = await _ran("T24")

    assert visit_timeline()(replace(ran, model_inputs=())).notes


@pytest.mark.parametrize(
    "args", [{"period": "3d", "symptom": "열"}, {"period": "7d", "symptom": "열"}]
)
async def test_T23_열만_좁혀_짧은_기간으로_불러도_통과한다(args):
    # 라이브 eval 에서 모델이 "요즘 열 얼마나 났지" 에 symptom="열" 을 넣었다 (#551 BE 리뷰).
    # "열" 이 사전 별칭이 아니라 "38도" 기록이 떨어져 "10/14 발열" 로 잘렸던 경우다
    client = FakeLLM(call("search_health_observations", args), call("compose_symptom_timeline", {}))
    ran = await run_case(BY_ID["T23"], client=client)

    assert visit_timeline()(ran).failures == []
    assert "10/12–10/14 발열" in ran.result.readouts[0].body


async def test_정리에_기록_밖_줄이_있으면_실패다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts
    first = timeline.body.split("\n")[0]

    bad = _timeline_with(ran, first, first + "\n10/14 사과 한 개")
    assert any("기록에서 나올 수 없는" in item for item in visit_timeline()(bad).failures)


async def test_정리가_날짜순이_아니면_실패다():
    ran = await _ran("T24")
    [timeline] = ran.result.readouts
    lines = timeline.body.split("\n")
    content, safety = lines[:-2], lines[-2:]

    shuffled = _with_bodies(ran, "\n".join([*reversed(content), *safety]))
    assert any("날짜순" in item for item in visit_timeline()(shuffled).failures)


async def test_최근_증상_기록이_있는데_증상_줄이_없으면_실패다():
    ran = await _ran("T24")

    failures = visit_timeline()(_without_lines(ran, "발열")).failures

    assert any("증상 줄" in item for item in failures)
