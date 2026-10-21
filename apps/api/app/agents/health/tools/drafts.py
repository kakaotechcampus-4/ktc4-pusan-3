"""복약 초안 tool — `create_medication_schedule` · `update_medication_schedule` ·
`stop_medication_schedule`.

**셋 다 DB 를 건드리지 않는다.** 생성 · 수정은 초안 payload 까지만 만들고(보호자가 카드에서 제출해야
백엔드가 쓴다), 중단은 한 번 묻고 그 run 에서는 아무것도 바꾸지 않는다. '네' 를 받은 다음 run 이
`labels/medication.py` 에서 `stopped` 로 바꾼다.

모델은 요청 원문의 말을 그대로 복사한 span 만 넘기고, 코드가 세 가지를 한다.

    - span 이 원문의 부분 문자열인지 본다. 아니면 거절한다 (약 이름 · 용량이 바뀐 채 나가지 않게)
    - 시점 표현은 사전이 anchor 로 바꾼다. 못 풀면 짐작하지 않고 되묻는다 — 초안 0 · 맥락만 남긴다
    - 대상 약은 `title_span` 으로 진행 중 코스에서 찾는다. 모델에게 id 를 받지 않는다. 모호하면 먼저
      어느 약인지 묻는다 — 엉뚱한 약의 알림이 끊기거나 바뀌지 않게

되묻고 멈추는 자리는 세 곳이다(중단 확인 · 대상 약 · 복용 시점). 셋 다 `state.question` 과
`state.pending` 을 채우고 `ok` 로 끝난다 — 루프가 거기서 멈춘다. 맥락(`pending.payload`)에는
대상 id · 화면에 나갈 값 · 이어서 처리할 칸(검증을 통과한 span)만 싣고 발화 원문 전체는 싣지
않는다.

로그에 약명 · 이유 원문을 남기지 않는다. 실패 메시지에도 값을 싣지 않는다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.agents.common.age import life_stage
from app.agents.common.result import DomainPending
from app.agents.health.context import HealthContext
from app.agents.health.readouts import (
    ASK_DOSE_CLOCK,
    ASK_DOSE_TIMING,
    ASK_WHICH_MED,
    MED_CONFIRM,
    MED_ONE_AT_A_TIME,
    MED_STOP_CONFIRM,
    MED_UPDATE_CONFIRM,
    NOTICE_NO_ACTIVE_MED,
    READOUTS,
)
from app.agents.health.result import ErrorCode, ToolResult, fail, ok
from app.agents.health.schemas.medication import (
    CreateMedicationArgs,
    StopMedicationArgs,
    UpdateMedicationArgs,
)
from app.agents.health.store.ports import MedicationCourse
from app.agents.health.tools.spans import is_verbatim, normalize_spaces
from app.agents.health.tools.timing import (
    ResolvedDose,
    anchors_named,
    resolve_dose_timing,
    resolve_period,
    unclear_clock,
)
from app.core.medication_draft import (
    ANCHOR_TIMES,
    REQUIRED_SCHEDULE_FIELDS,
    DoseDraft,
    MedicationDraft,
    MedicationSnapshot,
    ScheduleDraft,
    TimingAnchor,
)

AGENT = "health"
_MEALS = ("breakfast", "lunch", "dinner")
CREATE = "create_medication_schedule"
UPDATE = "update_medication_schedule"
STOP = "stop_medication_schedule"

# 되묻기 맥락의 kind
KIND_STOP = "medication_stop"
KIND_WHICH = "medication_which"
KIND_TIMING = "dose_timing"


@dataclass(frozen=True)
class Ambiguous:
    """대상 약을 못 가렸다 — 어느 약인지 묻는다."""

    candidates: tuple[MedicationCourse, ...]


def pick_course(
    courses: Sequence[MedicationCourse], title_span: str | None
) -> MedicationCourse | Ambiguous | None:
    """진행 중 코스에서 대상 약 하나를 찾는다. 임의로 고르지 않는다.

    - 코스가 없으면 None
    - 이름이 없으면 하나일 때만 그 약이다
    - 이름이 있으면 코스 이름과 한쪽이 다른 쪽을 포함하는 코스가 **딱 하나**일 때만 그 약이다.
      하나도 안 맞으면 하나뿐이어도 모호하다 — 엉뚱한 약의 알림을 끊거나 바꾸지 않는다
    """
    if not courses:
        return None
    name = normalize_spaces(title_span) if title_span else ""
    if not name:
        return courses[0] if len(courses) == 1 else Ambiguous(tuple(courses))
    matched = [c for c in courses if name in (t := normalize_spaces(c.title)) or t in name]
    if len(matched) == 1:
        return matched[0]
    return Ambiguous(tuple(matched or courses))


async def create_medication_schedule(
    context: HealthContext, args: CreateMedicationArgs
) -> ToolResult:
    """새 약의 복약 알림 초안. 시점을 못 풀면 되묻고, 기간이 없으면 `ends_on` 을 비운다."""
    unverified = _unverified(
        context,
        [
            ("title_span", args.title_span),
            ("dosage_span", args.dosage_span),
            ("indication_span", args.indication_span),
            ("period_span", args.period_span),
            *(("timing_spans", span) for span in args.timing_spans),
        ],
    )
    if unverified:
        return _not_verbatim(CREATE, unverified)

    starts_on, ends_on = resolve_period(args.period_span, today=context.today) or (
        context.today,
        None,  # 못 읽는 기간도 짐작하지 않는다 — missing 으로 넘겨 카드에서 채우게 한다
    )
    fields = dict(
        title=_clean(args.title_span),
        dosage=_clean(args.dosage_span),
        indication_text=_clean(args.indication_span),
        starts_on=starts_on,
        ends_on=ends_on,
    )
    timing = resolve_dose_timing(args.timing_spans, months=await child_months(context))
    if timing is None:
        return _ask_timing(context, CREATE, "create", _spans(args.timing_spans), fields)
    return finish_create(context, timing=timing, **fields)


async def update_medication_schedule(
    context: HealthContext, args: UpdateMedicationArgs
) -> ToolResult:
    """등록된 약의 시각 · 양 · 기간을 바꾸는 초안. 말하지 않은 칸은 지금 값 그대로다."""
    unverified = _unverified(
        context,
        [
            ("title_span", args.title_span),
            ("dosage_span", args.dosage_span),
            ("period_span", args.period_span),
            *(("timing_spans", span) for span in args.timing_spans or []),
        ],
    )
    if unverified:
        return _not_verbatim(UPDATE, unverified)

    target = pick_course(await _active(context), args.title_span)
    if target is None:
        return fail(
            "draft",
            UPDATE,
            ErrorCode.NOT_FOUND,
            "진행 중인 복약이 없어 바꿀 수 없다. 새로 등록할지 보호자에게 묻는다.",
        )
    timing_spans = _spans(args.timing_spans) if args.timing_spans is not None else None
    if isinstance(target, Ambiguous):
        return _ask_which(
            context,
            UPDATE,
            "update",
            target.candidates,
            {
                "dosage_span": _clean(args.dosage_span),
                "timing_spans": timing_spans,
                "period_span": _clean(args.period_span),
            },
        )
    return await update_for(
        context,
        target,
        dosage_span=_clean(args.dosage_span),
        timing_spans=timing_spans,
        period_span=_clean(args.period_span),
    )


async def update_for(
    context: HealthContext,
    course: MedicationCourse,
    *,
    dosage_span: str | None,
    timing_spans: list[str] | None,
    period_span: str | None,
) -> ToolResult:
    """대상 코스가 정해진 수정. 대상 약을 고른 뒤 이어 갈 때도 이 길로 온다(span 은 검증됨).

    이 run 에서 같은 약의 수정 초안이 이미 있으면 그 위에 쌓는다 — DB 현재 값이 아니라 앞 초안이
    현재 값이다(`_base_for`).
    """
    base = _base_for(context, course)
    ends_on = base.schedule.ends_on or course.ends_on
    if period := resolve_period(period_span, today=context.today):
        ends_on = period[1] or ends_on  # 못 읽는 기간은 지금 값 그대로
    dosage = dosage_span or base.schedule.dosage
    if timing_spans is None:  # 시점은 안 바뀌었다
        return finish_update(context, course, dosage=dosage, ends_on=ends_on, timing=None)
    timing = resolve_dose_timing(timing_spans, months=await child_months(context))
    if timing is None:
        fields = dict(
            title=base.schedule.title,
            dosage=dosage,
            indication_text=base.schedule.indication_text,
            starts_on=base.schedule.starts_on,
            ends_on=ends_on,
        )
        return _ask_timing(context, UPDATE, "update", timing_spans, fields, course=course)
    return finish_update(
        context,
        course,
        dosage=dosage,
        ends_on=ends_on,
        timing=timing,
        named=anchors_named(timing_spans),
    )


async def stop_medication_schedule(context: HealthContext, args: StopMedicationArgs) -> ToolResult:
    """복약 중단. 바꾸지 않고 한 번 묻는다 — '네' 를 받은 다음 run 이 `stopped` 로 바꾼다."""
    unverified = _unverified(context, [("title_span", args.title_span)])
    if unverified:
        return _not_verbatim(STOP, unverified)

    target = pick_course(await _active(context), args.title_span)
    if target is None:
        # 멈출 것이 없다. 되묻지 않고 없다고만 안내한다
        context.state.readouts.append(READOUTS.render(NOTICE_NO_ACTIVE_MED))
        return ok("record", STOP, asked=False)
    if isinstance(target, Ambiguous):
        return _ask_which(context, STOP, "stop", target.candidates, {})
    return ask_stop(context, target)


def ask_stop(context: HealthContext, course: MedicationCourse) -> ToolResult:
    """중단 확인 질문. 이 run 에서는 아무것도 바꾸지 않는다."""
    if busy := _busy(context, STOP):
        return busy
    question = READOUTS.render(MED_STOP_CONFIRM, title=course.title).body
    _ask(
        context,
        DomainPending(
            agent=AGENT,
            kind=KIND_STOP,
            question=question,
            payload={"schedule_id": str(course.id), "title": course.title},
        ),
    )
    return ok("record", STOP, asked=True)


def finish_create(
    context: HealthContext,
    *,
    title: str | None,
    dosage: str | None,
    indication_text: str | None,
    starts_on: date | None,
    ends_on: date | None,
    timing: Sequence[ResolvedDose],
) -> ToolResult:
    """새 약 초안을 만든다. 비어 있는 필수 칸은 `missing` 에 실린다."""
    schedule = ScheduleDraft(
        title=title,
        dosage=dosage,
        indication_text=indication_text,
        storage=None,  # 보호자가 카드에서 적는 선택 칸 — Agent 는 비워 둔다
        med_form=None,
        note=None,
        starts_on=starts_on,
        ends_on=ends_on,
    )
    return _publish(
        context,
        CREATE,
        op="create",
        schedule=schedule,
        doses=_dose_drafts(timing),
        confirm=MED_CONFIRM,
    )


def finish_update(
    context: HealthContext,
    course: MedicationCourse,
    *,
    dosage: str | None,
    ends_on: date,
    timing: Sequence[ResolvedDose] | None,
    named: set[TimingAnchor] | None = None,
) -> ToolResult:
    """수정 초안. 일정처럼 **전체 값**(지금 값에 바뀐 값을 얹은 것)과 `before` 를 싣는다.

    바뀐 부분만 따로 보내지 않는다 — 화면이 `before` 와 비교한다. 바뀐 필드 이름(`changed`)은
    payload 에 싣지 않고 tool 결과(모델)와 "바뀐 게 없으면 초안을 만들지 않는다" 판정에 쓴다.
    보호자가 적은 칸(보관 · 제형 · 특이사항)과 말하지 않은 칸은 지금 값 그대로다. 같은 약을 이
    run 에서 이미 고쳤으면 앞 초안 위에 쌓고 같은 `draft_id` 로 갈아 끼운다.
    """
    base = _base_for(context, course)
    current = base.doses
    if timing is None:
        doses = list(current)
    else:
        timing = _without_references(timing)
        # 끼니를 말했으면 그 회차만 바꾸고, 안 말했으면("하루 두 번 식후로") 패턴 전체를 바꾼다
        kept = _replace_named(current, named) if named else []
        doses = kept + _dose_drafts(timing)
    schedule = base.schedule.model_copy(update={"dosage": dosage, "ends_on": ends_on})
    if _same(base.schedule, current, schedule, doses):
        # 같은 값으로 "바꿀까요?" 카드를 띄우지 않는다
        return fail(
            "draft",
            UPDATE,
            ErrorCode.NO_CHANGE,
            "바꾸는 값이 지금과 같다. 보호자가 바꾸려는 것이 있는지 확인한다.",
        )
    if _same(base.before.schedule, base.before.doses, schedule, doses):
        # 앞 호출의 변경을 되돌려 처음 값과 같아졌다 — 보여 줄 수정이 없으니 앞 초안도 거둔다
        _drop(context, base.draft_id)
        return fail(
            "draft",
            UPDATE,
            ErrorCode.NO_CHANGE,
            "바꾸는 값이 처음 값과 같아졌다. 보호자가 바꾸려는 것이 있는지 확인한다.",
        )
    changed = _merge_changed(base.changed, _diff(base.schedule, current, schedule, doses))
    return _publish(
        context,
        UPDATE,
        op="update",
        schedule=schedule,
        doses=doses,
        confirm=MED_UPDATE_CONFIRM,
        schedule_id=str(course.id),
        before=base.before,
        draft_id=base.draft_id,
        changed=changed,
    )


def _publish(
    context: HealthContext,
    resource: str,
    *,
    op: str,
    schedule: ScheduleDraft,
    doses: list[DoseDraft],
    confirm: str,
    schedule_id: str | None = None,
    before: MedicationSnapshot | None = None,
    draft_id: str | None = None,
    changed: tuple[str, ...] = (),
) -> ToolResult:
    notice_times = sorted(f"{dose.scheduled_time:%H:%M}" for dose in doses)
    try:
        draft = MedicationDraft(
            draft_id=draft_id or str(uuid4()),
            op=op,  # type: ignore[arg-type]
            source="utterance",
            schedule=schedule,
            doses=doses,
            notice_times=notice_times,
            missing=[n for n in REQUIRED_SCHEDULE_FIELDS if getattr(schedule, n) is None],
            schedule_id=schedule_id,
            before=before,
        )
    except ValidationError:
        # 화면에 못 나갈 초안(같은 시각 둘 등)은 내지 않는다. 값은 메시지에 싣지 않는다
        return fail(
            "draft",
            resource,
            ErrorCode.INVALID_ARGS,
            "초안이 계약과 맞지 않는다(같은 시각이 겹치는 등). 시점을 확인해 다시 호출한다.",
        )
    state = context.state
    # 같은 draft_id 면 그 자리를 갈아 끼운다 — 한 약의 수정은 한 장이다(일정의 DraftBook 과 같다)
    index = next(
        (i for i, kept in enumerate(state.drafts) if kept.draft_id == draft.draft_id), None
    )
    if index is None:
        state.drafts.append(draft)
    else:
        state.drafts[index] = draft
    readout = READOUTS.render(
        confirm,
        title=schedule.title or "-",
        times=" · ".join(notice_times),
        starts_on=_md(schedule.starts_on),
        ends_on=_md(schedule.ends_on),
    )
    if (stale := state.confirms.get(draft.draft_id)) is not None:
        state.readouts.remove(stale)  # 앞 초안의 확인 문구는 낡은 시각이다
    state.confirms[draft.draft_id] = readout
    state.readouts.append(readout)
    extra: dict[str, Any] = {}
    if op == "update":
        state.changed[draft.draft_id] = changed
        extra["changed"] = list(changed)  # 모델이 무엇이 바뀌었는지 안다 — payload 에는 없다
    return ok(
        "draft",
        resource,
        draft=True,
        draft_id=draft.draft_id,
        notice_times=notice_times,
        missing=draft.missing,
        **extra,
    )


@dataclass(frozen=True)
class _Base:
    """수정의 출발점. 이 run 에 같은 약의 수정 초안이 있으면 그것이, 없으면 DB 코스가 현재 값."""

    schedule: ScheduleDraft
    doses: list[DoseDraft]
    before: MedicationSnapshot  # 처음 원본 — 두 번째 호출부터도 이 run 의 DB 값 그대로
    draft_id: str | None
    changed: tuple[str, ...]


def _base_for(context: HealthContext, course: MedicationCourse) -> _Base:
    key = str(course.id)
    buffered = next(
        (d for d in context.state.drafts if d.op == "update" and d.schedule_id == key), None
    )
    if buffered is not None and buffered.before is not None:
        return _Base(
            schedule=buffered.schedule,
            doses=list(buffered.doses),
            before=buffered.before,
            draft_id=buffered.draft_id,
            changed=context.state.changed.get(buffered.draft_id, ()),
        )
    original = MedicationSnapshot(schedule=_schedule_of(course), doses=_dose_drafts(course.pattern))
    return _Base(original.schedule, list(original.doses), original, None, ())


# 바뀐 필드 이름의 순서. 일정의 _CHANGE_ORDER 와 같은 역할이다
_CHANGE_ORDER = (*ScheduleDraft.model_fields, "doses")


def _diff(
    old: ScheduleDraft, old_doses: list[DoseDraft], new: ScheduleDraft, new_doses: list[DoseDraft]
) -> tuple[str, ...]:
    """모델이 넘긴 인자가 아니라 값을 비교한다."""
    names = [
        name for name in ScheduleDraft.model_fields if getattr(old, name) != getattr(new, name)
    ]
    if _by_time(old_doses) != _by_time(new_doses):
        names.append("doses")
    return tuple(names)


def _merge_changed(*groups: tuple[str, ...]) -> tuple[str, ...]:
    """앞 호출에서 바뀐 것과 이번에 바뀐 것을 합쳐 필드 순서로 정렬한다."""
    seen = {name for group in groups for name in group}
    return tuple(name for name in _CHANGE_ORDER if name in seen)


def _same(
    old: ScheduleDraft, old_doses: list[DoseDraft], new: ScheduleDraft, new_doses: list[DoseDraft]
) -> bool:
    return old == new and _by_time(old_doses) == _by_time(new_doses)


def _drop(context: HealthContext, draft_id: str | None) -> None:
    """초안과 그 확인 문구 · changed 를 거둔다."""
    if draft_id is None:
        return
    state = context.state
    state.drafts[:] = [d for d in state.drafts if d.draft_id != draft_id]
    if (readout := state.confirms.pop(draft_id, None)) is not None:
        state.readouts.remove(readout)
    state.changed.pop(draft_id, None)


def _ask_timing(
    context: HealthContext,
    resource: str,
    op: str,
    timing_spans: list[str],
    fields: Mapping[str, Any],
    *,
    course: MedicationCourse | None = None,
) -> ToolResult:
    """시점을 못 풀었다 — 짐작하지 않고 되묻는다. 시점 밖 칸은 맥락에 실어 다음 run 이 이어 간다.

    시각이 모호해서("8시에") 못 풀었으면 식전 · 식후가 아니라 몇 시인지(오전 · 오후)를 묻는다.
    """
    if busy := _busy(context, resource):
        return busy
    payload: dict[str, Any] = {
        "op": op,
        "timing_spans": timing_spans,
        "draft": {
            name: value.isoformat() if isinstance(value, date) else value
            for name, value in fields.items()
        },
    }
    if course is not None:
        payload["schedule_id"] = str(course.id)
    _ask(
        context,
        DomainPending(
            agent=AGENT,
            kind=KIND_TIMING,
            question=READOUTS.render(
                ASK_DOSE_CLOCK if unclear_clock(timing_spans) else ASK_DOSE_TIMING
            ).body,
            payload=payload,
        ),
    )
    return ok("draft", resource, asked=True)


def _ask_which(
    context: HealthContext,
    resource: str,
    op: str,
    candidates: Sequence[MedicationCourse],
    args: Mapping[str, Any],
) -> ToolResult:
    """대상 약을 못 가렸다 — 어느 약인지 먼저 묻는다. 이어서 할 일(op · span)을 맥락에 싣는다."""
    if busy := _busy(context, resource):
        return busy
    titles = " · ".join(course.title for course in candidates)
    _ask(
        context,
        DomainPending(
            agent=AGENT,
            kind=KIND_WHICH,
            question=READOUTS.render(ASK_WHICH_MED, titles=titles).body,
            payload={
                "op": op,
                "candidates": [
                    {"schedule_id": str(course.id), "title": course.title} for course in candidates
                ],
                "args": dict(args),
            },
        ),
    )
    return ok("draft" if op == "update" else "record", resource, asked=True)


def _busy(context: HealthContext, resource: str) -> ToolResult | None:
    """이미 되묻는 중이면 두 번째 되묻기는 하지 않는다.

    보호자에게는 질문이 하나만 나가야 하고, 둘째가 첫째의 맥락을 덮어쓰면 첫째 약이 조용히 사라진다.
    대신 나머지는 이어서 말해 달라고 안내한다(한 번만).
    """
    if context.state.pending is None:
        return None
    note = READOUTS.render(MED_ONE_AT_A_TIME)
    if note not in context.state.readouts:
        context.state.readouts.append(note)
    return fail(
        "draft",
        resource,
        ErrorCode.INVALID_ARGS,
        "이미 보호자에게 되묻는 중이다. 되묻기는 한 번에 하나만 한다. 이 약은 지금 처리 안 한다.",
    )


def _ask(context: HealthContext, pending: DomainPending) -> None:
    context.state.question = pending.question
    context.state.pending = pending


def _dose_drafts(slots: Sequence[Any]) -> list[DoseDraft]:
    """`DoseSlot` · `ResolvedDose` → 초안의 시각 칸."""
    return [
        DoseDraft(
            timing_anchor=slot.timing_anchor,
            offset_minutes=slot.offset_minutes,
            fixed_time=slot.fixed_time,
            scheduled_time=slot.scheduled_time,
        )
        for slot in slots
    ]


# anchor 로 못 찾을 때(이미 `fixed` 로 고친 저녁 회차) 그 끼니 시각 둘레에서만 찾는다. 멀리 있는
# 회차를 "가장 가깝다" 는 이유로 지우면 말하지 않은 회차가 사라진다
NEAREST_WINDOW_MINUTES = 150


def _replace_named(doses: list[DoseDraft], named: set[TimingAnchor]) -> list[DoseDraft]:
    """말한 끼니의 회차를 뺀다. 그 anchor 가 없으면 그 끼니 시각 둘레(±150분)의 회차를 뺀다.

    "저녁 7시로" 로 이미 한 번 고친 코스는 저녁 회차가 `fixed` 라 anchor 로는 못 찾는다. 둘레에
    회차가 없으면(말한 끼니가 원래 패턴에 없었다) 아무것도 빼지 않고 새 회차만 더한다.
    """
    remaining = list(doses)
    for anchor in sorted(named, key=lambda a: ANCHOR_TIMES[a]):
        if not remaining:
            break
        exact = next((d for d in remaining if d.timing_anchor == anchor), None)
        if exact is not None:
            remaining.remove(exact)
            continue
        base = _minutes(ANCHOR_TIMES[anchor])
        nearest = min(remaining, key=lambda d: abs(_minutes(d.scheduled_time) - base))
        if abs(_minutes(nearest.scheduled_time) - base) <= NEAREST_WINDOW_MINUTES:
            remaining.remove(nearest)
    return remaining


def _without_references(timing: Sequence[ResolvedDose]) -> Sequence[ResolvedDose]:
    """ "저녁 약은 자기 전으로" 의 저녁은 옮길 회차를 가리키는 말이지 새 회차가 아니다.

    끼니만 말한(시점 표현이 없는) 회차와 자기 전 · 일어나서 · 시각이 한 말에 같이 있으면 끼니 쪽을
    새 회차로 더하지 않는다. 안 그러면 저녁이 17:00 으로 다시 생겨 옮긴 것이 아니라 하나 늘어난다.
    """
    bare = [d for d in timing if d.timing_anchor in _MEALS and d.offset_minutes == 0]
    others = [d for d in timing if d not in bare]
    return others if bare and others else timing


def _by_time(doses: list[DoseDraft]) -> list[DoseDraft]:
    return sorted(doses, key=lambda dose: dose.scheduled_time)


def _minutes(moment) -> int:
    return moment.hour * 60 + moment.minute


def _schedule_of(course: MedicationCourse) -> ScheduleDraft:
    return ScheduleDraft(
        title=course.title,
        dosage=course.dosage,
        indication_text=course.indication_text,
        storage=course.storage,
        med_form=course.med_form,
        note=course.note,
        starts_on=course.starts_on,
        ends_on=course.ends_on,
    )


async def _active(context: HealthContext) -> list[MedicationCourse]:
    return await context.ports.medication.active_courses(
        child_id=context.child_id, today=context.today
    )


async def child_months(context: HealthContext) -> int:
    """아이의 월령. 라벨 실행기가 되묻기 답을 풀 때도 쓴다."""
    birth = context.state.birth_date or await context.ports.profile.birth_date(
        child_id=context.child_id
    )
    return life_stage(birth, context.today).months


def _clean(span: str | None) -> str | None:
    return span if span and span.strip() else None


def _spans(spans: Sequence[str]) -> list[str]:
    return [span for span in spans if span.strip()]


def _unverified(context: HealthContext, spans: Sequence[tuple[str, str | None]]) -> str | None:
    """원문에 없는 span 이 있으면 그 칸 이름을 돌려준다. 값은 돌려주지 않는다."""
    texts = context.state.request_texts
    for name, span in spans:
        if span is not None and span.strip() and not is_verbatim(span, texts):
            return name
    return None


def _not_verbatim(resource: str, name: str) -> ToolResult:
    return fail(
        "draft",
        resource,
        ErrorCode.INVALID_ARGS,
        f"{name} 이 요청 원문에 없는 말이다. 원문에서 그대로 복사해 다시 호출한다.",
    )


def _md(day: date | None) -> str:
    return f"{day.month}/{day.day}" if day else "-"
