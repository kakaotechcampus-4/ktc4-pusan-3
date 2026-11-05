"""`care_handoff` — 투약의뢰서 · 응급 카드. 모델 0회.

남에게 아이를 맡길 때 매번 손으로 적는 정보를 앱이 이미 갖고 있다. 이 라벨은 **등록된 값을
고정 서식에 옮기는 것**이 전부라 새로 판단하는 것이 하나도 없다. 모델이 문장을 다듬으면
복약 문구가 미세하게 바뀌므로 모델을 부르지 않는다. 저장하지 않고 복사 · 공유만 한다.

    투약의뢰서   머리 아래에 아이 별명 한 줄, 진행 중 복약마다 약 종류 · 투약 시각 · 투약 기간 ·
                보관 · 복용 이유 · 특이사항 여섯 줄. 끝에 "보호자 확인 후 서명해 주세요" 가 늘
                붙는다(보호자 책임 서류). 진행 중 복약이 없으면 서식 대신 `notice.no_active_med`
    응급 카드    아이 기본 정보 · 알레르기 · 만성질환 · 복용 중인 약. 알레르기 · 만성질환은
                이름 뒤에 등록한 반응 · 대처 원문이 붙는다(없으면 이름만). **보호자 연락처는
                없다** — 담을 칸이 없고, 넣으려면 수집 항목이 늘어난다(루트 CLAUDE.md §2)

- 빈 칸은 `-` 다. 짐작해서 채우지 않는다. 특이사항은 보호자가 카드에서 적은 원문 그대로다.
  서식이 줄 단위라 원문의 줄바꿈만 공백 하나로 바꾼다(`_flat`) — 그 밖의 글자는 그대로다.
- 어느 서식인지는 표현 표로 가른다. 순서는 ① 서식 이름("의뢰서" 등) → ② 알레르기 · 응급 · 만성
  → ③ 넓은 투약 표현("투약" · "약 보낼" · "약 맡길") → ④ 그 밖에는 응급 카드다. "투약" 은 넓은
  말이라 "응급 카드에 투약 중인 약도 넣어줘" 를 투약의뢰서로 가져가면 안 되고, 투약의뢰서에는
  알레르기 칸이 없어서 알레르기를 말한 요청도 그쪽으로 가면 안 된다.
- 안전 조회(`health_safety`)가 실패하면 알레르기 · 만성질환 칸을 비우고 실패를 적는다.
  "없음" 으로 쓰지 않는다 — 조회 실패를 "등록한 것이 없다" 로 읽으면 맡긴 사람이 알레르기를 모른다.
- 투약의뢰서는 안전 정보를 쓰지 않으므로 조회 실패가 서식을 막지 않는다.
"""

import re
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
    CARD_MANAGEMENT,
    CARD_MEDS,
    CARD_REACTIONS,
    CARD_SAFETY_UNAVAILABLE,
    NOTICE_HEADER,
    NOTICE_LINE_CHILD,
    NOTICE_LINE_INDICATION,
    NOTICE_LINE_NOTE,
    NOTICE_LINE_PERIOD,
    NOTICE_LINE_STORAGE,
    NOTICE_LINE_TIME,
    NOTICE_LINE_TITLE,
    NOTICE_NO_ACTIVE_MED,
    NOTICE_SIGNATURE,
    READOUTS,
)
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.ports import HealthSafetyEntry, MedicationCourse
from app.rules.age import age_display

TASK = HealthTaskType.CARE_HANDOFF

# 서식 이름을 콕 집은 표현. 가장 먼저 본다 — 알레르기 약 얘기여도 "의뢰서" 를 달라는 말이다
NOTICE_FORM_WORDS: tuple[str, ...] = ("투약의뢰서", "투약 의뢰", "의뢰서")
# 투약의뢰서를 원한다는 표현. "투약" · "약 보낼" · "약 맡길" 은 넓은 말이라 아래 카드 표현에 밀린다
NOTICE_WORDS: tuple[str, ...] = (*NOTICE_FORM_WORDS, "투약", "약 보낼", "약 맡길")
# 응급 카드를 원한다는 표현. 서식 이름이 없으면 넓은 투약 표현보다 먼저 본다 — 투약의뢰서에는
# 알레르기 칸이 없어서, 알레르기 · 응급을 말하거나 카드를 달라는 요청, 기관이 아니라 사람(시터 ·
# 조부모)에게 맡기는 요청이 그쪽으로 가면 맡는 사람이 알레르기를 모른다. 둘 다 걸리지 않으면
# 응급 카드다
CARD_WORDS: tuple[str, ...] = (
    "응급",
    "알레르기",
    "만성",
    "카드",
    "시터",
    "조부모",
    "할머니",
    "할아버지",
)

_EMPTY = "-"  # 빈 칸 — 짐작해서 채우지 않는다
_LINE_BREAKS = re.compile(r"\s*[\r\n]+\s*")
_STORAGE = {"room": "실온", "fridge": "냉장"}


async def run_care_handoff(
    task: DomainTask, context: HealthContext, client: LLMClient | None
) -> DomainAgentResult:
    if _wants_notice(" ".join(task.request_texts)):
        readout = await _med_notice(context)
    else:
        readout = await _emergency_card(context)
    return result(TASK, "completed", readouts=(readout,))


def _wants_notice(text: str) -> bool:
    """서식 이름이 있으면 투약의뢰서, 없으면 카드 표현이 있을 때 카드, 그 밖의 투약 표현은 서식."""
    if any(word in text for word in NOTICE_FORM_WORDS):
        return True
    if any(word in text for word in CARD_WORDS):
        return False
    return any(word in text for word in NOTICE_WORDS)


async def _med_notice(context: HealthContext) -> Readout:
    courses = await _active(context)
    if not courses:
        return READOUTS.render(NOTICE_NO_ACTIVE_MED)  # 빈 서식을 만들지 않는다
    child = await context.ports.child.basic(child_id=context.child_id)
    blocks = ["\n".join(_notice_lines(course)) for course in courses]
    body = "\n".join(
        [
            READOUTS.render(NOTICE_HEADER, date=context.today.isoformat()).body,
            READOUTS.render(NOTICE_LINE_CHILD, nickname=child.nickname).body,  # 약마다 되풀이 안 함
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
        READOUTS.render(
            NOTICE_LINE_PERIOD,
            starts_on=course.starts_on.isoformat(),
            ends_on=course.ends_on.isoformat(),
        ).body,
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
                CARD_ALLERGY, items=_items(_safety_item(e) for e in active if e.kind == "allergy")
            ).body,
            READOUTS.render(
                CARD_CHRONIC,
                items=_items(_safety_item(e) for e in active if e.kind == "chronic_disease"),
            ).body,
        ]
    else:
        # 조회 실패 — 칸을 비우고 실패를 적는다. "없음" 이 아니다
        lines.append(READOUTS.render(CARD_SAFETY_UNAVAILABLE).body)
    meds = [_med_item(course) for course in await _active(context)]
    lines.append(READOUTS.render(CARD_MEDS, items=_items(meds)).body)
    return Readout(kind=READOUTS.get(CARD_HEADER).kind, body="\n".join(lines), authored_by="code")


def _med_item(course: MedicationCourse) -> str:
    title = _flat(course.title)
    return f"{title} ({_flat(course.dosage)})" if course.dosage else title


def _safety_item(entry: HealthSafetyEntry) -> str:
    """등록한 이름 뒤에 반응 · 대처 원문을 괄호로. 비었으면 이름만이다.

    원문의 글자는 바꾸지 않는다 — 앞뒤 공백을 떼고 줄바꿈만 공백 하나로 바꾼다(`_flat`). 카드는
    줄바꿈으로 줄을 가르므로 원문의 줄바꿈이 한 칸을 두 줄로 깨면 안 된다.
    """
    reactions = [text for text in (_flat(r).strip() for r in entry.reactions) if text]
    management = _flat(entry.management or "").strip()
    parts = []
    if reactions:
        parts.append(READOUTS.render(CARD_REACTIONS, items=" · ".join(reactions)).body)
    if management:
        parts.append(READOUTS.render(CARD_MANAGEMENT, text=management).body)
    label = _flat(entry.label)
    return f"{label} ({' / '.join(parts)})" if parts else label


def _flat(text: str) -> str:
    """줄바꿈(곁의 공백 포함)을 공백 하나로. 서식은 줄 단위라 원문의 줄바꿈이 한 칸을 둘로 깨면 안
    된다. 줄바꿈 말고는 바꾸지 않는다 — 안쪽 공백을 합치지 않는다."""
    return _LINE_BREAKS.sub(" ", text)


def _items(labels: Iterable[str]) -> str:
    """등록한 이름 원문을 이어 붙인다. 하나도 없으면 "등록된 내용이 없어요" 다.

    "없다" 는 판정이 아니라 등록한 것이 없다는 사실이다.
    """
    items = list(labels)
    return ", ".join(items) if items else READOUTS.render(CARD_EMPTY).body


def _text(value: str | None) -> str:
    return _flat(value) if value and value.strip() else _EMPTY


async def _active(context: HealthContext) -> list[MedicationCourse]:
    return await context.ports.medication.active_courses(
        child_id=context.child_id, today=context.today
    )
