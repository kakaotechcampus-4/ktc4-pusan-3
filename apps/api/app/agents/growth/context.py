"""Growth tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id, 월령, 동의)과 날짜 계산의 기준(now, timezone), 읽기 포트를
한 곳에 모은다. tool 인자에는 이 값들이 노출되지 않는다.

월령은 context가 값으로 들고 있지 않는다. `build_gate()`가 매 run
`ports.profile.birth_date`에서 `LifeStage`를 계산해 `Gate`로 넘긴다.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, tzinfo
from uuid import UUID

from app.agents.common.datetime_rules import today_of
from app.agents.common.gate import DataReady, Gate, SafetyState
from app.agents.growth.gating import EDUCATION_MIN_MONTH
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.ports import (
    BookRow,
    GrowthMeasurement,
    GrowthPorts,
    SafetyEntry,
    SafetyLookupError,
)
from app.agents.growth.tools.safety import SafetyRules, SafetyRulesUnavailable, load_safety_rules
from app.rules.age import life_stage


@dataclass
class GrowthRunState:
    """run 하나 동안만 사는 값. `build_gate` 가 읽은 것을 담고 tool 이 읽는다.

    건강정보 · 측정은 **run 당 한 번만 읽는다.** 출력 검증이 다시 읽으면 같은 run 안에서 게이트와
    필터가 다른 행을 볼 수 있다. 테스트가 읽기 횟수를 고정한다.
    """

    gate: Gate | None = None
    birth_date: date | None = None
    # growth_review + 동의가 있을 때만. 시간순 정렬은 compute_growth_delta 가 한다
    measurements: tuple[GrowthMeasurement, ...] = ()
    # learning_suggestion + 동의가 있을 때만. status 무관 전체 — 거르는 것은 안전 필터다
    safety_entries: tuple[SafetyEntry, ...] = ()
    # learning_suggestion · routine_coaching. 안전 사전과 매처
    rules: SafetyRules | None = None
    # book_suggestion. 이번 run 에서 `search_books` 가 돌려준 책 (정규화한 ISBN → 행).
    # `propose_books` 는 이 표에 있는 ISBN 만 통과시킨다 — 연령 필터에 걸러진 책은 들어오지 않는다
    seen_books: dict[str, BookRow] = field(default_factory=dict)


@dataclass(frozen=True)
class GrowthContext:
    child_id: UUID
    run_id: str
    now: datetime  # timezone이 붙은 현재 시각
    timezone: tzinfo
    ports: GrowthPorts
    # 안전 사전을 읽는 함수. 기본은 develop 의 사전으로 만든 임시 구현이다 (tools/safety.py)
    safety_rules: Callable[[], SafetyRules] = load_safety_rules
    state: GrowthRunState = field(default_factory=GrowthRunState)

    @property
    def today(self) -> date:
        return today_of(self.now, self.timezone)

    def for_task(self) -> "GrowthContext":
        """task 하나 몫의 context. run state 만 새로 만든다.

        pipeline 이 Agent 를 부르기 전에 늘 부른다 (`DomainContext` 프로토콜). task 둘이 동시에
        돌아도 측정 · 안전 행이 섞이지 않는다.
        """
        return replace(self, state=GrowthRunState())


async def build_gate(
    context: GrowthContext, task_type: GrowthTaskType, *, book_api_ok: bool = True
) -> Gate:
    """이번 run의 Gate(registry `tools_for`의 입력).

    - birth_date → `life_stage()`로 `LifeStage`를 계산한다. 월령은 보호자 시간대의 오늘에서 센다.
    - 건강정보는 **라벨이 필요할 때만** 읽는다. 동의가 없으면 아예 읽지 않는다.
        growth_review      동의가 있을 때 측정 **전부**(`child_growth_log`)
        learning_suggestion 동의가 있을 때 `health_safety`(allergy · environmental)
        그 밖                읽지 않는다 — 루틴 · 도서는 건강정보를 쓰지 않는다
    - 읽다가 실패하면 빈 목록이 아니라 닫는다. 안전 조회 실패는 `safety_ok=False` 다
      (교육만 닫힌다).
      안전 사전을 못 읽은 것도 같다 — 빈 사전으로 통과시키지 않는다. 측정 읽기 실패는 예외를 그대로
      올린다 ("측정 0건" 으로 읽히면 안 된다).
    - 교육이 0–11개월이면 어차피 `closed.infant_learning` 으로 닫혀서 안전 정보도 읽지 않는다.
    - `book_api_ok` 는 호출부가 도서 API 상태를 넘긴다. 도서 포트(인증키)가 없으면 닫힌다.
    """
    ports = context.ports
    state = context.state
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    stage = life_stage(birth_date, context.today)
    consent = await ports.consent.child_health_granted(child_id=context.child_id)
    state.birth_date = birth_date

    safety_ok = True
    allergy_states: tuple[SafetyState, ...] = ()
    growth_log_count = 0
    notice = False

    if task_type is GrowthTaskType.LEARNING_SUGGESTION and stage.months >= EDUCATION_MIN_MONTH:
        try:
            state.rules = context.safety_rules()
            entries = await ports.safety.growth_safety(child_id=context.child_id) if consent else []
        except (SafetyRulesUnavailable, SafetyLookupError):
            safety_ok = False
            state.rules = None
        else:
            state.safety_entries = tuple(entries)
            allergy_states = tuple(entry.status for entry in entries if entry.kind == "allergy")
        if ports.notice is not None:
            notice = await ports.notice.has_notice(child_id=context.child_id)
    elif task_type is GrowthTaskType.ROUTINE_COACHING:
        state.rules = context.safety_rules()  # 음식 용어 스캔용. 실패는 그대로 올린다
    elif task_type is GrowthTaskType.GROWTH_REVIEW and consent:
        measurements = await ports.growth_log.measurements(child_id=context.child_id)
        state.measurements = tuple(measurements)
        growth_log_count = len(measurements)

    return Gate(
        stage=stage,
        consent_child_health=consent,
        safety_ok=safety_ok,
        allergy_states=allergy_states,
        growth_log_count=growth_log_count,
        data=DataReady(notice=notice, book_api=book_api_ok and ports.books is not None),
    )
