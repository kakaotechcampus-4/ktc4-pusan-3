"""교육 · 루틴 후보 출력 검증 (Tool_공통 §5-1 순서).

  1. 안전 필터   교육: 음식 용어 · 등록 이름 · 위험 용어 / 루틴: 음식 용어 · 의료 처치 · 증상.
                 걸린 후보는 거절이 아니라 뺀다(`removed`) — 모델에게 사유를 돌려주지 않는다
  2. 금지 표현   걸리면 뺀다. 재호출 제외 목록에도 넣는다 — Tool_공통 §5-2 는 안전 필터만 적었고
                 Growth 가 넓혔다(같은 후보가 다시 나오지 않게)
  3. Growth 검증 근거 id 대조 · 루틴은 코드가 고른 단계(step_id)만
  4. build()     아이 기록 근거 수로 kind 를 정한다. 루틴은 일반 추천을 내지 않는다

🚨 #402(추천별 경고 자리) 전까지 교육 후보는 경고가 붙어도 뺀다. 화면에 경고 자리가 없으면
"경고와 함께" 가 실제로는 경고 없이 나가는 것이다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import UUID

from app.agents.common.evidence import RankedEvidence, cite
from app.agents.common.refs import EvidenceCitation, Ref
from app.agents.common.suggestion import SuggestionDraft, SuggestionRejected, build
from app.agents.growth.result import ErrorCode
from app.agents.growth.schemas.common import EvidencePick
from app.agents.growth.schemas.learning import LearningItem
from app.agents.growth.schemas.routine import RoutineItem
from app.agents.growth.store.ports import GrowthDocRow, RoutineObservation, SafetyEntry
from app.agents.growth.tools.closures import closure_key
from app.agents.growth.tools.routine import routine_evidence
from app.agents.growth.tools.safety import (
    SafetyRules,
    check_learning_candidate,
    check_routine_candidate,
)
from app.rules.evaluative import find_evaluative

AGENT = "growth"
# 아이 기록 근거가 0행이면 build() 가 reason 을 이 문구로 덮어쓴다 (kind="general", 루트 §2)
GENERAL_REASON = "쌓인 배움 기록이 아직 적어서, 이 나이 아이들에게 많이 권하는 활동으로 골랐어요."
# #402 전까지 경고가 붙는 교육 후보도 뺀다. 경고 자리가 생기면 False 로 두고 경고를 싣는다
DROP_WARNED = True

Seen = Mapping[UUID, RankedEvidence]
Docs = Mapping[UUID, GrowthDocRow]


class RejectReason(StrEnum):
    UNKNOWN_EVIDENCE = "unknown_evidence"
    UNKNOWN_STEP = "unknown_step"
    DUPLICATE_STEP = "duplicate_step"
    OVER_CAPACITY = "over_capacity"
    BUILD = "build_rejected"


GUIDANCE: dict[RejectReason, str] = {
    RejectReason.UNKNOWN_EVIDENCE: "evidence 에는 조회 결과나 [예시]에 있던 id 만 쓴다.",
    RejectReason.UNKNOWN_STEP: "step_id 는 search_routine_memory 의 next_steps 에 있던 것만 쓴다.",
    RejectReason.DUPLICATE_STEP: "같은 단계를 두 번 냈다. 단계마다 하나씩만 낸다.",
    RejectReason.OVER_CAPACITY: "next_steps 에 있는 단계 수보다 많이 냈다.",
    RejectReason.BUILD: (
        "아이 기록을 인용하지 않으면 [예시] 문서 행 id 를 하나 이상 인용한다. "
        "생활 루틴은 그 루틴 기록을 인용한다. 싫어하는 것을 근거로 썼으면 "
        "무엇을 피했는지 reason 에 쓴다."
    ),
}


@dataclass(frozen=True)
class Rejection:
    index: int  # 후보 순서 (0부터)
    reason: RejectReason


@dataclass(frozen=True)
class Review:
    drafts: tuple[SuggestionDraft, ...]
    rejections: tuple[Rejection, ...]
    removed: tuple[int, ...] = ()  # 안전 필터 · 금지 표현으로 뺀 후보 순서. 로그 · 제외 목록용


def learning_content(item: LearningItem) -> str:
    """카드 본문 — 제목 한 줄 + 번호 붙인 단계. `SuggestionDraft` 에 칸을 더하지 않는다."""
    steps = "\n".join(f"{n}. {step.strip()}" for n, step in enumerate(item.steps, start=1))
    return f"{item.title.strip()}\n{steps}"


def review_learning(
    items: Sequence[LearningItem],
    *,
    months: int,
    entries: Sequence[SafetyEntry],
    rules: SafetyRules,
    seen: Seen,
    docs: Docs,
    today: date,
) -> Review:
    drafts: list[SuggestionDraft] = []
    rejections: list[Rejection] = []
    removed: list[int] = []
    for index, item in enumerate(items):
        texts = (item.title, *item.steps, *item.materials)
        verdict = check_learning_candidate(texts, months=months, entries=entries, rules=rules)
        if not verdict.allowed or (DROP_WARNED and verdict.cautions):
            removed.append(index)
            continue
        if _evaluative((item.title, *item.steps, item.reason), item.evidence):
            removed.append(index)
            continue
        citations = _resolve(item.evidence, seen, docs)
        if citations is None:
            rejections.append(Rejection(index, RejectReason.UNKNOWN_EVIDENCE))
            continue
        try:
            drafts.append(
                build(
                    agent=AGENT,
                    content=learning_content(item),
                    reason=item.reason.strip(),
                    citations=citations,
                    general_reason=GENERAL_REASON,
                    items=tuple(m.strip() for m in item.materials if m.strip()),
                    today=today,
                )
            )
        except SuggestionRejected:
            rejections.append(Rejection(index, RejectReason.BUILD))
    return Review(tuple(drafts), tuple(rejections), tuple(removed))


def review_routine(
    items: Sequence[RoutineItem],
    *,
    rows: Mapping[UUID, GrowthDocRow],
    capacity: int,
    category: str,
    observations: Mapping[UUID, RoutineObservation],
    rules: SafetyRules,
    seen: Seen,
    today: date,
) -> Review:
    """`rows` 는 코드가 고른 다음 단계 행(step_id → 행)이다. 이 밖의 단계는 거절한다.

    루틴은 일반 추천을 내지 않는다(`general_reason=None`). 근거로 세는 것은 **선언한 카테고리와 같고
    최근 14일 안인 `observation_routine`** 이다 — 관심(affinity)만 인용하면 아이가 지금 어디까지
    하는지 모르는 채 단계를 내게 된다(Growth_Tool_명세 §2 "루틴 근거"). 고른 단계 행은 코드가
    `growth_doc` 근거로 덧붙인다.
    """
    drafts: list[SuggestionDraft] = []
    rejections: list[Rejection] = []
    removed: list[int] = []
    used: set[UUID] = set()
    for index, item in enumerate(items):
        if index >= capacity:
            rejections.append(Rejection(index, RejectReason.OVER_CAPACITY))
            continue
        step_id = _uuid(item.step_id)
        row = rows.get(step_id) if step_id else None
        if row is None or step_id is None:
            rejections.append(Rejection(index, RejectReason.UNKNOWN_STEP))
            continue
        if step_id in used:
            rejections.append(Rejection(index, RejectReason.DUPLICATE_STEP))
            continue
        texts = (item.next_step, item.how)
        if not check_routine_candidate(texts, rules=rules).allowed or closure_key(texts):
            removed.append(index)
            continue
        if _evaluative((*texts, item.reason), item.evidence):
            removed.append(index)
            continue
        citations = _resolve(item.evidence, seen, {})
        if citations is None:
            rejections.append(Rejection(index, RejectReason.UNKNOWN_EVIDENCE))
            continue
        cited = [observations[c.ref.id] for c in citations if c.ref.id in observations]
        if not routine_evidence(cited, category=category, today=today):
            rejections.append(Rejection(index, RejectReason.BUILD))
            continue
        step_ref = EvidenceCitation(
            ref=Ref(kind="growth_doc", id=row.id), note=row.title, label=row.title
        )
        try:
            drafts.append(
                build(
                    agent=AGENT,
                    content=f"{item.next_step.strip()}\n{item.how.strip()}",
                    reason=item.reason.strip(),
                    citations=(*citations, step_ref),
                    general_reason=None,
                    items=row.materials,
                    today=today,
                )
            )
        except SuggestionRejected:
            rejections.append(Rejection(index, RejectReason.BUILD))
            continue
        used.add(step_id)
    return Review(tuple(drafts), tuple(rejections), tuple(removed))


def step_of(draft: SuggestionDraft) -> UUID | None:
    """루틴 추천이 옮긴 단계 행 id. 재호출 결과를 합칠 때 "같은 추천" 판정에 쓴다."""
    return next((c.ref.id for c in draft.citations if c.ref.kind == "growth_doc"), None)


def error_code(rejections: Sequence[Rejection]) -> str:
    if any(r.reason is RejectReason.UNKNOWN_EVIDENCE for r in rejections):
        return ErrorCode.EVIDENCE_REQUIRED
    return ErrorCode.CANDIDATE_REJECTED


def explain(rejections: Sequence[Rejection]) -> str:
    """모델이 읽는 거절 설명. 후보 번호(1부터)와 고칠 방향만 싣는다."""
    return " / ".join(f"{r.index + 1}번 후보: {GUIDANCE[r.reason]}" for r in rejections)


def _evaluative(texts: Sequence[str], evidence: Sequence[EvidencePick]) -> bool:
    return any(find_evaluative(text) for text in (*texts, *(pick.note for pick in evidence)))


def _uuid(raw: str) -> UUID | None:
    try:
        return UUID(raw)
    except ValueError:
        return None


def _resolve(
    picks: Sequence[EvidencePick], seen: Seen, docs: Docs
) -> tuple[EvidenceCitation, ...] | None:
    """조회 결과 · [예시]에 있던 id 만 인용으로 옮긴다. 하나라도 밖이면 None — 후보를 거절한다."""
    citations: list[EvidenceCitation] = []
    for pick in picks:
        key = _uuid(pick.id)
        if key is not None and key in seen:
            citations.append(cite(seen[key], note=pick.note))
        elif key is not None and key in docs:
            row = docs[key]
            citations.append(
                EvidenceCitation(
                    ref=Ref(kind="growth_doc", id=row.id), note=pick.note, label=row.title
                )
            )
        else:
            return None
    return tuple(citations)
