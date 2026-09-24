"""연결 단계. 벡터는 있고 연결이 없는 관찰을 Profile 에 붙이거나 새 candidate 를 만든다.

관찰을 저장된 순서대로 한 건씩 처리한다. 앞에서 만든 Profile 이 다음 관찰의 후보가 된다.
    1. 이름이 같은 Profile (공백을 모두 지운 subject == 공백을 모두 지운 merge_key)
    2. 코사인 유사도가 가장 높은 Profile. threshold 이상일 때만
    3. 둘 다 없으면 새 candidate (merge_key = subject, 벡터는 관찰 것을 그대로)
후보는 같은 아이 · 도메인 · polarity 뿐이다. archived 도 똑같이 비교한다.

바꾸는 것은 관찰의 affinity_id 와 새 Profile 뿐이다.
Profile 의 state · strength · last_observed_on 은 건드리지 않는다 — 다시 승격할지는
승격 규칙이 정한다. 기존 Profile 에 연결할 때 last_observed_on 을 누가 갱신할지는
PR 에서 제안한다 (연결된 active 관찰 중 가장 늦은 observed_on 으로 다시 계산하는 안).

벡터가 잘못됐으면 유사도 미달과 구분해 보류한다. 새 candidate 를 만들면 잘못된 벡터가
퍼지거나, 원래 붙을 Profile 옆에 중복이 생긴다. 보류한 관찰은 affinity_id 가 비어 있어
다음 실행이 다시 시도한다.
"""

import logging
import math
from dataclasses import dataclass
from typing import Literal, TypeGuard
from uuid import UUID

from app.agents.curator.embedding.ports import (
    CuratorStore,
    ObservationItem,
    ObservationKey,
    ProfileItem,
)

logger = logging.getLogger(__name__)

# 임시값. 임계값 실험(S5)에서 정한다
# 참고 실측(text-embedding-3-small): 딸기–생딸기 0.58 · 딸기–레고 0.25 · 딸기–블루베리 0.16
DEFAULT_THRESHOLD = 0.5

LinkStatus = Literal["linked", "created", "held"]
Match = Literal["exact", "similar", "new"]
HoldReason = Literal[
    "embedding_failed",  # 임베딩 API 실패. 다음 실행이 자동으로 다시 시도한다 (임베딩 단계)
    "empty_subject",  # subject 가 빈 문자열. 고칠 때까지 계속 보류된다 (임베딩 단계)
    "invalid_observation_embedding",  # 관찰 벡터가 잘못됐다
    "invalid_profile_embedding",  # 후보 Profile 벡터가 잘못됐다. invalid_profile_ids 참고
]


@dataclass(frozen=True)
class LinkOutcome:
    key: ObservationKey
    status: LinkStatus
    affinity_id: str | None = None  # held 면 None
    match: Match | None = None  # held 면 None
    similarity: float | None = None  # match="similar" 일 때만
    reason: HoldReason | None = None  # held 일 때만
    # reason="invalid_profile_embedding" 일 때 그 그룹에서 벡터가 잘못된 Profile 전부.
    # 고치기 전까지 같은 아이 · 도메인 · polarity 의 관찰이 계속 보류되므로 복구 대상을 알려 준다
    invalid_profile_ids: tuple[str, ...] = ()

    @property
    def created(self) -> bool:
        return self.status == "created"


@dataclass(frozen=True)
class LinkStepResult:
    outcomes: tuple[LinkOutcome, ...] = ()


async def link_pending(
    store: CuratorStore, *, child_id: UUID, threshold: float = DEFAULT_THRESHOLD
) -> LinkStepResult:
    check_threshold(threshold)
    outcomes: list[LinkOutcome] = []
    for item in await store.list_unlinked(child_id=child_id):
        outcome = await _link_one(store, item, child_id=child_id, threshold=threshold)
        if outcome.status == "held":
            # 원문(subject)은 남기지 않는다
            logger.warning(
                "curator link held child_id=%s domain=%s observation_id=%s reason=%s "
                "invalid_profile_ids=%s",
                child_id,
                item.domain,
                item.id,
                outcome.reason,
                list(outcome.invalid_profile_ids),
            )
        outcomes.append(outcome)
    return LinkStepResult(tuple(outcomes))


def check_threshold(threshold: float) -> None:
    """NaN 이면 모든 비교가 거짓이라 관찰마다 새 candidate 가 생긴다. 아무것도 하기 전에 막는다."""
    if not math.isfinite(threshold) or not -1.0 <= threshold <= 1.0:
        raise ValueError(f"threshold 는 -1 이상 1 이하의 유한한 값이어야 한다: {threshold!r}")


async def _link_one(
    store: CuratorStore, item: ObservationItem, *, child_id: UUID, threshold: float
) -> LinkOutcome:
    vector = item.embedding
    if not _valid(vector):
        return LinkOutcome(item.key, "held", reason="invalid_observation_embedding")

    # 매번 새로 읽는다 — 바로 앞 관찰이 만든 Profile 도 후보여야 한다
    profiles = await store.list_profiles(
        child_id=child_id, domain=item.domain, polarity=item.polarity
    )
    subject = item.subject.strip()
    name = same_name_key(subject)

    # 저장된 순서라 첫 번째가 가장 오래된 Profile 이다
    same_name = next((p for p in profiles if same_name_key(p.merge_key) == name), None)
    if same_name is not None:
        await store.link(domain=item.domain, observation_id=item.id, affinity_id=same_name.id)
        return LinkOutcome(item.key, "linked", affinity_id=same_name.id, match="exact")

    comparable: list[tuple[ProfileItem, list[float]]] = []
    invalid: list[str] = []
    for profile in profiles:
        candidate = profile.embedding
        if _valid(candidate) and len(candidate) == len(vector):
            comparable.append((profile, candidate))
        else:
            invalid.append(profile.id)
    if invalid:
        # 그 Profile 만 빼고 비교하면, 그게 원래 붙을 곳이었을 때 중복이 생긴다
        return LinkOutcome(
            item.key,
            "held",
            reason="invalid_profile_embedding",
            invalid_profile_ids=tuple(invalid),
        )

    best: ProfileItem | None = None
    best_score = -math.inf
    for profile, candidate in comparable:
        score = _cosine(vector, candidate)
        if score > best_score:  # 같으면 앞의 것(오래된 Profile)을 남긴다
            best, best_score = profile, score

    if best is not None and best_score >= threshold:
        await store.link(domain=item.domain, observation_id=item.id, affinity_id=best.id)
        return LinkOutcome(
            item.key, "linked", affinity_id=best.id, match="similar", similarity=best_score
        )

    created = await store.create_profile(
        child_id=child_id,
        domain=item.domain,
        polarity=item.polarity,
        merge_key=subject,
        embedding=vector,
        last_observed_on=item.observed_on,
    )
    await store.link(domain=item.domain, observation_id=item.id, affinity_id=created.id)
    return LinkOutcome(item.key, "created", affinity_id=created.id, match="new")


def same_name_key(text: str) -> str:
    """이름 비교용 키. 공백을 모두 지운다 — "방울토마토"와 "방울 토마토"는 같은 이름이다.

    띄어쓰기만 다른 쌍은 유사도가 0.78~0.92 로 흩어져 임계값에 따라 놓칠 수 있다
    (임계값 실험, pairs_tune.txt). 규칙으로 잡으면 확실하고 잘못 합침도 늘지 않는다.
    merge_key 에는 공백을 지우지 않은 subject 를 그대로 저장한다 — 사람이 읽는 값이다.
    """
    return "".join(text.split())


def _valid(vector: list[float] | None) -> TypeGuard[list[float]]:
    """비어 있지 않고, 유한한 값만 있고, 길이가 0 이 아니다."""
    if not vector:
        return False
    if not all(math.isfinite(x) for x in vector):
        return False
    return any(x != 0.0 for x in vector)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
