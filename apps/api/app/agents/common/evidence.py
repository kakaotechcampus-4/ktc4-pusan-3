"""추천의 근거를 코드가 미리 줄 세운다.

프롬프트에 "확정 관심을 먼저 봐라"를 쓰면 테스트로 검증할 수 없고, tool 결과가 길어질수록
모델이 뒤쪽을 무시한다. 순서는 여기서 정하고 모델은 정렬된 목록을 받는다.

모델에게 보이지 않는 코드 tool 이다 (docs/agents/shared/Tool_공통.md §4).

    티어 1  confirmed  · polarity ∈ {+1, −1} · archived 제외
    티어 2  candidate  · polarity ∈ {+1, −1} · strength ≥ 임계
    티어 3  최근 14일 관찰 (polarity 무관)
    ── 전부 0행 ──  호출부가 kind="general"

**감쇠는 여기서 하지 않는다.** `profile_affinity` 는 Curator(백엔드 배치)가 관찰을 승격해
만들고, 오래된 관심은 Curator 가 `strength` 를 내리거나 `archived` 로 바꿔 이미 반영한다.
Agent 는 그 결과를 읽기만 한다 — 유예일을 여기서 다시 세면 값이 두 벌이 되고 한쪽만 갱신된다.

기피(−1)는 제외 필터가 아니라 근거다. "브로콜리를 싫어해서 이렇게 골랐어요"가 되어야 한다.
후보에서 지우는 것은 안전 필터의 일이고 이 함수는 순위만 매긴다.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from app.agents.common.refs import ChildRecordKind, Ref

AffinityState = Literal["candidate", "confirmed", "archived"]

# 티어 3 이 보는 관찰의 기간. affinity 는 무기한이다 — 의도된 비대칭이고,
# affinity 의 존재 이유가 장기 신호의 증류라서다. 감쇠는 Curator 의 strength 가 표현한다.
OBSERVATION_WINDOW_DAYS = 14

DEFAULT_STRENGTH_THRESHOLD = 0.5


@dataclass(frozen=True)
class AffinityRow:
    """`profile_affinity` 한 줄. 포트가 내부 dataclass 로 바꿔 넘긴다.

    Agent 는 이 행을 만들지 않는다. Curator 가 관찰에서 승격해 둔 것을 읽을 뿐이다.
    """

    id: UUID
    merge_key: str  # 사람이 읽는 라벨
    state: AffinityState
    polarity: int | None  # -1 기피 · +1 선호 · None 은 아직 모름
    strength: float
    last_observed_on: date


@dataclass(frozen=True)
class ObservationRow:
    """`observation_*` 한 줄."""

    id: UUID
    kind: ChildRecordKind  # observation_food · observation_activity …
    subject: str
    polarity: int  # -1 / 0 / +1. NOT NULL
    observed_on: date


@dataclass(frozen=True)
class RankedEvidence:
    ref: Ref
    tier: Literal[1, 2, 3]
    label: str
    polarity: int
    observed_on: date

    @property
    def is_avoidance(self) -> bool:
        """기피 근거인가. 인용했으면 `reason` 에 무엇을 피했는지 적어야 한다."""
        return self.polarity < 0


def rank_evidence(
    affinities: tuple[AffinityRow, ...],
    observations: tuple[ObservationRow, ...],
    *,
    today: date,
    strength_threshold: float = DEFAULT_STRENGTH_THRESHOLD,
) -> tuple[RankedEvidence, ...]:
    """티어 순으로 줄 세운다. 빈 튜플이면 호출부가 `kind="general"` 로 간다.

    `polarity IS NULL` 인 candidate 는 여기서 빠진다. 선호인지 기피인지 모르는 신호라
    근거가 아니라 되물을 거리다 — `pick_followup()` 이 따로 집는다.
    """
    ranked: list[RankedEvidence] = []

    for row in affinities:
        if row.state == "archived" or row.polarity is None:
            continue
        if row.state == "confirmed":
            tier: Literal[1, 2, 3] = 1
        elif row.strength >= strength_threshold:
            tier = 2
        else:
            continue
        ranked.append(
            RankedEvidence(
                ref=Ref(kind="profile_affinity", id=row.id),
                tier=tier,
                label=row.merge_key,
                polarity=row.polarity,
                observed_on=row.last_observed_on,
            )
        )

    window_start = today - timedelta(days=OBSERVATION_WINDOW_DAYS)
    for observation in observations:
        if observation.observed_on < window_start:
            continue
        ranked.append(
            RankedEvidence(
                ref=Ref(kind=observation.kind, id=observation.id),
                tier=3,
                label=observation.subject,
                polarity=observation.polarity,
                observed_on=observation.observed_on,
            )
        )

    ranked.sort(key=lambda item: (item.tier, -item.observed_on.toordinal()))
    return tuple(ranked)


def pick_followup(affinities: tuple[AffinityRow, ...]) -> AffinityRow | None:
    """되물을 거리 하나. `candidate` 인데 선호인지 기피인지 모르는 것 중 신호가 가장 센 것.

    **되묻기는 한 번에 하나다.** 후보가 여럿이어도 하나만 낸다.
    """
    unknown = [row for row in affinities if row.state == "candidate" and row.polarity is None]
    if not unknown:
        return None
    return max(unknown, key=lambda row: (row.strength, row.last_observed_on))
