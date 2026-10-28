"""놀이 후보 출력 검증 — 설계 3-3 순서.

  1. 안전 필터       위험 용어(월령별 차단 · 경고) · health_safety(active). 걸린 후보는 거절이
                     아니라 풀에서 뺀다(`removed`) — 모델에게 사유를 돌려주지 않는다 (§5-2)
  2. 평가 표현       content · reason · note
  3. Activity 검증   근거 id · 장소 이름 · 바깥 활동이 어려운 날의 야외 후보 · 기피 대상 ·
                     note 반복 표현 · 최근 중복 · 0–17개월 보호자 동반
  4. build()         6개월이 안 지난 아이 기록 근거 수로 kind 를 정한다. 모델이 고르지 않는다.
                     장소 이름은 코드가 content 앞에 붙인다. allergens · items 도 코드가 채운다
  5. check_count()   최대 3개 — 호출부(출력 tool)가 부른다

순서가 결과를 바꾼다. 안전에 걸린 후보가 뒤 검사를 타면 엉뚱한 사유가 나간다.
걸린 후보는 고치지 않고 통째로 빼거나 거절한다. "물놀이터" 를 "얕은 물놀이터" 로 고쳐 통과시키지
않는다.
사유는 코드로만 남긴다 — 활동명 · 근거 문장을 싣지 않는다 (로그로 흘러간다).
"""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import cache
from math import ceil
from uuid import UUID

from app.agents.activity.doc_seed import activity_doc_seed
from app.agents.activity.result import ErrorCode
from app.agents.activity.rules import is_recent_duplicate, normalize_activity, overstates
from app.agents.activity.schemas.common import (
    ActivitySetting,
    CaregiverRole,
    DayLabel,
    EvidencePick,
)
from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.activity.store.ports import ActivityDocRow
from app.agents.activity.tools.filters import SafetyTerms, check_candidate
from app.agents.common.evidence import RankedEvidence, cite
from app.agents.common.reference import age_gates
from app.agents.common.refs import EvidenceCitation, Ref
from app.agents.common.suggestion import SuggestionDraft, SuggestionRejected, build
from app.rules.evaluative import find_evaluative
from app.rules.term_match import normalize, split_words

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
    UNRELATED_DOC = "unrelated_doc"
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
    RejectReason.UNRELATED_DOC: (
        "근거로 단 [예시] 문서 행이 그 활동과 맞지 않는다. 활동에 맞는 [예시] 행을 근거로 달거나,"
        " [예시]에 있는 활동으로 바꾼다."
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
    places: Collection[str] = (),
    outdoor: Mapping[DayLabel, bool] | None = None,
    docs: Docs | None = None,
    today: date,
) -> Review:
    """후보를 3-3 순서로 검사해 통과한 것은 SuggestionDraft 로, 걸린 것은 사유로 돌려준다.

    `safety` 는 build_gate 가 run state 에 담은 health_safety 행으로 만든 대조표다 — 여기서
    다시 읽지 않는다. 안전 필터에 걸린 후보는 `removed` 로 빠지고 뒤 검사를 타지 않는다.

    `seen` 은 이번 run 에서 search_activity_memory 가 돌려준 근거다. 기피 대상 거절도 이 표를
    쓴다 — 모델이 인용하지 않은 기피 근거여도 조회됐으면 막는다.
    `places` 는 이번 run 에서 search_nearby_places 가 돌려준 장소 이름이다.
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
    known_places = {key: name for name in places if (key := normalize_activity(name))}
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
    places: Mapping[str, str],
    avoided: tuple[str, ...],
    recent: Collection[str],
    outdoor: Mapping[DayLabel, bool] | None,
) -> tuple[RejectReason | None, tuple[EvidenceCitation, ...], str | None]:
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

    # 문서 행은 "이 활동의 근거" 다 — [예시]에 있기만 하면 통과시키면 손 씻기 문서가 종이컵 쌓기의
    # 근거로 붙는다(운영 QA). 맞지 않는 문서 행은 근거에서 뗀다. 남는 근거가 없으면 거절한다 —
    # 일반 추천은 근거가 문서 행뿐이라 여기서 막아야 한다 (루트 §2). 아이 기록이나 맞는 문서 행이
    # 남으면 후보는 그대로 나간다 — 인용 하나 때문에 후보를 다시 내게 하면 재호출만 늘어 failed 에
    # 가까워진다 (#557 리뷰)
    related = [
        (pick, found)
        for pick, found in resolved
        if not isinstance(found, ActivityDocRow) or _doc_related(candidate, found)
    ]
    if resolved and not related:
        return RejectReason.UNRELATED_DOC, (), None
    resolved = related

    place: str | None = None
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
    place: str | None,
    *,
    allergens: tuple[str, ...],
    today: date,
) -> SuggestionDraft:
    """allergens 는 안전 필터가 찾은 알레르기 항목 정식 명칭, items 는 후보의 재료다 (#232).

    allergens 는 상태(없음/모름)를 담지 않는다 — 승인할 때 서버가 health_safety 를 다시 읽는다.
    """
    return build(
        agent="activity",
        content=_with_place(candidate.content, place),
        reason=candidate.reason.strip(),
        citations=citations,
        general_reason=GENERAL_REASON,
        allergens=allergens,
        items=tuple(candidate.materials),
        today=today,
    )


def _with_place(content: str, place: str | None) -> str:
    """장소 이름을 코드가 문장 앞에 붙인다. 모델이 이미 문장에 썼으면 다시 붙이지 않는다."""
    if place is None or normalize_activity(place) in normalize_activity(content):
        return content
    return f"{place}에서 {content}"


# 문서에서 활동을 가르지 못하는 낱말 — 어느 놀이 문장에나 나온다
_GENERIC_WORDS = frozenset(
    {
        "보기",
        "하기",
        "놀이",
        "만들기",
        "함께",
        "같이",
        "다음",
        "따라",
        "놀기",
        "하며",
        "보며",
        "주기",
        "보고",
        "한다",
        "하고",
        "본다",
        "다시",
        "하나",
        "있는",
        "같은",
        "내며",
    }
)
# 시드 행의 이 비율(최소 `COMMON_DOC_WORD_MIN_ROWS` 행) 이상에 나오는 낱말은 활동을 가르지
# 못한다 — "그림" · "놀이" (#557 리뷰). 행 수로 고정하면 시드가 늘수록 "종이컵" 같은 구체적인
# 낱말까지 흔한 말로 빠진다
COMMON_DOC_WORD_SHARE = 0.05
COMMON_DOC_WORD_MIN_ROWS = 4


def _stems(text: str) -> set[str]:
    """두 글자 이상 낱말. 세 글자 이상은 앞 두 글자 — 모델이 "쌓기" 를 "쌓아" 로 쓴다."""
    return {
        word if len(word) == 2 else word[:2]
        for word in split_words(text)
        if len(word) >= 2 and word not in _GENERIC_WORDS
    }


def _doc_keys(doc: ActivityDocRow) -> set[str]:
    """문서 행의 준비물(그대로) · 제목 · 본문 낱말."""
    materials = {key for item in doc.materials if len(key := normalize(item)) >= 2}
    return materials | _stems(doc.title) | _stems(doc.body)


def common_doc_words(rows: Sequence[ActivityDocRow]) -> frozenset[str]:
    """문서 행 여럿(`COMMON_DOC_WORD_SHARE` 이상)에 나오는 낱말."""
    counts = Counter(key for row in rows for key in _doc_keys(row))
    least = max(COMMON_DOC_WORD_MIN_ROWS, ceil(len(rows) * COMMON_DOC_WORD_SHARE))
    return frozenset(key for key, count in counts.items() if count >= least)


@cache
def _common_doc_words() -> frozenset[str]:
    """시드로 센 흔한 낱말. 시드가 DB 행의 정본이다."""
    return common_doc_words([entry.to_row() for entry in activity_doc_seed()])


def _doc_related(candidate: ActivityCandidate, doc: ActivityDocRow) -> bool:
    """후보가 그 문서 행의 활동인가 — 문서를 가르는 낱말이 후보에 하나라도 나오는가.

    - 문서의 준비물 · 제목 · 본문 낱말에서 여러 행에 흔한 낱말("그림" · "놀이")을 뺀 것으로 본다.
      제목만 보면 "곰처럼 네 발로 걸어 보기" 가 "동물 흉내 내며 걷기" 와 안 겹치고(본문에는 "곰처럼"
      이 있다), 흔한 낱말까지 보면 "그림책 다음 장 맞혀 보기" 가 "손 그림자" 와 겹친다 (#557 리뷰).
    - 가르는 낱말이 하나도 없는 문서(전부 흔한 말)는 흔한 낱말로 본다. 흔한 낱말이라도 제목 낱말
      둘이 같이 나오면 같은 활동이다. 낱말이 아예 없으면 막지 않는다.
    - 완전한 의미 대조가 아니라, 손 씻기 문서를 종이컵 쌓기에 다는 것처럼 아무것도 안 겹치는
      인용을 막는 하한이다.
    """
    keys = _doc_keys(doc)
    if not keys:
        return True
    texts = [normalize(candidate.content), *(normalize(item) for item in candidate.materials)]

    def found(key: str) -> bool:
        return any(key in text for text in texts)

    informative = keys - _common_doc_words()
    if any(found(key) for key in informative or keys):
        return True
    # 제목이 흔한 낱말뿐인 행("노래에 맞춰 손 흔들기")은 흔한 낱말 둘이 같이 나오면 같은 활동이다.
    # 제목 낱말이 하나뿐이면("상자로 집 만들기" 의 상자) 그 하나다
    title = _stems(doc.title)
    return bool(title) and sum(found(key) for key in title) >= min(2, len(title))


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
