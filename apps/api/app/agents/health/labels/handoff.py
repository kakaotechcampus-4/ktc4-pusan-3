"""`care_handoff` — 투약의뢰서 · 응급 카드. 모델 0회.

남에게 아이를 맡길 때 매번 손으로 적는 정보를 앱이 이미 갖고 있다. 이 라벨은 **등록된 값을
고정 서식에 옮기는 것**이 전부라 새로 판단하는 것이 하나도 없다. 모델이 문장을 다듬으면
복약 문구가 미세하게 바뀌므로 모델을 부르지 않는다. 저장하지 않고 복사 · 공유만 한다.

    투약의뢰서   진행 중 복약마다 약 종류 · 투약 시각 · 보관 · 복용 이유 · 특이사항 다섯 줄. 끝에
                "보호자 확인 후 서명해 주세요" 가 늘 붙는다(보호자 책임 서류). 진행 중 복약이 없으면
                서식 대신 `notice.no_active_med`
    응급 카드    아이 기본 정보 · 알레르기 · 만성질환 · 복용 중인 약. **보호자 연락처는 없다** —
                담을 칸이 없고, 넣으려면 수집 항목이 늘어난다(루트 CLAUDE.md §2)

- 빈 칸은 `-` 다. 짐작해서 채우지 않는다. 특이사항은 보호자가 카드에서 적은 원문 그대로다.
- 안전 조회(`health_safety`)가 실패하면 알레르기 · 만성질환 칸을 비우고 실패를 적는다.
  "없음" 으로 쓰지 않는다 — 조회 실패를 "등록한 것이 없다" 로 읽으면 맡긴 사람이 알레르기를 모른다.
- 투약의뢰서는 안전 정보를 쓰지 않으므로 조회 실패가 서식을 막지 않는다.
"""

from collections.abc import Iterable

from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.health.context import HealthContext
from app.agents.health.envelope import result
from app.agents.health.readouts import (
    CARD_ALLERGY,
    CARD_CHILD,
    CARD_CHRONIC,
    CARD_EMPTY,
    CARD_HEADER,
    CARD_MEDS,
    CARD_SAFETY_UNAVAILABLE,
    NOTICE_HEADER,
    NOTICE_LINE_INDICATION,
    NOTICE_LINE_NOTE,
    NOTICE_LINE_STORAGE,
    NOTICE_LINE_TIME,
    NOTICE_LINE_TITLE,
    NOTICE_NO_ACTIVE_MED,
    NOTICE_SIGNATURE,
    READOUTS,
)
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.ports import MedicationCourse
from app.rules.age import age_display

TASK = HealthTaskType.CARE_HANDOFF

# 투약의뢰서를 원한다는 표현. 걸리지 않으면 응급 카드다
NOTICE_WORDS: tuple[str, ...] = ("투약의뢰서", "투약 의뢰", "의뢰서")

_EMPTY = "-"  # 빈 칸 — 짐작해서 채우지 않는다
_STORAGE = {"room": "실온", "fridge": "냉장"}


async def run_care_handoff(
    task: DomainTask, context: HealthContext, client: LLMClient | None
) -> DomainAgentResult:
    text = " ".join(task.request_texts)
    if any(word in text for word in NOTICE_WORDS):
        readout = await _med_notice(context)
    else:
        readout = await _emergency_card(context)
    return result(TASK, "completed", readouts=(readout,))


async def _med_notice(context: HealthContext) -> Readout:
    courses = await _active(context)
    if not courses:
        return READOUTS.render(NOTICE_NO_ACTIVE_MED)  # 빈 서식을 만들지 않는다
    blocks = ["\n".join(_notice_lines(course)) for course in courses]
    body = "\n".join(
        [
            READOUTS.render(NOTICE_HEADER, date=context.today.isoformat()).body,
            "\n\n".join(blocks),  # 약 사이는 빈 줄
            READOUTS.render(NOTICE_SIGNATURE).body,  # 늘 붙는다 — 보호자 책임 서류
        ]
    )
    return Readout(kind=READOUTS.get(NOTICE_HEADER).kind, body=body, authored_by="code")


def _notice_lines(course: MedicationCourse) -> list[str]:
    times = " · ".join(sorted(f"{slot.scheduled_time:%H:%M}" for slot in course.pattern))
    storage = _STORAGE.get(course.storage or "", _EMPTY)
    return [
        READOUTS.render(NOTICE_LINE_TITLE, title=course.title, dosage=_text(course.dosage)).body,
        READOUTS.render(NOTICE_LINE_TIME, times=times or _EMPTY).body,
        READOUTS.render(NOTICE_LINE_STORAGE, storage=storage).body,
        READOUTS.render(NOTICE_LINE_INDICATION, indication=_text(course.indication_text)).body,
        # 특이사항은 보호자가 적은 원문 그대로다
        READOUTS.render(NOTICE_LINE_NOTE, note=_text(course.note)).body,
    ]


async def _emergency_card(context: HealthContext) -> Readout:
    state = context.state
    gate = state.gate
    assert gate is not None  # run() 이 채운다
    child = await context.ports.child.basic(child_id=context.child_id)
    lines = [
        READOUTS.render(CARD_HEADER, date=context.today.isoformat()).body,
        READOUTS.render(
            CARD_CHILD,
            nickname=child.nickname,
            birth=child.birth_date.isoformat(),
            age=age_display(child.birth_date, context.today),
        ).body,
    ]
    if gate.safety_ok:
        active = [entry for entry in state.safety_entries if entry.status == "active"]
        lines += [
            READOUTS.render(
                CARD_ALLERGY, items=_items(e.label for e in active if e.kind == "allergy")
            ).body,
            READOUTS.render(
                CARD_CHRONIC, items=_items(e.label for e in active if e.kind == "chronic_disease")
            ).body,
        ]
    else:
        # 조회 실패 — 칸을 비우고 실패를 적는다. "없음" 이 아니다
        lines.append(READOUTS.render(CARD_SAFETY_UNAVAILABLE).body)
    meds = [_med_item(course) for course in await _active(context)]
    lines.append(READOUTS.render(CARD_MEDS, items=_items(meds)).body)
    return Readout(kind=READOUTS.get(CARD_HEADER).kind, body="\n".join(lines), authored_by="code")


def _med_item(course: MedicationCourse) -> str:
    return f"{course.title} ({course.dosage})" if course.dosage else course.title


def _items(labels: Iterable[str]) -> str:
    """등록한 이름 원문을 이어 붙인다. 하나도 없으면 "등록된 내용이 없어요" 다.

    "없다" 는 판정이 아니라 등록한 것이 없다는 사실이다.
    """
    items = list(labels)
    return ", ".join(items) if items else READOUTS.render(CARD_EMPTY).body


def _text(value: str | None) -> str:
    return value if value and value.strip() else _EMPTY


async def _active(context: HealthContext) -> list[MedicationCourse]:
    return await context.ports.medication.active_courses(
        child_id=context.child_id, today=context.today
    )
