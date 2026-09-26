"""프로필 상태 전이와 strength 변화 규칙.

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

상태는 (O, W, G, last_observed_on, today) 만으로 계산되는 공식 하나로 고정하고,
strength 는 Agent 추천 우선순위 용도로만 분리한다.
"""

from datetime import date
from typing import Literal

# ---------------------------------------------------------------------------
# 정책 상수 — 매직넘버 금지. 바꿀 때 여기 한 곳만 고친다.
# ---------------------------------------------------------------------------

PROMOTION_WINDOW_DAYS: int = 7
"""승격 윈도우. active 관찰을 세는 최근 일수."""

DEMOTION_WINDOW_DAYS: int = 21
"""하강 윈도우. 이 기간 동안 관찰이 없으면 archived."""

PROMOTION_THRESHOLD: int = 3
"""G 없이 confirmed 로 올라가는 데 필요한 최소 O (W=0 기준)."""

PROMOTION_THRESHOLD_WITH_G: int = 2
"""G 가 있으면 confirmed 로 올라가는 데 필요한 최소 O (W=0 기준)."""

STRENGTH_DEFAULT: float = 0.5
STRENGTH_MIN: float = 0.0
STRENGTH_MAX: float = 1.0

VERDICT_DECAY: dict[str, float] = {
    "wrong": 0.93,
    "outdated": 0.95,
    "need_more_observation": 0.97,
}
"""verdict 별 strength 곱셈 계수. 상태와 무관하게 동일."""

TRANSITION_MULTIPLIER: dict[tuple[str, str], float] = {
    ("candidate", "confirmed"): 1.10,
    ("confirmed", "candidate"): 0.90,
    ("confirmed", "archived"): 0.90,
    ("candidate", "archived"): 0.90,
    ("archived", "candidate"): 1.00,
    ("archived", "confirmed"): 1.10,
}
"""상태 전이 시 strength 곱셈 계수. 실제로 바뀐 경우에만 1회 적용."""

STRENGTH_EXPOSURE_THRESHOLD: float = 0.0
"""프론트 노출 기준. 이 값 미만이면 목록에서 숨긴다. [미정] 0.3 제안."""

PROFILE_LIMIT_PER_DOMAIN: int = 10
"""도메인당 confirmed 프로필 최대 노출 수."""

# ---------------------------------------------------------------------------
# 타입
# ---------------------------------------------------------------------------

ProfileStatus = Literal["candidate", "confirmed", "archived"]
_VALID_PROFILE_VERDICTS = frozenset(VERDICT_DECAY)


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------


def compute_profile_status(
    *,
    obs_count: int,
    wrong_count: int,
    has_signal: bool,
    last_observed_on: date,
    today: date,
) -> ProfileStatus:
    """프로필 상태를 결정한다. 위에서부터 먼저 맞는 조건이 적용된다.

    이슈 표기: O = obs_count, W = wrong_count, G = has_signal

    1. today - last_observed_on > DEMOTION_WINDOW_DAYS  →  archived
    2. obs_count >= PROMOTION_THRESHOLD + wrong_count  또는
       (obs_count >= PROMOTION_THRESHOLD_WITH_G + wrong_count 그리고 has_signal)  →  confirmed
    3. 그 외  →  candidate
    """
    days_since = (today - last_observed_on).days
    if days_since > DEMOTION_WINDOW_DAYS:
        return "archived"

    if obs_count >= PROMOTION_THRESHOLD + wrong_count:
        return "confirmed"
    if has_signal and obs_count >= PROMOTION_THRESHOLD_WITH_G + wrong_count:
        return "confirmed"

    return "candidate"


def apply_correction(strength: float, verdict: str) -> float:
    """verdict 에 따라 strength 를 곱셈 감소한다. 0~1 클램프."""
    if verdict not in _VALID_PROFILE_VERDICTS:
        raise ValueError(f"프로필에 적용할 수 없는 verdict: {verdict!r}")
    return _clamp(strength * VERDICT_DECAY[verdict])


def apply_transition(
    strength: float,
    prev_state: str,
    next_state: str,
) -> float:
    """상태가 실제로 바뀐 경우에만 strength 를 보정한다. 0~1 클램프."""
    if prev_state == next_state:
        return strength
    multiplier = TRANSITION_MULTIPLIER.get((prev_state, next_state), 1.0)
    return _clamp(strength * multiplier)


def _clamp(value: float) -> float:
    return max(STRENGTH_MIN, min(STRENGTH_MAX, value))
