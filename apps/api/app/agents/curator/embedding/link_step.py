"""연결 단계. 새 관찰을 같은 대상의 Profile 에 붙이거나, 없으면 새 candidate 를 만든다.

관찰을 저장된 순서대로 한 건씩 처리한다. 앞에서 만든 Profile 이 다음 관찰의 후보가 된다.
    1. 후보: 같은 아이 · 도메인 · polarity 의 Profile (archived 포함)
    2. 이름이 같은 후보 (공백을 모두 지운 subject == 공백을 모두 지운 merge_key) → 연결
    3. 후보가 없으면 → 새 candidate. 판정기를 부르지 않는다
    4. 판정기(Jev)에 subject 와 후보 merge_key 를 보여 주고 답을 받는다
         후보 하나 → 그 후보가 목록에 있는지 코드가 확인하고 연결
         none      → 5. 반대 방향 확인
         uncertain · 오류 · 목록에 없는 값 → 보류. 다음 실행에서 다시 시도한다
    5. 가까운 후보 REVERSE_TOP 개에 방향을 바꿔 한 번씩 묻는다 ("홍당무와 같은 것이 당근인가?")
         그렇다 → 그 후보에 연결
         모두 아니다 → 새 candidate
         오류 → 보류 (judge_failed). 장애 때문에 Profile 이 나뉘지 않게 새로 만들지 않는다
새 candidate 의 merge_key 는 subject, 벡터는 관찰 벡터를 그대로 쓴다.

반대 방향 확인은 관찰이 들어온 순서 때문에 같은 대상이 둘로 나뉘는 것을 막는다.
"홍당무" Profile 이 있을 때 "당근"이 들어오면 판정기가 none 이라 답한 경우가 있었다
(실험 3, 43번 중 4번). 방향을 바꿔 물으면 없어졌고, 판정기 호출은 약 2배가 됐다 (실험 5).

판정기가 묻는 것은 "같은 대상인가" 하나다. 연결 판단에 벡터 유사도를 쓰지 않는다 —
임계값 실험(실험 1 · 2)에서 유사도는 글자 겹침을 따라가 계란–달걀(0.20)과
장난감 기차–비행기(0.76)를 가르지 못했다. 벡터가 연결 단계에 나오는 곳은 셋뿐이다.
    - 후보가 MAX_CANDIDATES 를 넘을 때 판정기에 보여 줄 후보를 추린다
    - 후보가 REVERSE_TOP 을 넘을 때 반대 방향으로 물을 후보를 고른다
    - 새 candidate 에 관찰 벡터를 복사한다 (Agent 검색 · 추리기에 쓰인다)
관찰 벡터의 본래 쓰임은 도메인 Agent 의 검색(memory.search)이다.

바꾸는 것은 관찰의 affinity_id 와 새 Profile 뿐이다.
Profile 의 state · strength · last_observed_on 은 건드리지 않는다 — 다시 승격할지는
승격 규칙이 정한다. 기존 Profile 에 연결할 때 last_observed_on 을 누가 갱신할지는
PR 에서 제안한다 (연결된 active 관찰 중 가장 늦은 observed_on 으로 다시 계산하는 안).
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeGuard
from uuid import UUID

from app.agents.common.llm_client import LLMError
from app.agents.curator.embedding.judge import (
    NONE,
    RESERVED,
    STOP_ERRORS,
    UNCERTAIN,
    IdentityJudge,
    JudgeAnswer,
)
from app.agents.curator.embedding.ports import (
    CuratorDomain,
    CuratorStore,
    ObservationItem,
    ObservationKey,
    ProfileItem,
)

logger = logging.getLogger(__name__)

# 판정기에 한 번에 보여 줄 후보 수. 넘으면 벡터 유사도로 가까운 순서대로 추린다.
# 임시값 — 실제에 가까운 후보 수로 다시 정한다 (실험 3 · 4 의 후보는 2~3개였다)
MAX_CANDIDATES = 30

# 판정기가 none 이라 답했을 때 방향을 바꿔 다시 물을 후보 수. 넘으면 벡터 유사도로 고른다.
# 실험 5 와 같은 값이다. 유사도가 글자 겹침을 따라가서 같은 대상이 여기에 못 들 수 있다
REVERSE_TOP = 3

LinkStatus = Literal["linked", "created", "held"]
Match = Literal["exact", "judged", "reversed", "new"]
HoldReason = Literal[
    "embedding_failed",  # 임베딩 API 실패. 다음 실행이 자동으로 다시 시도한다 (임베딩 단계)
    "empty_subject",  # subject 가 빈 문자열. 고칠 때까지 계속 보류된다 (임베딩 단계)
    "invalid_observation_embedding",  # 관찰 벡터가 잘못됐다
    "invalid_profile_embedding",  # 추리거나 고를 Profile 벡터가 잘못됐다. invalid_profile_ids
    "invalid_candidate_name",  # 후보 merge_key 가 비었거나 none · uncertain. invalid_profile_ids
    "judge_unavailable",  # 판정기가 없다 (CURATOR_JUDGE_* 미설정). 설정하면 다음 실행에서 풀린다
    "judge_uncertain",  # 판정기가 판단할 수 없다고 답했다
    "judge_failed",  # 판정기 호출 실패 (LLMError, error 참고). 다음 실행이 다시 시도한다
    "judge_invalid",  # 판정기가 후보 목록에 없는 값을 답했다
]


@dataclass(frozen=True)
class LinkOutcome:
    key: ObservationKey
    status: LinkStatus
    affinity_id: str | None = None  # held 면 None
    match: Match | None = None  # held 면 None
    reason: HoldReason | None = None  # held 일 때만
    # 판정기를 불렀을 때만. 연결 · 생성 · 보류(uncertain · invalid) 모두 남긴다
    judge_model: str | None = None
    confidence: float | None = None  # 기록만 한다
    error: str | None = None  # reason="judge_failed" 일 때 오류 종류 이름. 메시지는 싣지 않는다
    # reason="invalid_profile_embedding" · "invalid_candidate_name" 일 때 문제가 된 Profile 전부.
    # 고치기 전까지 같은 그룹의 관찰이 계속 보류되므로 복구 대상을 알려 준다
    invalid_profile_ids: tuple[str, ...] = ()

    @property
    def created(self) -> bool:
        return self.status == "created"


@dataclass(frozen=True)
class LinkStepResult:
    outcomes: tuple[LinkOutcome, ...] = ()


@dataclass
class _Judging:
    """한 실행 안의 판정기 상태. 계정 오류(STOP_ERRORS)가 나면 남은 관찰에 부르지 않는다."""

    judge: IdentityJudge | None
    stopped_by: str | None = field(default=None)  # 멈추게 한 오류 종류 이름


async def link_pending(
    store: CuratorStore, judge: IdentityJudge | None, *, child_id: UUID
) -> LinkStepResult:
    """judge 가 None 이면 이름 비교 · 후보 없음만 처리한다. 나머지는 judge_unavailable 로 보류."""
    judging = _Judging(judge)
    outcomes: list[LinkOutcome] = []
    for item in await store.list_unlinked(child_id=child_id):
        outcome = await _link_one(store, judging, item, child_id=child_id)
        if outcome.status == "held":
            # 원문(subject)은 남기지 않는다
            logger.warning(
                "curator link held child_id=%s domain=%s observation_id=%s reason=%s "
                "error=%s invalid_profile_ids=%s",
                child_id,
                item.domain,
                item.id,
                outcome.reason,
                outcome.error,
                list(outcome.invalid_profile_ids),
            )
        outcomes.append(outcome)
    return LinkStepResult(tuple(outcomes))


async def _link_one(
    store: CuratorStore, judging: _Judging, item: ObservationItem, *, child_id: UUID
) -> LinkOutcome:
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

    # 여기부터는 벡터가 필요할 수 있다 — 새 Profile 에 복사하거나 후보를 추린다
    vector = item.embedding
    if not _valid(vector):
        return LinkOutcome(item.key, "held", reason="invalid_observation_embedding")

    if not profiles:
        return await _create(store, item, vector, child_id=child_id)
    judge = judging.judge
    if judge is None:
        return LinkOutcome(item.key, "held", reason="judge_unavailable")
    if judging.stopped_by is not None:
        return LinkOutcome(item.key, "held", reason="judge_failed", error=judging.stopped_by)

    shown = profiles
    if len(profiles) > MAX_CANDIDATES:
        comparable, invalid = _comparable(profiles, vector)
        if invalid:
            # 그 Profile 만 빼고 추리면, 그게 원래 붙을 곳이었을 때 중복이 생긴다
            return _invalid_profile_embedding(item, invalid)
        shown = _nearest(comparable, vector, MAX_CANDIDATES)

    # 같은 merge_key 가 둘이면 목록에는 한 번만. 연결은 전체 후보 중 오래된 Profile 로 —
    # 추린 목록(shown)은 유사도 순이라 거기서 고르면 더 가까운 쪽이 남는다
    oldest: dict[str, ProfileItem] = {}
    for profile in profiles:
        oldest.setdefault(profile.merge_key.strip(), profile)
    keys = list(dict.fromkeys(profile.merge_key.strip() for profile in shown))

    # 빈 이름 · none · uncertain 은 판정기 답과 구분할 수 없다. 추측하지 않고 보류한다
    bad = [p.id for p in shown if not p.merge_key.strip() or p.merge_key.strip() in RESERVED]
    if bad:
        return LinkOutcome(
            item.key, "held", reason="invalid_candidate_name", invalid_profile_ids=tuple(bad)
        )

    try:
        answer = await _ask(judging, judge, subject=subject, domain=item.domain, candidates=keys)
    except _JudgeFailed as failed:
        return LinkOutcome(item.key, "held", reason="judge_failed", error=failed.error)

    model, confidence = answer.model, answer.confidence
    if answer.choice == NONE:
        targets, invalid = _reverse_targets(shown, vector)
        if invalid:
            return _invalid_profile_embedding(item, invalid)
        try:
            found = await _ask_reverse(
                judging, judge, subject=subject, domain=item.domain, targets=targets
            )
        except _JudgeFailed as failed:
            # 새로 만들지 않는다 — 장애 때문에 같은 대상이 둘로 나뉘면 합칠 방법이 없다
            return LinkOutcome(item.key, "held", reason="judge_failed", error=failed.error)
        if found is None:
            return await _create(
                store, item, vector, child_id=child_id, judge_model=model, confidence=confidence
            )
        key, back = found
        chosen = oldest[key]
        await store.link(domain=item.domain, observation_id=item.id, affinity_id=chosen.id)
        return LinkOutcome(
            item.key,
            "linked",
            affinity_id=chosen.id,
            match="reversed",
            judge_model=back.model,
            confidence=back.confidence,
        )
    if answer.choice == UNCERTAIN:
        return LinkOutcome(
            item.key, "held", reason="judge_uncertain", judge_model=model, confidence=confidence
        )
    chosen = _pick(answer.choice, keys, oldest)
    if chosen is None:
        # 판정기 답을 믿고 추측하지 않는다 — 목록에 있는 값만 연결한다 (§3 확인은 코드가)
        return LinkOutcome(
            item.key, "held", reason="judge_invalid", judge_model=model, confidence=confidence
        )

    await store.link(domain=item.domain, observation_id=item.id, affinity_id=chosen.id)
    return LinkOutcome(
        item.key,
        "linked",
        affinity_id=chosen.id,
        match="judged",
        judge_model=model,
        confidence=confidence,
    )


async def _create(
    store: CuratorStore,
    item: ObservationItem,
    vector: list[float],
    *,
    child_id: UUID,
    judge_model: str | None = None,
    confidence: float | None = None,
) -> LinkOutcome:
    created = await store.create_profile(
        child_id=child_id,
        domain=item.domain,
        polarity=item.polarity,
        merge_key=item.subject.strip(),
        embedding=vector,
        last_observed_on=item.observed_on,
    )
    await store.link(domain=item.domain, observation_id=item.id, affinity_id=created.id)
    return LinkOutcome(
        item.key,
        "created",
        affinity_id=created.id,
        match="new",
        judge_model=judge_model,
        confidence=confidence,
    )


class _JudgeFailed(Exception):
    """판정기 호출 실패. error 는 오류 종류 이름이다 (메시지는 싣지 않는다)."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


async def _ask(
    judging: _Judging,
    judge: IdentityJudge,
    *,
    subject: str,
    domain: CuratorDomain,
    candidates: Sequence[str],
) -> JudgeAnswer:
    try:
        return await judge.judge(subject=subject, domain=domain, candidates=candidates)
    except LLMError as exc:
        error = type(exc).__name__
        if isinstance(exc, STOP_ERRORS):
            # 계정 문제다. 남은 관찰에 불러도 모두 같은 오류라 이번 실행에서는 더 부르지 않는다
            judging.stopped_by = error
            logger.error("curator judge stopped for this run error=%s", error)
        raise _JudgeFailed(error) from exc


def _reverse_targets(
    shown: list[ProfileItem], vector: list[float]
) -> tuple[list[str], tuple[str, ...]]:
    """반대 방향으로 물을 후보 merge_key 와, 벡터가 잘못돼 고르지 못하게 한 Profile id.

    후보가 REVERSE_TOP 개 이하면 모두 묻는다 — 벡터를 보지 않는다.
    넘으면 가까운 순서로 고른다. 벡터가 잘못된 Profile 이 있으면 고르지 않는다
    (그 Profile 만 빼면, 그게 원래 붙을 곳이었을 때 중복이 생긴다).
    """
    by_key: dict[str, ProfileItem] = {}
    for profile in shown:
        by_key.setdefault(profile.merge_key.strip(), profile)
    if len(by_key) <= REVERSE_TOP:
        return list(by_key), ()
    comparable, invalid = _comparable(list(by_key.values()), vector)
    if invalid:
        return [], invalid
    nearest = _nearest(comparable, vector, REVERSE_TOP)
    return [profile.merge_key.strip() for profile in nearest], ()


async def _ask_reverse(
    judging: _Judging,
    judge: IdentityJudge,
    *,
    subject: str,
    domain: CuratorDomain,
    targets: list[str],
) -> tuple[str, JudgeAnswer] | None:
    """후보를 subject 로, 원래 subject 를 유일한 후보로 넣어 묻는다. 그렇다고 답한 첫 후보."""
    if not subject or subject in RESERVED:
        # subject 가 후보 자리에 들어간다. 판정기 답과 구분할 수 없으니 묻지 않는다
        return None
    for key in targets:
        back = await _ask(judging, judge, subject=key, domain=domain, candidates=[subject])
        if same_name_key(back.choice) == same_name_key(subject):
            return key, back
    return None


def _comparable(
    profiles: list[ProfileItem], vector: list[float]
) -> tuple[list[tuple[ProfileItem, list[float]]], tuple[str, ...]]:
    """벡터로 비교할 수 있는 Profile 과, 벡터가 없거나 잘못됐거나 차원이 다른 Profile id."""
    comparable: list[tuple[ProfileItem, list[float]]] = []
    invalid: list[str] = []
    for profile in profiles:
        candidate = profile.embedding
        if _valid(candidate) and len(candidate) == len(vector):
            comparable.append((profile, candidate))
        else:
            invalid.append(profile.id)
    return comparable, tuple(invalid)


def _invalid_profile_embedding(item: ObservationItem, invalid: tuple[str, ...]) -> LinkOutcome:
    return LinkOutcome(
        item.key, "held", reason="invalid_profile_embedding", invalid_profile_ids=invalid
    )


def _pick(choice: str, keys: list[str], oldest: dict[str, ProfileItem]) -> ProfileItem | None:
    """보여 준 후보 중 판정기가 고른 것. 공백만 다르면, 그런 후보가 딱 하나일 때만 인정한다."""
    if choice in keys:
        return oldest[choice]
    loose = [key for key in keys if same_name_key(key) == same_name_key(choice)]
    return oldest[loose[0]] if len(loose) == 1 else None


def _nearest(
    comparable: list[tuple[ProfileItem, list[float]]], vector: list[float], limit: int
) -> list[ProfileItem]:
    """유사도가 높은 순서로 limit 개. 같으면 오래된 Profile 이 앞이다 (정렬이 안정적)."""
    scored = [(profile, _cosine(vector, candidate)) for profile, candidate in comparable]
    scored.sort(key=lambda pair: -pair[1])
    return [profile for profile, _ in scored[:limit]]


def same_name_key(text: str) -> str:
    """이름 비교용 키. 공백을 모두 지운다 — "방울토마토"와 "방울 토마토"는 같은 이름이다.

    띄어쓰기만 다른 쌍은 유사도가 0.78~0.92 로 흩어져 임계값에 따라 놓칠 수 있다
    (임계값 실험, tune_pairs.txt). 규칙으로 잡으면 확실하고 잘못 합침도 늘지 않는다.
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
