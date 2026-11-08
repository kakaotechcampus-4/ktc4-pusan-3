"""놀이 후보 출력 검증 — 설계 3-3 순서.

  1. 안전 필터       위험 용어(월령별 차단 · 경고) · health_safety(active). 걸린 후보는 거절이
                     아니라 풀에서 뺀다(`removed`) — 모델에게 사유를 돌려주지 않는다 (§5-2)
  2. 평가 표현       content · reason · note
  3. Activity 검증   근거 id · 장소 · 행사 이름(문장에만 쓴 행사도 붙인다 · 한 후보에 둘이면
                     거절) · 그날 열리지 않는 행사(일정으로) · 그날이 아닌 날의 행사 장소 ·
                     말한 지역의 장소에 "근처" · 바깥 활동이
                     어려운 날의 야외 후보(행사 포함) · 기피 대상 · note 반복 표현 · 최근 중복 ·
                     0–17개월 보호자 동반
  4. build()         6개월이 안 지난 아이 기록 근거 수로 kind 를 정한다. 모델이 고르지 않는다.
                     장소 이름은 코드가 content 앞에 붙이고, 확인 문구(place_notice)를 칸으로
                     싣는다. allergens · items · 위험 경고(warnings)도 코드가 채운다
  5. check_count()   최대 3개 — 호출부(출력 tool)가 부른다

순서가 결과를 바꾼다. 안전에 걸린 후보가 뒤 검사를 타면 엉뚱한 사유가 나간다.
걸린 후보는 고치지 않고 통째로 빼거나 거절한다. "물놀이터" 를 "얕은 물놀이터" 로 고쳐 통과시키지
않는다.
사유는 코드로만 남긴다 — 활동명 · 근거 문장을 싣지 않는다 (로그로 흘러간다).
"""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from uuid import UUID

from app.agents.activity.events import core_name, open_on
from app.agents.activity.readouts import (
    PLACE_NOTICE,
    PLACE_NOTICE_EVENT,
    PLACE_NOTICE_FACILITY,
    PLACE_NOTICE_UNDATED,
    READOUTS,
)
from app.agents.activity.result import ErrorCode
from app.agents.activity.rules import invented_facilities, is_recent_duplicate, normalize_activity
from app.agents.activity.schemas.common import (
    ActivitySetting,
    CaregiverRole,
    EvidencePick,
)
from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.activity.store.ports import (
    ActivityDocRow,
    EventRow,
    PlaceRow,
    PlaceSourceName,
)
from app.agents.activity.tools.filters import SafetyTerms, check_candidate
from app.agents.activity.tools.outing import INDOOR_CATEGORIES, days_of
from app.agents.common.evidence import RankedEvidence, cite, overstates
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

# 어린이놀이시설(키즈카페 · 육아종합지원센터) 장소의 출처. 이용료 확인 문구를 붙인다 (#516)
FACILITY_SOURCE: PlaceSourceName = "kids_play_facility"

# 아이 기록 근거가 0행이면 build() 가 reason 을 이 문구로 덮어쓴다 (kind="general").
# 화면이 일반 추천임을 표시하고 쌓인 기록 건수를 함께 보여준다 (루트 §2).
GENERAL_REASON = "이 나이 아이들에게 많이 권하는 놀이로 골랐어요."
# 보호자가 놀이를 정해 물었고 아이 기록 근거가 없을 때 — 기준이 [예시]가 아니라 이번 요청이다.
# "맞춰" 가 아니라 "바탕으로" 다 — 요청을 그대로 못 하면 비슷한 놀이로 바꿔 낸다("복숭아 따기 체험"
# → 집에서 하는 과일 놀이, 판정 모델 실측 3%). 바꾼 것도 거짓말이 되지 않는 말로 둔다 (#597 리뷰)
REQUESTED_REASON = "요청하신 놀이를 바탕으로 골랐어요."

# 보호자가 말한 지역에서 찾은 장소에 쓰면 틀리는 말. 보호자는 그 자리에 있지 않다 (`areas.py`)
NEARBY_WORDS = ("근처",)

# 행사를 이름 전체(또는 연도 · 회차 · 괄호 · 부제를 뗀 이름)로 알아보는 최소 길이(정규화한 글자 수).
# 짧은 이름은 흔한 말과 겹친다
MIN_EVENT_NAME = 4
# 장소가 날마다 다른 행사에서 다른 날 장소를 알아보는 낱말의 최소 길이. "모래섬 해변" 을
# "모래섬" 으로 줄여 써도 본다. "공원" · "해변" 같은 두 글자 말은 어느 날 장소에나 있다
MIN_EVENT_PLACE_WORD = 3
# 행사 이름 안의 낱말 하나로 알아보는 최소 길이. 모델은 "2026 대한민국 ○○박람회" 를 "○○박람회" 로
# 줄여 쓴다(11-03 실측). "크리스마스"(5) · "놀이터" 같은 흔한 말로 알아보면 행사가 아닌 후보에
# 붙는다
MIN_EVENT_WORD = 6

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
    EVENT_CLOSED = "event_closed"
    EVENT_PLACE = "event_place"
    NEARBY_WORDING = "nearby_wording"
    OUTDOOR_CLOSED = "outdoor_closed"
    MIXED_PLACES = "mixed_places"
    INVENTED_FACILITY = "invented_facility"
    BUILD = "build_rejected"


# 모델에게 돌려줄 설명 — 무엇을 고치면 되는지만. 걸린 낱말은 알려 주지 않는다
GUIDANCE: dict[RejectReason, str] = {
    RejectReason.EVALUATIVE: "또래 비교나 발달 판정 표현을 쓰지 않는다.",
    RejectReason.UNKNOWN_EVIDENCE: (
        "evidence 에는 search_activity_memory 결과의 id 나 [예시]의 id(예1 같은)만 그대로 쓴다."
    ),
    RejectReason.AVOIDED: "싫어한다고 나온 활동은 다시 내지 않는다. 다른 활동으로 바꾼다.",
    RejectReason.OVERSTATED_NOTE: (
        "note 의 '어제도 · 또 · 늘 · 항상 · 좋아하는' 은 확정된 관심(tier 1)에만 쓴다."
    ),
    RejectReason.RECENT_DUPLICATE: "최근에 한 활동이다. 다른 활동으로 바꾼다.",
    RejectReason.CAREGIVER_ROLE: "이 월령은 보호자가 같이 하는 활동(together)만 낸다.",
    RejectReason.UNKNOWN_PLACE: (
        "place_name 에는 search_nearby_places 결과의 장소 이름이나 search_events 결과의 행사 "
        "이름(name)만 쓴다. 장소 · 행사가 없는 활동으로 바꾸면 비우고, 문장에서도 그 장소 · "
        "행사를 뺀다."
    ),
    RejectReason.EVENT_CLOSED: (
        "후보의 행사는 그날(day) 열리지 않는다. search_events 결과의 open_days 에 있는 날로 day 를 "
        "고치거나, 문장에서도 그 행사를 빼고 다른 활동으로 바꾼다."
    ),
    RejectReason.EVENT_PLACE: (
        "그날(day) 그 행사가 열리는 곳이 아니다. 장소는 search_events 결과의 그날 place 로 "
        "고치거나, 문장에서도 그 행사를 빼고 다른 활동으로 바꾼다."
    ),
    RejectReason.NEARBY_WORDING: (
        "보호자가 말한 지역에서 찾은 장소다. 보호자가 지금 그 자리에 있지 않으니 "
        "'근처' 를 쓰지 않는다."
    ),
    RejectReason.OUTDOOR_CLOSED: (
        "그날(day)은 바깥 활동이 어려운 날이다. 공원 · 놀이터 · 행사를 문장에서도 빼고 indoor 나"
        " either 활동으로 바꾸거나, lookup_weather 로 바깥 활동이 되는 날을 찾아 day 를 고친다."
    ),
    RejectReason.MIXED_PLACES: (
        "한 후보에는 장소나 행사를 하나만 쓴다. 문장에 쓴 행사가 있으면 place_name 에 그 행사 "
        "이름(name)을 쓴다."
    ),
    RejectReason.INVENTED_FACILITY: (
        "장소 이름만 보고 그 안의 시설을 짐작해 쓰지 않는다. 보호자가 말했거나 기록에 있는 시설만 "
        "쓰고, 아니면 시설 이름 없이 놀이 방법만 쓴다."
    ),
    RejectReason.BUILD: (
        "아이 기록을 인용하지 않으면 [예시] 문서 행 id 를 하나 이상 인용한다 "
        "(보호자가 이번 요청에서 직접 말한 놀이일 때만 requested_play 로 면제된다). "
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
    # 통과한 바깥 활동(`_outdoors`)의 (정규화한 content, 날). 날씨 안내를 결과에 실제로 나간
    # 바깥 활동의 날로 고르는 데 쓴다 — 오늘 해가 졌어도 내일 공원은 나간다 (#515 리뷰)
    outdoor: frozenset[tuple[str, str]] = frozenset()
    # 통과한 후보 전부의 날 표현 — 오늘 하는 추천이 없으면 오늘 날씨 안내를 붙이지 않는다 (#800)
    days: frozenset[str] = frozenset()


def review_candidates(
    candidates: Sequence[ActivityCandidate],
    *,
    months: int,
    seen: Seen,
    recent_activities: Collection[str],
    safety: SafetyTerms,
    places: Mapping[str, PlaceRow] | None = None,
    events: Mapping[str, EventRow] | None = None,
    outdoor: Mapping[str, bool] | None = None,
    outdoor_days: Mapping[date, bool] | None = None,
    docs: Docs | None = None,
    area_places: Collection[str] = (),
    today: date,
    requested: bool = False,
    ended_today: Collection[str] = (),
    request_texts: Sequence[str] = (),
) -> Review:
    """후보를 3-3 순서로 검사해 통과한 것은 SuggestionDraft 로, 걸린 것은 사유로 돌려준다.

    `safety` 는 build_gate 가 run state 에 담은 health_safety 행으로 만든 대조표다 — 여기서
    다시 읽지 않는다. 안전 필터에 걸린 후보는 `removed` 로 빠지고 뒤 검사를 타지 않는다.

    `seen` 은 이번 run 에서 search_activity_memory 가 돌려준 근거다. 기피 대상 거절도 이 표를
    쓴다 — 모델이 인용하지 않은 기피 근거여도 조회됐으면 막는다.
    `places` 는 이번 run 에서 search_nearby_places 가 돌려준 장소다(이름 → 행). `events` 는
    search_events 가 돌려준 행사다 — place_name 이 장소에 없으면 여기서 찾는다(이름이 겹치면
    상설 장소가 먼저다). 모델이 가리킨 방식 그대로 찾고, 문장에만 쓴 행사도 붙인다(`_attach`) —
    행사 이름이 나간 후보는 반드시 검증을 받는다. 행사는 후보의 날에 열려야 하고, 바깥 나들이로
    본다.
    `area_places` 는 그중 보호자가 말한 지역에서 찾은 장소 · 행사 이름이다 — 이 장소에 "근처" 를
    쓰면 거절한다.
    `outdoor` 는 후보의 날(`day`)마다 바깥 활동을 할 수 있는지다 (`outing.outdoor_on`). 출력 tool 이
    후보에 나온 날을 전부 채운다. None 이면 날씨로 거르지 않는다(날씨와 무관한 검사의 테스트용).
    `outdoor_days` 는 같은 판정의 날짜별 값이다. 행사는 열리는 날 중 하루가 바깥이 되는 날이어야
    한다 — 주말이 일요일만 맑아도 토요일에만 열리는 행사는 갈 날이 없다 (#668 리뷰).
    `docs` 는 이번 run 의 프롬프트 [예시]에 실은 문서 행이다. 인용하면 `activity_doc` 근거가
    되고, 아이 기록이 아니라 개인화로 세지 않는다 — 문서 행만 단 추천은 `general` 이다.
    `today` 는 `build()` 의 6개월 판정 기준일이다.
    `request_texts` 는 보호자 요청 글이다 — 장소가 붙은 후보의 시설 낱말이 요청에도 조회한
    기록에도 없으면 지어낸 것으로 거절한다 (#814).
    `ended_today` 는 오늘 운영 시간이 이미 끝난 행사 이름이다 — 그 행사는 오늘 열리지 않은
    것으로 본다.
    """
    # 대조는 subject 로 한다 — 관찰의 label 은 "물감 놀이" 같은 문장이라
    # 후보에 그대로 나오는 일이 드물다
    avoided = tuple(
        subject
        for ranked in seen.values()
        if ranked.is_avoidance
        and len(subject := normalize_activity(ranked.subject)) >= MIN_AVOIDED_LABEL
    )
    known = _Known.of(places or {}, events or {})
    # 시설 낱말을 써도 되는 글 — 보호자 요청 · 이번 run 에 조회한 기록의 이름
    said = (
        *request_texts,
        *(text for ranked in seen.values() for text in (ranked.label, ranked.subject)),
    )
    far_places = frozenset(normalize_activity(name) for name in area_places)
    drafts: list[SuggestionDraft] = []
    outdoor_drafts: set[tuple[str, str]] = set()
    draft_days: set[str] = set()
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
            known=known,
            far_places=far_places,
            avoided=avoided,
            recent=recent_activities,
            outdoor=outdoor,
            outdoor_days=outdoor_days,
            today=today,
            ended_today=ended_today,
            said=said,
        )
        if reason is None:
            try:
                drafts.append(
                    _build(
                        candidate,
                        citations,
                        place,
                        allergens=check.allergens,
                        warnings=check.warnings,
                        today=today,
                        requested=requested,
                    )
                )
            except SuggestionRejected:
                reason = RejectReason.BUILD
            else:
                draft_days.add(candidate.day)
                if _outdoors(candidate, place):
                    outdoor_drafts.add((normalize_activity(drafts[-1].content), candidate.day))
        if reason is not None:
            rejections.append(Rejection(index=index, reason=reason))
    return Review(
        drafts=tuple(drafts),
        rejections=tuple(rejections),
        removed=tuple(removed),
        outdoor=frozenset(outdoor_drafts),
        days=frozenset(draft_days),
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
    known: "_Known",
    far_places: frozenset[str],
    avoided: tuple[str, ...],
    recent: Collection[str],
    outdoor: Mapping[str, bool] | None,
    outdoor_days: Mapping[date, bool] | None,
    today: date,
    ended_today: Collection[str] = (),
    said: Sequence[str] = (),
) -> tuple[RejectReason | None, tuple[EvidenceCitation, ...], PlaceRow | EventRow | None]:
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

    reason, place = _attach(candidate, known)
    if reason is not None:
        return reason, (), None
    if isinstance(place, EventRow):
        open_days = [
            day
            for day in days_of(candidate.day, today)
            if open_on(place, (day,)) and not (day == today and place.name in ended_today)
        ]
        if not open_days:
            return RejectReason.EVENT_CLOSED, (), None
        if _elsewhere(candidate, place, open_days):
            return RejectReason.EVENT_PLACE, (), None
        # 열리는 날과 바깥이 되는 날이 같은 날이어야 한다. 표에 없는 날은 판정하지 않은 날이다
        if outdoor_days is not None and not any(outdoor_days.get(day, False) for day in open_days):
            return RejectReason.OUTDOOR_CLOSED, (), None
    if (
        place is not None
        and normalize_activity(place.name) in far_places
        and any(word in text for text in texts for word in NEARBY_WORDS)
    ):
        return RejectReason.NEARBY_WORDING, (), None
    # 장소 이름만 보고 시설을 지어내지 않는다 — 공공데이터에 시설 칸이 없다 (#814)
    if isinstance(place, PlaceRow) and invented_facilities(texts, said):
        return RejectReason.INVENTED_FACILITY, (), None

    # 그날 날씨 판정이 야외를 막았다(D8 · #377). 표에 없는 날은 판정하지 않은 날이라 막는다 —
    # 맑다고 가정하지 않는다
    if (
        outdoor is not None
        and _outdoors(candidate, place)
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


@dataclass(frozen=True)
class _Known:
    """이번 run 에 조회한 장소 · 행사를 후보가 가리키는 이름으로 찾는 표.

    띄어쓰기만 다르게 써도 같은 곳으로 본다. 붙일 때는 조회 결과의 이름을 그대로 쓴다.
    """

    places: Mapping[str, PlaceRow]  # 정규화한 이름 → 장소
    events: Mapping[str, EventRow]  # 정규화한 이름 → 행사
    # 알아보는 이름(`_event_keys`) → 행사. 두 행사가 같이 쓰는 이름 · 장소 이름과 같은 이름은 뺐다 —
    # 어느 쪽인지 모른다
    event_keys: Mapping[str, EventRow]
    # 정규화한 개최 장소 → 행사. 장소가 날마다 다른 행사는 그날그날의 장소도 넣는다 — tool 결과의
    # place 가 그날 장소다
    venues: Mapping[str, tuple[EventRow, ...]]

    @classmethod
    def of(cls, places: Mapping[str, PlaceRow], events: Mapping[str, EventRow]) -> "_Known":
        by_place = {key: row for row in places.values() if (key := normalize_activity(row.name))}
        owners: dict[str, list[EventRow]] = {}
        venues: dict[str, list[EventRow]] = {}
        for event in events.values():
            for key in _event_keys(event):
                owners.setdefault(key, []).append(event)
            held = {event.place, *(name for _, name in event.schedule.day_places)}
            for venue in {normalize_activity(name) for name in held} - {""}:
                venues.setdefault(venue, []).append(event)
        return cls(
            places=by_place,
            events={key: row for row in events.values() if (key := normalize_activity(row.name))},
            event_keys={
                key: rows[0]
                for key, rows in owners.items()
                if len(rows) == 1 and key not in by_place
            },
            venues={key: tuple(rows) for key, rows in venues.items()},
        )

    def named(self, text: str) -> dict[str, EventRow]:
        """문장에 이름이 나온 행사 (행사 이름 → 행)."""
        written = normalize_activity(text)
        return {row.name: row for key, row in self.event_keys.items() if key in written}

    def pointed(self, key: str) -> dict[str, EventRow]:
        """정규화한 place_name 이 가리키는 행사 — 행사 이름 · 줄인 이름 · 개최 장소."""
        found = {row.name: row for name, row in self.event_keys.items() if name in key}
        if (row := self.events.get(key)) is not None:
            found[row.name] = row
        found.update((row.name, row) for row in self.venues.get(key, ()))
        return found


def _event_keys(event: EventRow) -> set[str]:
    """문장 · place_name 에서 이 행사를 알아보는 이름 — 원래 이름 · 연도 · 회차 · 괄호 · 부제를 뗀
    이름(`MIN_EVENT_NAME` 자 이상)과 그 안의 긴 낱말(`MIN_EVENT_WORD` 자 이상)."""
    core = core_name(event.name)
    names = {normalize_activity(event.name), normalize_activity(core)}
    words = {normalize_activity(word) for word in core.split()}
    return {name for name in names if len(name) >= MIN_EVENT_NAME} | {
        word for word in words if len(word) >= MIN_EVENT_WORD
    }


def _attach(
    candidate: ActivityCandidate, known: _Known
) -> tuple[RejectReason | None, PlaceRow | EventRow | None]:
    """후보에 붙는 장소 · 행사. 모델이 가리킨 방식 그대로 찾는다.

    11-03 실측(실제 TourAPI · 실제 모델) — 모델은 place_name 에 행사 대신 개최 장소를 쓰고, 문장에는
    줄인 이름을 쓰고, 거절되면 place_name 만 비우고 행사를 문장에 남겼다. 그래서
    - place_name 이 장소 이름이면 장소다. 문장에 그 장소에서 열리는 행사를 썼으면 그 행사다.
    - 아니면 행사 이름 · 줄인 이름 · 개최 장소로 찾는다. 개최 장소가 같은 행사가 여럿이면 문장의
      행사 이름으로 고르고, 문장에도 없으면 못 고른다(UNKNOWN_PLACE). 개최 장소를 여럿 적은 행사
      ("○○해수욕장, △△ 일원")는 그중 한 곳만 써도 문장에 그 행사가 있으면 그 행사다 — 문장에 없으면
      그곳에서 하는 다른 활동일 수 있어 붙이지 않는다.
    - place_name 이 비어도 문장에 행사 이름이 나오면 그 행사다 — 🚨 행사 이름이 나간 후보는 반드시
      붙어서 그날 열리는지 · 바깥이 되는지 검증을 받는다.
    - 한 후보에 장소 · 행사가 둘이면 거절한다(MIXED_PLACES) — 하나만 붙고 나머지는 검증 없이 나간다.
    """
    named = known.named(candidate.content)
    declared = normalize_activity(candidate.place_name or "")
    if not declared:
        if len(named) > 1:
            return RejectReason.MIXED_PLACES, None
        return None, next(iter(named.values()), None)
    if (place := known.places.get(declared)) is not None:
        if not named:
            return None, place
        # 그 장소에서 열리는 행사를 문장에 썼으면 그 행사다
        if len(named) == 1 and _held_at(declared, row := next(iter(named.values()))):
            return None, row
        return RejectReason.MIXED_PLACES, None
    pointed = known.pointed(declared)
    if len(named) == 1:
        row = next(iter(named.values()))
        if row.name in pointed or _held_at(declared, row):
            return None, row
    if not pointed:
        return RejectReason.UNKNOWN_PLACE, None
    if named:
        return RejectReason.MIXED_PLACES, None
    if len(pointed) == 1:
        return None, next(iter(pointed.values()))
    return RejectReason.UNKNOWN_PLACE, None


def _outdoors(candidate: ActivityCandidate, place: PlaceRow | EventRow | None) -> bool:
    """바깥에서 하는 후보인가. either 는 실내에서도 되니 바깥이 아니다.

    행사가 붙으면 바깥이다 — 실내 공연도 오가는 길과 줄이 바깥이라 행사 조회와 같은 기준이다.

    붙은 장소가 실내 종류가 아니면(공원 · 실외 놀이터) setting 과 상관없이 바깥이다. 장소 조회는
    그 조회의 `day` 로만 바깥 종류를 막아서, 오늘로 찾은 공원을 비 오는 내일 either 활동에 붙이면
    여기서 막아야 한다 (#600 리뷰).
    """
    if candidate.setting == ActivitySetting.OUTDOOR or isinstance(place, EventRow):
        return True
    return place is not None and place.category not in INDOOR_CATEGORIES


def _build(
    candidate: ActivityCandidate,
    citations: tuple[EvidenceCitation, ...],
    place: PlaceRow | EventRow | None,
    *,
    allergens: tuple[str, ...],
    warnings: tuple[str, ...],
    today: date,
    requested: bool = False,
) -> SuggestionDraft:
    """allergens 는 안전 필터가 찾은 알레르기 항목 정식 명칭, items 는 후보의 재료다 (#232).

    `requested` 면 보호자가 놀이를 정해 물은 것이다. 아이 기록 근거가 없으면 [예시] 근거 없이도
    일반 추천으로 나가고 이유는 `REQUESTED_REASON` 이다 — 막연한 요청만 [예시]를 근거로 단다.

    allergens 는 상태(없음/모름)를 담지 않는다 — 승인할 때 서버가 health_safety 를 다시 읽는다.
    장소가 붙으면 확인 문구를(행사면 기간도), 위험 용어에 걸리면 그 축의 경고 문구를 칸으로
    싣는다 — 문장에 섞으면 화면이 따로 세울 수 없다 (#516 · #402).
    """
    draft = build(
        agent="activity",
        content=_with_place(candidate.content, place),
        reason=candidate.reason.strip(),
        citations=citations,
        general_reason=REQUESTED_REASON if requested else GENERAL_REASON,
        allergens=allergens,
        items=tuple(candidate.materials),
        requested=requested,
        today=today,
    )
    event = place if isinstance(place, EventRow) else None
    return replace(
        draft,
        place_notice=place_notice(place) if place is not None else None,
        event_starts_on=event.starts_on if event else None,
        event_ends_on=event.ends_on if event else None,
        warnings=warnings,
    )


def place_notice(place: PlaceRow | EventRow) -> str:
    """장소가 붙은 추천의 확인 문구 (#516). 모델이 아니라 코드 문구다.

    공공데이터 기준일자가 있으면 그 달을 앞에 둔다. 어린이놀이시설은 이용료도 확인해 달라고 한다 —
    민간 키즈카페는 영업장이고, 공공 시설도 이용료 · 예약이 있는 곳이 많다. 행사는 출처와 함께
    일정 · 관람 연령을 확인해 달라고 한다.
    """
    if isinstance(place, EventRow):
        return READOUTS.render(PLACE_NOTICE_EVENT).body
    if place.source == FACILITY_SOURCE:
        return READOUTS.render(PLACE_NOTICE_FACILITY).body
    if place.data_date is None:
        return READOUTS.render(PLACE_NOTICE_UNDATED).body
    return READOUTS.render(
        PLACE_NOTICE, year=place.data_date.year, month=place.data_date.month
    ).body


def _elsewhere(candidate: ActivityCandidate, event: EventRow, days: Sequence[date]) -> bool:
    """장소가 날마다 다른 행사인데 후보가 그날(`days`)이 아닌 날의 장소를 썼다.

    다른 날 장소의 이름 · 그 안의 낱말(`MIN_EVENT_PLACE_WORD` 자 이상)이 문장이나 place_name 에
    있으면 쓴 것으로 본다. 그날 장소에도 있는 낱말은 보지 않는다. 그날 장소를 모르면 보지 않는다.
    """
    places = event.schedule.day_places
    there = {name for day, name in places if day in days}
    if not there:
        return False
    here = {normalize_activity(word) for name in there for word in (name, *name.split())}
    written = normalize_activity(f"{candidate.content} {candidate.place_name or ''}")
    for name in {name for _, name in places} - there:
        words = {normalize_activity(word) for word in (name, *name.split())} - here
        if any(len(word) >= MIN_EVENT_PLACE_WORD and word in written for word in words):
            return True
    return False


def _held_at(declared: str, event: EventRow) -> bool:
    """정규화한 place_name 이 행사 개최 장소의 일부인가 — 개최 장소를 여럿 적은 행사의 한 곳."""
    return len(declared) >= MIN_EVENT_NAME and declared in normalize_activity(event.place)


def _with_place(content: str, place: PlaceRow | EventRow | None) -> str:
    """장소 이름을 코드가 문장 앞에 붙인다. 모델이 이미 문장에 썼으면 다시 붙이지 않는다.

    행사는 줄여 쓴 이름(`_event_keys`)도 쓴 것으로 본다 — 긴 공식 이름을 다시 붙이면
    "○○ 작품투어 (…)에서 ○○ 작품투어에서 …" 가 된다 (11-03 실측). 이름의 뒤쪽 두 낱말
    이상(*"전통시장 박람회"*)만 썼으면 그 자리를 행사 이름으로 바꾼다 (`_spell_out`).

    모델이 문장을 "~에서" 로 시작했으면 "~에서" 를 두 번 붙이지 않는다 (10-28 QA).
    - 장소 이름이 그 낱말(두 낱말까지)로 끝나면 그 자리를 바꾼다 — "도서관에서 …" →
      "○○도서관에서 …", "귤 축제에서 …" → "○○ 귤 축제에서 …" (11-03 실측)
    - 아니면 그 앞에 장소 이름만 둔다 — "놀이터에서 …" → "○○공원 놀이터에서 …"
    """
    if place is None:
        return content
    written = normalize_activity(content)
    names = {normalize_activity(place.name)}
    if isinstance(place, EventRow):
        names |= _event_keys(place)
    if any(name in written for name in names):
        return content
    if isinstance(place, EventRow) and (spelled := _spell_out(content, place)) is not None:
        return spelled
    head, at, tail = content.strip().partition("에서 ")
    where = normalize_activity(head)
    if not (at and tail and where) or len(head.split()) > 2:
        return f"{place.name}에서 {content}"
    if normalize_activity(place.name).endswith(where):
        return f"{place.name}에서 {tail}"
    # 한 낱말이면 장소 안의 한 곳이다. 두 낱말은 바꿀 때만 본다 — "가족과 공원에서" 는 곳이 아니다
    if " " not in head:
        return f"{place.name} {content}"
    return f"{place.name}에서 {content}"


def _spell_out(content: str, event: EventRow) -> str | None:
    """문장에 쓴 행사 이름의 뒤쪽(두 낱말 이상)을 행사 이름으로 바꾼 문장. 없으면 None.

    이미 붙은 행사의 표시만 고친다 — 행사를 알아보는 데(`_Known.named`)는 쓰지 않는다.
    다른 낱말의 일부("늦가을 축제" 의 "가을 축제")는 바꾸지 않는다.
    """
    words = core_name(event.name).split()
    for start in range(len(words) - 1):
        chars = normalize_activity(" ".join(words[start:]))
        pattern = r"(?<!\w)" + r"\W*".join(map(re.escape, chars))
        if chars and (found := re.search(pattern, content, re.IGNORECASE)) is not None:
            return f"{content[: found.start()]}{event.name}{content[found.end() :]}"
    return None


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
