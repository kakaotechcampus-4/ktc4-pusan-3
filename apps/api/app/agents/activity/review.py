"""놀이 후보 출력 검증 — 설계 3-3 순서.

  1. 안전 필터       위험 용어(월령별 차단 · 경고) · health_safety(active). 걸린 후보는 거절이
                     아니라 풀에서 뺀다(`removed`) — 모델에게 사유를 돌려주지 않는다 (§5-2)
  2. 평가 표현       content · reason · note
  3. Activity 검증   근거 id · 장소 이름 · 바깥 활동이 어려운 날의 야외 후보 · 기피 대상 ·
                     note 반복 표현 · 최근 중복 · 0–17개월 보호자 동반
  4. build()         6개월이 안 지난 아이 기록 근거 수로 kind 를 정한다. 모델이 고르지 않는다.
                     장소 이름은 코드가 content 앞에 붙이고, 확인 문구(place_notice)를 칸으로
                     싣는다. allergens · items 도 코드가 채운다
  5. check_count()   최대 3개 — 호출부(출력 tool)가 부른다

순서가 결과를 바꾼다. 안전에 걸린 후보가 뒤 검사를 타면 엉뚱한 사유가 나간다.
걸린 후보는 고치지 않고 통째로 빼거나 거절한다. "물놀이터" 를 "얕은 물놀이터" 로 고쳐 통과시키지
않는다.
사유는 코드로만 남긴다 — 활동명 · 근거 문장을 싣지 않는다 (로그로 흘러간다).
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from uuid import UUID

from app.agents.activity.readouts import PLACE_NOTICE, PLACE_NOTICE_UNDATED, READOUTS
from app.agents.activity.result import ErrorCode
from app.agents.activity.rules import is_recent_duplicate, normalize_activity, overstates
from app.agents.activity.schemas.common import (
    ActivitySetting,
    CaregiverRole,
    DayLabel,
    EvidencePick,
)
from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.activity.store.ports import ActivityDocRow, PlaceRow
from app.agents.activity.tools.filters import SafetyTerms, check_candidate
from app.agents.common.evidence import RankedEvidence, cite
from app.agents.common.reference import age_gates
from app.agents.common.refs import EvidenceCitation, Ref
from app.agents.common.suggestion import SuggestionDraft, SuggestionRejected, build
from app.rules.evaluative import find_evaluative

# 이 월령 미만은 보호자가 같이 하는 활동만 낸다 (3-3). 밴드 경계와 따로 둔다 —
# 로그 편의로 밴드를 옮길 때 안전 경계가 같이 끌려가지 않게. 값은 `reference/age_gates.yaml` (#372).
TOGETHER_ONLY_BELOW_MONTH = age_gates().together_only_below_month

# 기피 대상(`subject`)이 이 길이보다 짧으면 기피 대상 거절에 쓰지 않는다. "공" 을 싫어한다고
# "공원" · "공룡" 이 들어간 후보까지 거절하면 과차단이다. 기피는 근거로는 계속 남는다.
MIN_AVOIDED_LABEL = 2

# 아이 기록 근거가 0행이면 build() 가 reason 을 이 문구로 덮어쓴다 (kind="general").
# 화면이 일반 추천임을 표시하고 쌓인 기록 건수를 함께 보여준다 (루트 §2).
GENERAL_REASON = "쌓인 놀이 기록이 아직 적어서, 이 나이 아이들에게 많이 권하는 놀이로 골랐어요."

Seen = Mapping[UUID, RankedEvidence]
# 이번 run 의 프롬프트 [예시]에 실은 문서 행. 일반 추천은 이 행을 근거로 단다 (루트 §2)
Docs = Mapping[UUID, ActivityDocRow]


class RejectReason(StrEnum):
    EVALUATIVE = "evaluative_expression"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    AVOIDED = "avoided_activity"
    OVERSTATED_NOTE = "overstated_note"
    RECENT_DUPLICATE = "recent_duplicate"
    CAREGIVER_ROLE = "caregiver_role"
    UNKNOWN_PLACE = "unknown_place"
    OUTDOOR_CLOSED = "outdoor_closed"
    BUILD = "build_rejected"


# 모델에게 돌려줄 설명 — 무엇을 고치면 되는지만. 걸린 낱말은 알려 주지 않는다
GUIDANCE: dict[RejectReason, str] = {
    RejectReason.EVALUATIVE: "또래 비교나 발달 판정 표현을 쓰지 않는다.",
    RejectReason.UNKNOWN_EVIDENCE: (
        "evidence 에는 search_activity_memory 결과나 [예시]에 있던 id 만 쓴다."
    ),
    RejectReason.AVOIDED: "싫어한다고 나온 활동은 다시 내지 않는다. 다른 활동으로 바꾼다.",
    RejectReason.OVERSTATED_NOTE: (
        "note 의 '어제도 · 또 · 늘 · 항상 · 좋아하는' 은 확정된 관심(tier 1)에만 쓴다."
    ),
    RejectReason.RECENT_DUPLICATE: "최근에 한 활동이다. 다른 활동으로 바꾼다.",
    RejectReason.CAREGIVER_ROLE: "이 월령은 보호자가 같이 하는 활동(together)만 낸다.",
    RejectReason.UNKNOWN_PLACE: (
        "place_name 에는 search_nearby_places 결과에 있던 이름만 쓴다. 없으면 비운다."
    ),
    RejectReason.OUTDOOR_CLOSED: (
        "그날(day)은 바깥 활동이 어려운 날이다. setting 이 indoor 나 either 인 활동으로 바꾸거나,"
        " lookup_weather 로 바깥 활동이 되는 날을 찾아 day 를 고친다."
    ),
    RejectReason.BUILD: (
        "아이 기록을 인용하지 않으면 [예시] 문서 행 id 를 하나 이상 인용한다. "
        "싫어하는 것을 근거로 썼으면 무엇을 피했는지 reason 에 쓴다."
    ),
}


@dataclass(frozen=True)
class Rejection:
    index: int  # 후보 순서 (0부터)
    reason: RejectReason


@dataclass(frozen=True)
class Removal:
    """안전 필터로 빠진 후보. 거절과 달리 모델에게 돌려주지 않는다 — 걸린 사유를 알려 주면
    어휘 회피를 가르치는 셈이다 (D6 · Tool_공통.md §5-2)."""

    index: int  # 후보 순서 (0부터)
    hits: tuple[str, ...]  # 걸린 위험 축 · "health_safety". 로그용 — 원문을 싣지 않는다


@dataclass(frozen=True)
class Review:
    drafts: tuple[SuggestionDraft, ...]
    rejections: tuple[Rejection, ...]
    removed: tuple[Removal, ...] = ()
    # drafts 와 같은 순서로 짝지은 위험 용어 경고 문구 (상수). 경고가 없으면 빈 튜플
    warnings: tuple[tuple[str, ...], ...] = ()
    # 통과한 바깥 활동(setting=outdoor)의 (정규화한 content, 날). 날씨 안내를 결과에 실제로 나간
    # 바깥 활동의 날로 고르는 데 쓴다 — 오늘 해가 졌어도 내일 공원은 나간다 (#515 리뷰)
    outdoor: frozenset[tuple[str, DayLabel]] = frozenset()


def review_candidates(
    candidates: Sequence[ActivityCandidate],
    *,
    months: int,
    seen: Seen,
    recent_activities: Collection[str],
    safety: SafetyTerms,
    places: Mapping[str, PlaceRow] | None = None,
    outdoor: Mapping[DayLabel, bool] | None = None,
    docs: Docs | None = None,
    today: date,
) -> Review:
    """후보를 3-3 순서로 검사해 통과한 것은 SuggestionDraft 로, 걸린 것은 사유로 돌려준다.

    `safety` 는 build_gate 가 run state 에 담은 health_safety 행으로 만든 대조표다 — 여기서
    다시 읽지 않는다. 안전 필터에 걸린 후보는 `removed` 로 빠지고 뒤 검사를 타지 않는다.

    `seen` 은 이번 run 에서 search_activity_memory 가 돌려준 근거다. 기피 대상 거절도 이 표를
    쓴다 — 모델이 인용하지 않은 기피 근거여도 조회됐으면 막는다.
    `places` 는 이번 run 에서 search_nearby_places 가 돌려준 장소다(이름 → 행).
    `outdoor` 는 후보의 날(`day`)마다 바깥 활동을 할 수 있는지다 (`outing.outdoor_on`). 출력 tool 이
    후보에 나온 날을 전부 채운다. None 이면 날씨로 거르지 않는다(날씨와 무관한 검사의 테스트용).
    `docs` 는 이번 run 의 프롬프트 [예시]에 실은 문서 행이다. 인용하면 `activity_doc` 근거가
    되고, 아이 기록이 아니라 개인화로 세지 않는다 — 문서 행만 단 추천은 `general` 이다.
    `today` 는 `build()` 의 6개월 판정 기준일이다.
    """
    # 대조는 subject 로 한다 — 관찰의 label 은 "물감 놀이" 같은 문장이라
    # 후보에 그대로 나오는 일이 드물다
    avoided = tuple(
        subject
        for ranked in seen.values()
        if ranked.is_avoidance
        and len(subject := normalize_activity(ranked.subject)) >= MIN_AVOIDED_LABEL
    )
    # 띄어쓰기만 다르게 써도 같은 곳으로 본다. 붙일 때는 조회 결과의 이름을 그대로 쓴다
    known_places = {
        key: row for row in (places or {}).values() if (key := normalize_activity(row.name))
    }
    drafts: list[SuggestionDraft] = []
    warnings: list[tuple[str, ...]] = []
    outdoor_drafts: set[tuple[str, DayLabel]] = set()
    rejections: list[Rejection] = []
    removed: list[Removal] = []
    for index, candidate in enumerate(candidates):
        # 1. 안전 필터 — 맨 앞이다. 걸린 후보가 뒤 검사를 타면 엉뚱한 사유가 나간다
        check = check_candidate(candidate, months=months, terms=safety)
        if check.blocked:
            removed.append(Removal(index=index, hits=check.hits))
            continue
        reason, citations, place = _check(
            candidate,
            months=months,
            seen=seen,
            docs=docs or {},
            places=known_places,
            avoided=avoided,
            recent=recent_activities,
            outdoor=outdoor,
        )
        if reason is None:
            try:
                drafts.append(
                    _build(candidate, citations, place, allergens=check.allergens, today=today)
                )
            except SuggestionRejected:
                reason = RejectReason.BUILD
            else:
                warnings.append(check.warnings)
                if candidate.setting == ActivitySetting.OUTDOOR:
                    outdoor_drafts.add((normalize_activity(drafts[-1].content), candidate.day))
        if reason is not None:
            rejections.append(Rejection(index=index, reason=reason))
    return Review(
        drafts=tuple(drafts),
        rejections=tuple(rejections),
        removed=tuple(removed),
        warnings=tuple(warnings),
        outdoor=frozenset(outdoor_drafts),
    )


def error_code(rejections: Sequence[Rejection]) -> str:
    """거절 묶음의 대표 오류 코드. 로그 집계용이다.

    모델에게 가는 설명은 `explain()` 이 후보마다 싣는다.

    조회하지 않은 근거 id 가 하나라도 있으면 공통 `EVIDENCE_REQUIRED` 다. 모델이 id 를 지어낸
    것이라 다른 거절과 따로 세야 한다 (Food 도 같은 코드를 쓴다). 나머지는 `CANDIDATE_REJECTED`.
    """
    if any(r.reason is RejectReason.UNKNOWN_EVIDENCE for r in rejections):
        return ErrorCode.EVIDENCE_REQUIRED
    return ErrorCode.CANDIDATE_REJECTED


def explain(rejections: Sequence[Rejection]) -> str:
    """모델이 읽는 거절 설명. 후보 번호(1부터)와 고칠 방향만 싣는다."""
    return " / ".join(f"{r.index + 1}번 후보: {GUIDANCE[r.reason]}" for r in rejections)


def _check(
    candidate: ActivityCandidate,
    *,
    months: int,
    seen: Seen,
    docs: Docs,
    places: Mapping[str, PlaceRow],
    avoided: tuple[str, ...],
    recent: Collection[str],
    outdoor: Mapping[DayLabel, bool] | None,
) -> tuple[RejectReason | None, tuple[EvidenceCitation, ...], PlaceRow | None]:
    # 1. 안전 필터는 호출부(review_candidates)가 먼저 돌렸다

    # 2. 평가 표현
    texts = (candidate.content, candidate.reason)
    notes = tuple(pick.note for pick in candidate.evidence)
    if any(find_evaluative(text) for text in (*texts, *notes)):
        return RejectReason.EVALUATIVE, (), None

    # 3. Activity 검증
    resolved: list[tuple[EvidencePick, RankedEvidence | ActivityDocRow]] = []
    for pick in candidate.evidence:
        found = _lookup(seen, docs, pick.id)
        if found is None:
            return RejectReason.UNKNOWN_EVIDENCE, (), None
        resolved.append((pick, found))

    place: PlaceRow | None = None
    if candidate.place_name and candidate.place_name.strip():
        place = places.get(normalize_activity(candidate.place_name))
        if place is None:
            return RejectReason.UNKNOWN_PLACE, (), None

    # 그날 날씨 판정이 야외를 막았다(D8 · #377). either 는 실내에서도 되니 통과한다.
    # 표에 없는 날은 판정하지 않은 날이라 막는다 — 맑다고 가정하지 않는다
    if (
        outdoor is not None
        and candidate.setting == ActivitySetting.OUTDOOR
        and not outdoor.get(candidate.day, False)
    ):
        return RejectReason.OUTDOOR_CLOSED, (), None

    content_key = normalize_activity(candidate.content)
    if any(subject in content_key for subject in avoided):
        return RejectReason.AVOIDED, (), None
    # 문서 행 note 도 본다 — "늘 좋아하는" 은 문서가 아니라 아이에 대한 말이다
    if any(overstates(pick.note) and not _confirmed(found) for pick, found in resolved):
        return RejectReason.OVERSTATED_NOTE, (), None
    if is_recent_duplicate(candidate.content, recent):
        return RejectReason.RECENT_DUPLICATE, (), None
    if months < TOGETHER_ONLY_BELOW_MONTH and candidate.caregiver_role != CaregiverRole.TOGETHER:
        return RejectReason.CAREGIVER_ROLE, (), None

    citations = tuple(_cite(found, note=pick.note) for pick, found in resolved)
    return None, citations, place


def _build(
    candidate: ActivityCandidate,
    citations: tuple[EvidenceCitation, ...],
    place: PlaceRow | None,
    *,
    allergens: tuple[str, ...],
    today: date,
) -> SuggestionDraft:
    """allergens 는 안전 필터가 찾은 알레르기 항목 정식 명칭, items 는 후보의 재료다 (#232).

    allergens 는 상태(없음/모름)를 담지 않는다 — 승인할 때 서버가 health_safety 를 다시 읽는다.
    장소가 붙으면 확인 문구를 칸으로 싣는다 — 문장에 섞으면 화면이 따로 세울 수 없다 (#516).
    """
    draft = build(
        agent="activity",
        content=_with_place(candidate.content, place.name if place else None),
        reason=candidate.reason.strip(),
        citations=citations,
        general_reason=GENERAL_REASON,
        allergens=allergens,
        items=tuple(candidate.materials),
        today=today,
    )
    if place is None:
        return draft
    return replace(draft, place_notice=place_notice(place))


def place_notice(place: PlaceRow) -> str:
    """장소가 붙은 추천의 확인 문구 (#516). 모델이 아니라 코드 문구다.

    공공데이터 기준일자가 있으면 그 달을 앞에 둔다. 없는 출처(어린이놀이시설)는 운영 여부 확인만.
    """
    if place.data_date is None:
        return READOUTS.render(PLACE_NOTICE_UNDATED).body
    return READOUTS.render(
        PLACE_NOTICE, year=place.data_date.year, month=place.data_date.month
    ).body


def _with_place(content: str, place: str | None) -> str:
    """장소 이름을 코드가 문장 앞에 붙인다. 모델이 이미 문장에 썼으면 다시 붙이지 않는다."""
    if place is None or normalize_activity(place) in normalize_activity(content):
        return content
    return f"{place}에서 {content}"


def _lookup(seen: Seen, docs: Docs, raw_id: str) -> RankedEvidence | ActivityDocRow | None:
    try:
        key = UUID(raw_id)
    except ValueError:
        return None
    return seen.get(key) or docs.get(key)


def _confirmed(found: RankedEvidence | ActivityDocRow) -> bool:
    return isinstance(found, RankedEvidence) and found.tier == 1


def _cite(found: RankedEvidence | ActivityDocRow, *, note: str) -> EvidenceCitation:
    """기억은 `cite()` 로, 문서 행은 `activity_doc` 근거로. 문서 행은 기피가 없다(polarity 0)."""
    if isinstance(found, RankedEvidence):
        return cite(found, note=note)
    return EvidenceCitation(ref=Ref(kind="activity_doc", id=found.id), note=note, label=found.title)
