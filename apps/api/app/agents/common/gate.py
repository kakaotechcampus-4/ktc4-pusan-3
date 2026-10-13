"""registry 가 tool 을 여닫을 때 보는 값.

전부 **발화가 아니라 아이 데이터·환경에서** 온다. 보호자가 "우리 애 두 돌"이라고 말해도
게이팅은 `stage.months` 를 쓴다 (docs/agents/shared/Tool_공통.md §3).

성별은 축이 아니다. 수집도 사용도 하지 않는다.

안전 조회(`read_safety_basis`)도 여기 있다 — 그 결과가 `Gate.safety_ok` · `allergy_states` 가 된다.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, Literal, Protocol, TypeVar

from app.agents.common.ports import SafetyLookupError
from app.rules.age import LifeStage

# `health_safety.status`(active/retracted/none)에 "매칭되는 행 없음" 인 unknown 을 더한 값.
# unknown 은 DB 에 저장되지 않는다 — 아이를 만들 때 19종을 미리 깔지 않고,
# 보호자가 답한 항목만 none/active 행이 된다 (10/4 확정안). 19종 밖은 추가할 때 active 다.
#
# **거르는 것은 active 뿐이다.** unknown 은 추천을 막지 않고 "이 알레르기가 있는지
# 확인해 주세요" 안내만 붙인다 — 모르는 항목 때문에 추천을 통째로 닫으면
# 답을 미룬 보호자가 서비스를 못 쓴다. 건강정보 동의를 안 해 0행인 경우도 같다.
#
# TODO: 10/4 확정안부터 unknown 은 DB 값이 아니라 "매칭되는 행이 없음" 이다. DB 를 붙이면
#   allergy_states 에 unknown 이 올 일이 없어 allergy_unconfirmed 가 늘 False 가 된다.
#   확인 안내는 추천 승인 때 suggestion.allergens 와 health_safety 를 대조하는 쪽으로 옮긴다.
SafetyState = Literal["active", "retracted", "none", "unknown"]


@dataclass(frozen=True)
class DataReady:
    """그 아이에게 그 데이터가 있는가. 없으면 그 데이터를 읽는 tool 이 빠진다."""

    daycare_meal: bool = False  # 급식 행 — Food 의 급식 조회·갱신
    notice: bool = False  # 기관 공지 — Growth 의 lookup_notice
    book_api: bool = True  # 도서 API 가용 — Growth 의 도서 라벨
    places: bool = False  # 장소 어댑터가 주입됐나 — Activity 의 search_nearby_places
    weather: bool = False  # 날씨 어댑터가 주입됐나 — Activity 의 lookup_weather


@dataclass(frozen=True)
class Gate:
    """`tools_for(task_type, gate)` 의 입력.

    `stage` 가 월령을 들고 있으므로 `age_months` 를 따로 넘기지 않는다.
    """

    stage: LifeStage
    consent_child_health: bool  # consent(scope=child_health) 최신 행이 granted 인가.
    # False 면 health_safety·child_growth_log 를 못 읽는다 — allergy_states 가 빈 튜플로 온다.
    # 조회 실패가 아니라 읽을 것이 없는 상태다. Food 는 닫히지 않고 Health 는 전 라벨이 닫힌다.
    safety_ok: bool  # health_safety 조회에 성공했는가 (실패는 0행과 다르다)
    allergy_states: tuple[SafetyState, ...] = ()  # kind='allergy' 행들의 state (F-4)
    growth_log_count: int = 0  # child_growth_log 측정 건수
    has_location: bool = False
    outdoor_ok: bool = True
    data: DataReady = field(default_factory=DataReady)

    @property
    def allergy_filter_ready(self) -> bool:
        """알레르기 필터를 걸 수 있는가. Food 의 식단 추천이 이 값으로 열리고 닫힌다.

        **막는 것은 조회 실패뿐이다.** 실패를 빈 목록으로 숨기지 않는다 (루트 CLAUDE.md §2).

        0행도 연다. 건강정보 동의를 안 한 보호자는 `health_safety` 에 행이 쌓이지 않는데,
        그렇다고 식단을 못 받으면 안 된다 — 알레르기 없는 아이 기준의 일반 식단으로 간다.
        `active` 행이 없으니 거를 것도 없다.

        `unknown` 도 막지 않는다. 아직 안 물어본 항목일 뿐이고, 추천은 내보내되
        `allergy_unconfirmed` 로 확인 안내를 함께 띄운다 (F-4).
        `retracted` 는 필터에서 `none` 과 같다 — 보호자가 취소했고 지금은 해당 없음이다.
        """
        return self.safety_ok

    @property
    def allergy_unconfirmed(self) -> bool:
        """아직 안 물어본 알레르기 항목이 있는가.

        참이면 추천과 함께 "이 알레르기가 있는지 확인해 주세요" 안내를 띄운다.
        항목 이름이 필요하면 tool 이 19종 중 행이 없는 것을 따로 본다 — Gate 는 상태만 든다.
        """
        return "unknown" in self.allergy_states


# 안전 조회
class SafetyRowLike(Protocol):
    """`read_safety_basis` 가 보는 칸. `SafetyEntry` 는 Agent 마다 따로 둔다 (#319 리뷰)."""

    @property
    def kind(self) -> str: ...

    @property
    def status(self) -> Literal["active", "retracted", "none"]: ...


E = TypeVar("E", bound=SafetyRowLike)


@dataclass(frozen=True)
class SafetyBasis(Generic[E]):
    """`build_gate` 가 task 시작에 한 번 읽은 health_safety.

    entries 가 None 이면 조회 실패다. () 는 동의가 없어 읽지 않았거나 등록한 것이 없는 것 —
    거를 것이 없는 상태라 실패와 다르다. 둘을 섞으면 안전 필터가 실패를 "거를 것 없음" 으로 읽는다.
    """

    entries: tuple[E, ...] | None

    @property
    def ok(self) -> bool:
        """조회에 성공했는가 — `Gate.safety_ok` 에 그대로 넣는다."""
        return self.entries is not None

    @property
    def allergy_states(self) -> tuple[SafetyState, ...]:
        """kind='allergy' 행들의 status, 읽은 순서대로. 실패면 빈 튜플이다."""
        return tuple(entry.status for entry in self.entries or () if entry.kind == "allergy")


async def read_safety_basis(
    read: Callable[[], Awaitable[Sequence[E]]], *, consent: bool
) -> SafetyBasis[E]:
    """health_safety 를 run 당 한 번 읽는다. Food · Activity 의 `build_gate` 가 쓴다.

    - 동의가 없으면 `read` 를 부르지 않는다 — 읽을 것이 없는 상태다.
    - `SafetyLookupError` 만 실패(None)로 바꾼다. 그 밖의 예외는 그대로 올린다 — 버그를 조회
      실패로 바꾸지 않고, 20초 제한의 `CancelledError` 를 삼키지 않는다 (Agent_공통규약 §7).
    - 받은 행은 거르지 않는다. 어떤 kind · status 를 읽을지는 각 Agent 의 `SafetyReader` 가 정한다.
    """
    if not consent:
        return SafetyBasis(entries=())
    try:
        rows = await read()
    except SafetyLookupError:
        return SafetyBasis(entries=None)
    return SafetyBasis(entries=tuple(rows))
