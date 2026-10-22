"""Health tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id, 월령, 동의)과 날짜 계산의 기준(now, timezone), 읽기 포트를
한 곳에 모은다. tool 인자에는 이 값들이 노출되지 않는다.

월령은 context가 값으로 들고 있지 않는다. `build_gate()`가 매 run
`ports.profile.birth_date`에서 `LifeStage`를 계산해 `Gate`로 넘긴다.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, tzinfo
from typing import Literal
from uuid import UUID

from app.agents.common.age import life_stage
from app.agents.common.datetime_rules import today_of
from app.agents.common.gate import Gate, SafetyState
from app.agents.common.readout import Readout
from app.agents.common.refs import Ref
from app.agents.common.result import DomainPending
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.store.ports import HealthPorts, HealthSafetyEntry, SafetyLookupError
from app.core.medication_draft import MedicationDraft

# health_safety 를 쓰는 라벨. 그 밖의 라벨은 동의가 있어도 읽지 않는다
SAFETY_TASKS: frozenset[HealthTaskType] = frozenset(
    {HealthTaskType.VISIT_SUMMARY, HealthTaskType.SCHEDULE_CHECK, HealthTaskType.CARE_HANDOFF}
)


VisitSection = Literal["symptom", "given", "medication", "meal"]


@dataclass(frozen=True)
class VisitLine:
    """진료 준비 정리의 한 줄. 글은 코드가 기록 원문 · 등록값에서 그린다 — 모델이 쓰지 않는다."""

    section: VisitSection
    day: date  # 정렬 기준 — 증상은 에피소드 첫날, 그 밖은 그 기록의 날
    body: str = field(repr=False)  # 원문이 들어 있다. 로그에 싣지 않는다
    refs: tuple[Ref, ...]


@dataclass
class HealthRunState:
    """run 하나 동안만 사는 값. `build_gate` 가 읽은 것을 담고 tool 이 읽는다.

    건강정보는 **run 당 한 번만 읽는다.** 다시 읽으면 같은 run 안에서 게이트와 tool 이 다른 행을
    볼 수 있다. 테스트가 읽기 횟수를 고정한다.
    """

    gate: Gate | None = None
    birth_date: date | None = None
    # SAFETY_TASKS + 동의가 있을 때만. active 행의 kind 전부 — 알레르기만이 아니다.
    # 조회에 실패하면 비어 있다 — 그때는 `gate.safety_ok` 가 False 다 ("없음" 과 구분한다)
    safety_entries: tuple[HealthSafetyEntry, ...] = ()
    # 요청 원문. 모델이 복사한 span 이 원문의 부분 문자열인지 tool 이 본다 — 라벨 실행기가 넣는다
    request_texts: tuple[str, ...] = ()
    # tool 이 쌓은 결과. 라벨 실행기가 마지막에 `DomainAgentResult` 로 옮긴다
    drafts: list[MedicationDraft] = field(default_factory=list)
    # 수정 초안은 일정처럼 한 약에 한 장으로 쌓인다. draft_id → 처음 값과 실제로 달라진 필드 이름
    # (payload 에는 싣지 않는다 — 화면은 before 와 비교한다) · 그 초안의 확인 문구(다시 고치면 교체)
    changed: dict[str, tuple[str, ...]] = field(default_factory=dict)
    confirms: dict[str, Readout] = field(default_factory=dict)
    readouts: list[Readout] = field(default_factory=list)
    # 진료 준비 조회 tool 이 그린 줄. 짧은 key("s1" · "g1" · "f1" · "c1") → 코드가 기록에서 그린 줄.
    # 모델은 key · 날짜 · 증상 코드만 보고 고른다 — 원문 · 약 이름 · 용량은 모델로 가지 않는다
    # (#551).
    # 출력 tool 은 여기 있는 key 만 줄로 낸다 — 이번 run 이 조회하지 않은 것은 근거가 아니다
    visit_lines: dict[str, VisitLine] = field(default_factory=dict, repr=False)
    # (구획, 기록 정체) → key. 같은 조회를 다시 불러도 같은 key 를 준다
    visit_keys: dict[tuple[str, object], str] = field(default_factory=dict, repr=False)
    question: str | None = None
    pending: DomainPending | None = None  # 되묻고 멈춘 자리. question 과 같이 채워진다


@dataclass(frozen=True)
class HealthContext:
    child_id: UUID
    run_id: str
    now: datetime  # timezone이 붙은 현재 시각
    timezone: tzinfo
    ports: HealthPorts
    state: HealthRunState = field(default_factory=HealthRunState)
    # 이전 run 이 되묻고 멈춘 자리. 연결 이슈가 reply_to 로 받아 넣는다 — 있으면 모델 없이 이어 간다
    pending: DomainPending | None = None

    @property
    def today(self) -> date:
        return today_of(self.now, self.timezone)

    def for_task(self) -> "HealthContext":
        """task 하나 몫의 context. run state 만 새로 만든다.

        pipeline 이 Agent 를 부르기 전에 늘 부른다 (`DomainContext` 프로토콜). task 둘이 동시에
        돌아도 안전 행이 섞이지 않는다.
        """
        return replace(self, state=HealthRunState())


async def build_gate(context: HealthContext, task_type: HealthTaskType) -> Gate:
    """이번 run의 Gate(registry 의 입력).

    - birth_date → `life_stage()`로 `LifeStage`를 계산한다. 월령은 보호자 시간대의 오늘에서 센다.
    - 동의가 없으면 건강정보를 읽지 않는다. 동의가 있어도 `SAFETY_TASKS` 가 아니면 읽지 않는다.
    - 읽다가 `SafetyLookupError` 가 나면 `safety_ok=False` 다. 빈 목록으로 바꾸지 않는다 —
      "알레르기 없음" 과 "읽지 못함" 은 다르다. 닫을지 경고를 붙일지는 라벨이 정한다.
    """
    ports = context.ports
    state = context.state
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    stage = life_stage(birth_date, context.today)
    consent = await ports.consent.child_health_granted(child_id=context.child_id)
    state.birth_date = birth_date

    safety_ok = True
    allergy_states: tuple[SafetyState, ...] = ()
    if consent and task_type in SAFETY_TASKS:
        try:
            entries = await ports.safety.health_safety(child_id=context.child_id)
        except SafetyLookupError:
            safety_ok = False
        else:
            state.safety_entries = tuple(entries)
            allergy_states = tuple(entry.status for entry in entries if entry.kind == "allergy")

    return Gate(
        stage=stage,
        consent_child_health=consent,
        safety_ok=safety_ok,
        allergy_states=allergy_states,
    )
