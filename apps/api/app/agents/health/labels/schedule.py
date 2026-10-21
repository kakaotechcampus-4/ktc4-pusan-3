"""`schedule_check` — 검진 차수 · 이 시기 권장 접종 안내와 알림 초안. 모델 0회.

요청 문장을 작은 표현 표로 셋으로 가른다.

    schedule  지금 · 다음 검진 차수와 이 시기 권장 접종을 안내한다
    alarm     같은 안내에 더해 다음 차수 시작일로 `EventRequest` 초안을 낸다
    context   "검진 때 뭐 챙겨가?" · 문진표 — 식사 · 복약 · 증상 기록이 다 있어야 해서 진료 준비
              이슈 전까지 `unsupported` 다. 차수 안내로 새지 않는다

계산은 `tools/checkup.py`(저장하지 않는다)이고 문구는 `health.readout.yaml` 이다. 접종은 맞았는지
모르므로 "놓쳤다" 를 말하지 않고 늘 `vaccine.disclaimer` 를 붙인다. 72개월부터는 검진만 끝난다 —
`checkup.aged_out` 한 줄을 먼저 내고, 접종은 표가 끝나는 만 12세까지 이어서 소개한다(만 4~6세 접종은
83개월까지다). `EventRequest` 가 실제 일정 초안이 되는 것은 pipeline 연결 이슈다 — 여기서는
결과에 실리는 것까지다.
"""

from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Literal

from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.result import DomainAgentResult, EventRequest
from app.agents.common.schemas.task import DomainTask
from app.agents.health.context import HealthContext
from app.agents.health.envelope import result
from app.agents.health.readouts import (
    CHECKUP_AGED_OUT,
    CHECKUP_CURRENT,
    CHECKUP_NEXT,
    CHECKUP_ORAL,
    CHECKUP_ORAL_ONLY,
    READOUTS,
    VACCINE_DISCLAIMER,
    VACCINE_DUE,
    VACCINE_NEXT,
    VACCINE_NONE_NOW,
)
from app.agents.health.reference import VaccineDose
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.tools.checkup import (
    RoundWindow,
    compute_checkup_schedule,
    compute_next_vaccines,
    compute_vaccine_schedule,
)

ScheduleIntent = Literal["schedule", "alarm", "context"]

# 알림 초안을 원한다는 표현. 그냥 "알려줘" · "미리 알려" · "전에 알려" 는 넣지 않는다 —
# "예방접종 일정 알려줘" 가 일정 초안을 만들고, "기간 끝나기 전에" 는 다음 차수 시작일과 뜻이 다르다
ALARM_WORDS: tuple[str, ...] = ("알림", "되면 알려", "되면 말해")
# 알림을 원하지 않는다는 표현이 같이 있으면 초안을 만들지 않는다. "알림장" 은 앱 말이 아니다
_NOT_ALARM: tuple[str, ...] = (
    "알림장",
    "꺼",
    "끄",
    "그만",
    "해제",
    "취소",
    "싫",
    "안받",
    "받지",
    "못받",
    "안와",
    "안왔",
    "왔는데",
)
# 접종 얘기만 있으면 검진 알림 초안이 아니다 (초안 제목이 "영유아 건강검진 N차" 다)
_VACCINE_WORDS: tuple[str, ...] = ("접종", "주사", "백신")
# 검진 맥락 요약을 원한다는 표현 — 가져갈 것 · 문진표. 알림보다 먼저 본다. 맨 "챙겨" · "준비" 는
# 넣지 않는다 ("검진 일정 챙겨줘" 가 답 없이 unsupported 로 간다)
CONTEXT_WORDS: tuple[str, ...] = (
    "챙겨가",
    "챙길",
    "뭐챙",
    "준비물",
    "준비할",
    "가져가",
    "가져갈",
    "문진",
)

_ALARM_AT = time(9, 0)


def _squash(text: str) -> str:
    return "".join(text.split())


def _is_alarm(squashed: str) -> bool:
    if not any(_squash(word) in squashed for word in ALARM_WORDS):
        return False
    if any(word in squashed for word in _NOT_ALARM):
        return False
    return not (any(word in squashed for word in _VACCINE_WORDS) and "검진" not in squashed)


def schedule_intent(texts: Sequence[str]) -> ScheduleIntent:
    """요청 문장을 하나씩 본다. 맥락이 알림보다 먼저다."""
    squashed = [_squash(text) for text in texts]
    if any(_squash(word) in text for text in squashed for word in CONTEXT_WORDS):
        return "context"
    if any(_is_alarm(text) for text in squashed):
        return "alarm"
    return "schedule"


def _day(value: date) -> str:
    return f"{value.year}년 {value.month}월 {value.day}일"


def _range(window: RoundWindow) -> dict[str, str]:
    return {
        "round": str(window.round.round),
        "starts_on": _day(window.starts_on),
        "ends_on": _day(window.ends_on),
    }


def _names(doses: Sequence[VaccineDose]) -> str:
    return " · ".join(f"{dose.vaccine} {dose.dose}" for dose in doses)


def _readout(key: str, **values: str) -> Readout:
    return READOUTS.render(key, **values)


async def run_schedule_check(
    task: DomainTask, context: HealthContext, client: LLMClient | None
) -> DomainAgentResult:
    """생일과 오늘(아이 타임존)만으로 안내한다. `client` 는 받지만 쓰지 않는다 — 모델 0회."""
    label = HealthTaskType.SCHEDULE_CHECK
    intent = schedule_intent(task.request_texts)
    if intent == "context":
        return result(label, "unsupported")

    birth_date = context.state.birth_date
    assert birth_date is not None  # build_gate 가 채운다
    today = context.today

    checkup = compute_checkup_schedule(birth_date, today)
    readouts: list[Readout] = []
    if checkup.aged_out:
        # 검진은 끝났지만 접종은 계속된다. 차수 · 구강검진 안내는 없다
        readouts.append(READOUTS.render(CHECKUP_AGED_OUT))
    else:
        if checkup.current:
            readouts.append(_readout(CHECKUP_CURRENT, **_range(checkup.current)))
        if checkup.oral:
            # 일반검진 기간 밖의 구강검진을 "같은 시기" 라고 하면 다음 차수와 같이 받는 줄 안다
            key = CHECKUP_ORAL if checkup.current else CHECKUP_ORAL_ONLY
            readouts.append(_readout(key, **_range(checkup.oral)))
        if checkup.next:
            readouts.append(_readout(CHECKUP_NEXT, **_range(checkup.next)))

    doses = compute_vaccine_schedule(birth_date, today)
    if doses:
        readouts.append(_readout(VACCINE_DUE, vaccines=_names(doses)))
    else:
        readouts.append(READOUTS.render(VACCINE_NONE_NOW))
    upcoming = compute_next_vaccines(birth_date, today)
    if upcoming:
        readouts.append(
            _readout(
                VACCINE_NEXT, starts_on=_day(upcoming.starts_on), vaccines=_names(upcoming.doses)
            )
        )
    readouts.append(READOUTS.render(VACCINE_DISCLAIMER))

    events: tuple[EventRequest, ...] = ()
    if intent == "alarm" and checkup.next:
        events = (
            EventRequest(
                title=f"영유아 건강검진 {checkup.next.round.round}차",
                starts_at=datetime.combine(checkup.next.starts_on, _ALARM_AT, context.timezone),
                category="health",
                all_day=True,
            ),
        )
    return result(label, "completed", readouts=tuple(readouts), event_requests=events)
