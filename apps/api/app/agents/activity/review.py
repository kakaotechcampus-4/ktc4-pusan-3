"""놀이 후보 출력 검증 — 설계 3-3 순서.

  1. 안전 필터       위험 용어 사전 PR 에서 여기 붙는다. 걸린 후보는 거절이 아니라 풀에서 뺀다
  2. 평가 표현       content · why_this · why_now · note
  3. Activity 검증   근거 id · 기피 대상 · note 반복 표현 · 최근 중복 · 0–17개월 보호자 동반
  4. build()         아이 기록 근거 행 수로 kind 를 정한다. 모델이 고르지 않는다
  5. check_count()   정확히 3개 — 호출부(출력 tool)가 부른다

순서가 결과를 바꾼다. 안전에 걸린 후보가 뒤 검사를 타면 엉뚱한 사유가 나간다.
걸린 후보는 고치지 않고 통째로 거절한다. "물놀이터" 를 "얕은 물놀이터" 로 고쳐 통과시키지 않는다.
사유는 코드로만 남긴다 — 활동명 · 근거 문장을 싣지 않는다 (로그로 흘러간다).
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.agents.activity.rules import is_recent_duplicate, normalize_activity, overstates
from app.agents.activity.schemas.common import CaregiverRole, EvidencePick
from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.common.evidence import RankedEvidence, cite
from app.agents.common.refs import EvidenceCitation
from app.agents.common.suggestion import SuggestionDraft, SuggestionRejected, build
from app.rules.evaluative import find_evaluative

# 이 월령 미만은 보호자가 같이 하는 활동만 낸다 (3-3). 밴드 경계와 따로 둔다 —
# 로그 편의로 밴드를 옮길 때 안전 경계가 같이 끌려가지 않게.
TOGETHER_ONLY_BELOW_MONTH = 18

# 기피 라벨이 이 길이보다 짧으면 기피 대상 거절에 쓰지 않는다. "공" 을 싫어한다고
# "공원" · "공룡" 이 들어간 후보까지 거절하면 과차단이다. 기피는 근거로는 계속 남는다.
MIN_AVOIDED_LABEL = 2

# 아이 기록 근거가 0행이면 build() 가 reason 을 이 문구로 덮어쓴다 (kind="general").
# 화면이 일반 추천임을 표시하고 쌓인 기록 건수를 함께 보여준다 (루트 §2).
GENERAL_REASON = "쌓인 놀이 기록이 아직 적어서, 이 나이 아이들에게 많이 권하는 놀이로 골랐어요."

Seen = Mapping[UUID, RankedEvidence]


class RejectReason(StrEnum):
    EVALUATIVE = "evaluative_expression"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    AVOIDED = "avoided_activity"
    OVERSTATED_NOTE = "overstated_note"
    RECENT_DUPLICATE = "recent_duplicate"
    CAREGIVER_ROLE = "caregiver_role"
    BUILD = "build_rejected"


# 모델에게 돌려줄 설명 — 무엇을 고치면 되는지만. 걸린 낱말은 알려 주지 않는다
GUIDANCE: dict[RejectReason, str] = {
    RejectReason.EVALUATIVE: "또래 비교나 발달 판정 표현을 쓰지 않는다.",
    RejectReason.UNKNOWN_EVIDENCE: "evidence 에는 search_activity_memory 결과에 있던 id 만 쓴다.",
    RejectReason.AVOIDED: "싫어한다고 나온 활동은 다시 내지 않는다. 다른 활동으로 바꾼다.",
    RejectReason.OVERSTATED_NOTE: (
        "note 의 '어제도 · 또 · 늘 · 항상 · 좋아하는' 은 확정된 관심(tier 1)에만 쓴다."
    ),
    RejectReason.RECENT_DUPLICATE: "최근에 한 활동이다. 다른 활동으로 바꾼다.",
    RejectReason.CAREGIVER_ROLE: "이 월령은 보호자가 같이 하는 활동(together)만 낸다.",
    RejectReason.BUILD: "싫어하는 것을 근거로 썼으면 무엇을 피했는지 why_this 에 쓴다.",
}


@dataclass(frozen=True)
class Rejection:
    index: int  # 후보 순서 (0부터)
    reason: RejectReason


@dataclass(frozen=True)
class Review:
    drafts: tuple[SuggestionDraft, ...]
    rejections: tuple[Rejection, ...]


def review_candidates(
    candidates: Sequence[ActivityCandidate],
    *,
    months: int,
    seen: Seen,
    recent_activities: Collection[str],
) -> Review:
    """후보를 3-3 순서로 검사해 통과한 것은 SuggestionDraft 로, 걸린 것은 사유로 돌려준다.

    `seen` 은 이번 run 에서 search_activity_memory 가 돌려준 근거다. 기피 대상 거절도 이 표를
    쓴다 — 모델이 인용하지 않은 기피 근거여도 조회됐으면 막는다.
    """
    avoided = tuple(
        label
        for ranked in seen.values()
        if ranked.is_avoidance
        and len(label := normalize_activity(ranked.label)) >= MIN_AVOIDED_LABEL
    )
    drafts: list[SuggestionDraft] = []
    rejections: list[Rejection] = []
    for index, candidate in enumerate(candidates):
        reason, citations = _check(
            candidate, months=months, seen=seen, avoided=avoided, recent=recent_activities
        )
        if reason is None:
            try:
                drafts.append(_build(candidate, citations))
            except SuggestionRejected:
                reason = RejectReason.BUILD
        if reason is not None:
            rejections.append(Rejection(index=index, reason=reason))
    return Review(drafts=tuple(drafts), rejections=tuple(rejections))


def explain(rejections: Sequence[Rejection]) -> str:
    """모델이 읽는 거절 설명. 후보 번호(1부터)와 고칠 방향만 싣는다."""
    return " / ".join(f"{r.index + 1}번 후보: {GUIDANCE[r.reason]}" for r in rejections)


def _check(
    candidate: ActivityCandidate,
    *,
    months: int,
    seen: Seen,
    avoided: tuple[str, ...],
    recent: Collection[str],
) -> tuple[RejectReason | None, tuple[EvidenceCitation, ...]]:
    # 1. 안전 필터 — 위험 용어 사전 PR 에서 여기에 붙는다

    # 2. 평가 표현
    texts = (candidate.content, candidate.why_this, candidate.why_now)
    notes = tuple(pick.note for pick in candidate.evidence)
    if any(find_evaluative(text) for text in (*texts, *notes)):
        return RejectReason.EVALUATIVE, ()

    # 3. Activity 검증
    resolved: list[tuple[EvidencePick, RankedEvidence]] = []
    for pick in candidate.evidence:
        ranked = _lookup(seen, pick.id)
        if ranked is None:
            return RejectReason.UNKNOWN_EVIDENCE, ()
        resolved.append((pick, ranked))

    content_key = normalize_activity(candidate.content)
    if any(label in content_key for label in avoided):
        return RejectReason.AVOIDED, ()
    if any(overstates(pick.note) and ranked.tier != 1 for pick, ranked in resolved):
        return RejectReason.OVERSTATED_NOTE, ()
    if is_recent_duplicate(candidate.content, recent):
        return RejectReason.RECENT_DUPLICATE, ()
    if months < TOGETHER_ONLY_BELOW_MONTH and candidate.caregiver_role != CaregiverRole.TOGETHER:
        return RejectReason.CAREGIVER_ROLE, ()

    citations = tuple(cite(ranked, note=pick.note) for pick, ranked in resolved)
    return None, citations


def _build(
    candidate: ActivityCandidate, citations: tuple[EvidenceCitation, ...]
) -> SuggestionDraft:
    reason = f"{candidate.why_this} {candidate.why_now}".strip()
    return build(
        agent="activity",
        content=candidate.content,
        reason=reason,
        citations=citations,
        general_reason=GENERAL_REASON,
    )


def _lookup(seen: Seen, raw_id: str) -> RankedEvidence | None:
    try:
        key = UUID(raw_id)
    except ValueError:
        return None
    return seen.get(key)
