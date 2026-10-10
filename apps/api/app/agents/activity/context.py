"""Activity tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id, 월령, 위치 격자)과 날짜 계산의 기준(now, timezone),
읽기 포트를 한 곳에 모은다. tool 인자에는 이 값들이 노출되지 않는다.

월령은 context가 값으로 들고 있지 않는다. `build_gate()`가 매 run
`ports.profile.birth_date`에서 `LifeStage`를 계산해 `Gate`로 넘긴다.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, tzinfo
from uuid import UUID

from app.agents.activity.store.ports import (
    ActivityPorts,
    CoarseLocation,
    PlaceRow,
    SafetyEntry,
    SafetyLookupError,
    WeatherGrid,
)
from app.agents.common.datetime_rules import today_of
from app.agents.common.evidence import RankedEvidence
from app.agents.common.gate import Gate, SafetyState
from app.agents.common.suggestion import SuggestionDraft
from app.rules.age import life_stage


@dataclass
class ActivityRunState:
    """run 하나 동안만 사는 값. tool 이 읽고 쓴다.

    출력 tool 이 `EvidencePick.id`를 공통 `EvidenceCitation`으로 바꿀 때 이 표를 본다 —
    이번 run 에서 실제로 조회한 것만 인용할 수 있다. 없는 id 는 후보를 거절한다 (D6).
    `place_name` 도 같다 — `search_nearby_places` 가 돌려준 이름만 쓸 수 있다.
    """

    seen_evidence: dict[UUID, RankedEvidence] = field(default_factory=dict)
    seen_places: dict[str, PlaceRow] = field(default_factory=dict)  # 이름 → 장소
    gate: Gate | None = None
    # build_gate 가 읽은 health_safety 행. 출력 검증의 안전 필터는 이 값만 쓰고 다시 읽지 않는다.
    # None 은 아직 안 읽었다(build_gate 전)는 뜻이고, 동의가 없으면 빈 튜플이다
    safety_entries: tuple[SafetyEntry, ...] | None = None
    # 출력 검증을 통과한 추천. run() 이 DomainAgentResult.suggestions 로 넘긴다
    suggestions: tuple[SuggestionDraft, ...] = ()
    # suggestions 와 같은 순서로 짝지은 위험 용어 경고 문구(사전의 warning_text 상수).
    # 화면에 어떻게 실을지는 아직 정하지 않았다 — 공통 SuggestionDraft 에 칸이 없다
    warnings: tuple[tuple[str, ...], ...] = ()
    # 안전 필터로 빠진 후보의 content. run() 이 재호출할 때 제외 목록으로만 넣는다 —
    # 걸린 사유는 넣지 않는다 (Tool_공통.md §5-2)
    excluded: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActivityContext:
    child_id: UUID
    run_id: str
    now: datetime  # timezone이 붙은 현재 시각
    timezone: tzinfo
    ports: ActivityPorts
    # 위치 동의나 권한이 없으면 None. 저장하지 않고 이 요청 안에서만 쓴다 (4-3).
    # 🚨 위치정보 동의는 서버가 여기서 다시 확인하지 않는다 — 채우는 쪽(app/api)이 동의가 있을
    # 때만 넣는다. Activity 는 값이 있으면 쓰고 없으면 장소 조회를 닫는다
    location: CoarseLocation | None = None
    state: ActivityRunState = field(default_factory=ActivityRunState)

    @property
    def today(self) -> date:
        return today_of(self.now, self.timezone)

    @property
    def grid(self) -> WeatherGrid | None:
        """날씨 포트에 넘길 기상청 격자. 좌표 대신 이것만 밖으로 나간다."""
        return self.location.grid if self.location is not None else None

    def for_task(self) -> "ActivityContext":
        """task 하나 몫의 context. run state 만 새로 만든다.

        pipeline 이 Agent 를 부르기 전에 늘 부른다 (`DomainContext` 프로토콜). task 둘이 동시에
        돌아도 `seen_evidence` · `seen_places` · `suggestions` 가 섞이지 않는다.
        """
        return replace(self, state=ActivityRunState())


async def build_gate(context: ActivityContext, *, outdoor_ok: bool) -> Gate:
    """이번 run의 Gate(registry `tools_for`의 입력).

    - birth_date → `life_stage()`로 `LifeStage`를 계산한다.
    - 동의(`consent_child_health`)가 없으면 `health_safety`를 아예 읽지 않는다.
      읽을 것이 없는 상태라 allergy_states 와 `state.safety_entries` 가 빈 튜플이다.
    - 🚨 읽은 행은 `context.state.safety_entries` 에 담는다. run 당 한 번만 읽고, 출력 검증의
      안전 필터는 이 값을 쓴다 — 다시 읽지 않는다 (SafetyReader 설명).
    - 동의가 있는데 조회가 실패하면 `safety_ok=False`. 실패를 빈 목록으로 숨기지 않는다.
      이 값이면 registry 가 Activity 를 통째로 닫는다 — 모델 0회 (D7).
    - `has_location` 은 좌표가 있는가. 없으면 장소 조회가 닫힌다.
    - `outdoor_ok` 는 호출부가 날씨를 먼저 조회해 넘긴다. 장소 조회가 실내 종류로
      좁혀지는지가 이 값에 걸려 있어서다 (3-2).
    """
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    stage = life_stage(birth_date, context.today)
    consent = await ports.consent.child_health_granted(child_id=context.child_id)

    safety_ok = True
    allergy_states: tuple[SafetyState, ...] = ()
    entries: tuple[SafetyEntry, ...] | None = ()
    if consent:
        try:
            entries = tuple(await ports.safety.activity_safety(child_id=context.child_id))
        except SafetyLookupError:
            safety_ok = False
            entries = None  # 읽지 못했다. 빈 튜플로 두면 안전 필터가 "거를 것 없음"으로 읽는다
        else:
            allergy_states = tuple(entry.status for entry in entries if entry.kind == "allergy")
    context.state.safety_entries = entries

    return Gate(
        stage=stage,
        consent_child_health=consent,
        safety_ok=safety_ok,
        allergy_states=allergy_states,
        has_location=context.location is not None,
        outdoor_ok=outdoor_ok,
    )
