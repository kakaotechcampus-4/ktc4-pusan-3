"""출력 검증 — 설계 3-3 순서 중 안전 필터를 뺀 나머지.

- 검사마다 거절 · 통과 양쪽을 본다.
- 지어낸 근거 id 는 거절된다. 통과한 근거는 공통 EvidenceCitation 으로 바뀐다.
- 거절 사유를 모델에게 돌려줄 때 활동명 · 근거 문장을 싣지 않는다.
- 장소 이름은 이번 run 장소 조회 결과에 있어야 하고, 통과하면 코드가 문장 앞에 붙인다.
"""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.events import schedule_days
from app.agents.activity.result import ErrorCode
from app.agents.activity.review import (
    GENERAL_REASON,
    GUIDANCE,
    MIN_AVOIDED_LABEL,
    REQUESTED_REASON,
    RejectReason,
    explain,
    review_candidates,
)
from app.agents.activity.rules import (
    is_recent_duplicate,
    normalize_activity,
    said_in_request,
)
from app.agents.activity.schemas.common import (
    ActivitySetting,
    CaregiverRole,
    DayLabel,
    EventKind,
    Intensity,
    PlaceCategory,
)
from app.agents.activity.schemas.recommend import ActivityCandidate, ProposeActivityCandidatesArgs
from app.agents.activity.store.inmemory import (
    InMemoryActivityMemory,
    InMemorySafety,
    in_memory_ports,
)
from app.agents.activity.store.ports import (
    ActivityDocRow,
    ActivityObservation,
    EventRow,
    EventSchedule,
    PlaceRow,
    SafetyEntry,
)
from app.agents.activity.tools.filters import safety_terms
from app.agents.activity.tools.recommend import propose_activity_candidates
from app.agents.common.evidence import RankedEvidence, overstates
from app.agents.common.reference import hazard_terms
from app.agents.common.refs import Ref

CHILD = UUID(int=1)
TODAY = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)

SAND = UUID(int=11)  # 모래놀이를 즐김 — 관찰(티어 3)
BLOCKS = UUID(int=12)  # 블록 — 확정 관심(티어 1)
WATER = UUID(int=13)  # 물놀이를 싫어함 — 확정 기피(티어 1)
BALL = UUID(int=14)  # 공을 싫어함 — 한 글자 라벨


def ranked(uid: UUID, *, label: str, tier: int, polarity: int) -> RankedEvidence:
    kind = "profile_affinity" if tier in (1, 2) else "observation_activity"
    return RankedEvidence(
        ref=Ref(kind=kind, id=uid), tier=tier, label=label, polarity=polarity, observed_on=TODAY
    )


SEEN = {
    SAND: ranked(SAND, label="모래놀이", tier=3, polarity=1),
    BLOCKS: ranked(BLOCKS, label="블록", tier=1, polarity=1),
    WATER: ranked(WATER, label="물놀이", tier=1, polarity=-1),
    BALL: ranked(BALL, label="공", tier=1, polarity=-1),
}


def candidate(**kwargs) -> ActivityCandidate:
    base = dict(
        content="큰 블록으로 탑 쌓기",
        setting="indoor",
        materials=["큰 블록"],
        physical_intensity="low",
        involves_food=False,
        caregiver_role="together",
        reason="블록을 오래 가지고 놀았어요 오늘 오후가 비어 있어요",
        evidence=[{"id": str(BLOCKS), "note": "블록 쌓기를 즐겨 했어요"}],
    )
    return ActivityCandidate.model_validate({**base, **kwargs})


PARK = "사직어린이공원"  # 이번 run 에 장소 조회가 돌려준 곳
PARK_ROW = PlaceRow(
    name=PARK,
    category=PlaceCategory.PARK,
    distance_m=800,
    source="city_park",
    data_date=date(2026, 6, 30),
)


def daily(starts_on: date, ends_on: date) -> EventSchedule:
    """기간 안 매일 열리는 일정 — 일정 원문에 요일 · 날짜 제한이 없는 행사."""
    days = schedule_days(TODAY)
    return EventSchedule(open_days=frozenset(day for day in days if starts_on <= day <= ends_on))


FEST = "광안리 가을 축제"  # 이번 run 에 행사 조회가 돌려준 행사. TODAY(09-29 화) 부터 10-01 까지
FEST_ROW = EventRow(
    name=FEST,
    kind=EventKind.FESTIVAL,
    starts_on=date(2026, 9, 29),
    ends_on=date(2026, 10, 1),
    place="광안리해수욕장 일원",
    distance_m=3_000,
    schedule=daily(date(2026, 9, 29), date(2026, 10, 1)),
)


TOWER_DOC = ActivityDocRow(
    id=UUID(int=21),
    doc_key="act-test-tower",
    title="종이컵 탑 쌓기",
    body="종이컵을 쌓고 무너뜨린다",
    min_month=24,
    max_month=48,
    setting=ActivitySetting.INDOOR,
    materials=("종이컵",),
    caregiver_role=CaregiverRole.TOGETHER,
    physical_intensity=Intensity.LOW,
    involves_food=False,
)
DOCS = {TOWER_DOC.id: TOWER_DOC}  # 이번 run 의 프롬프트 [예시]에 실은 행
# 일반 추천은 문서 행이 근거다 (루트 §2). 기록 없이 내는 후보는 이 인용을 단다
DOC_PICK = {"id": str(TOWER_DOC.id), "note": "종이컵 탑 쌓기 예시를 참고했어요"}


def review(
    *candidates,
    months=40,
    seen=SEEN,
    recent=(),
    places=None,
    events=None,
    safety=(),
    outdoor=None,
    outdoor_days=None,
    docs=DOCS,
    area_places=(),
    requested=False,
):
    return review_candidates(
        candidates,
        months=months,
        seen=seen,
        recent_activities=recent,
        places={PARK: PARK_ROW} if places is None else places,
        events={FEST: FEST_ROW} if events is None else events,
        safety=safety_terms(safety),
        outdoor=outdoor,
        outdoor_days=outdoor_days,
        docs=docs,
        area_places=area_places,
        today=TODAY,
        requested=requested,
    )


def reason_of(result) -> RejectReason | None:
    return result.rejections[0].reason if result.rejections else None


class TestPass:
    def test_근거가_있으면_개인화_추천이_된다(self):
        result = review(candidate())
        assert result.rejections == ()
        (draft,) = result.drafts
        assert draft.kind == "personalized"
        (citation,) = draft.citations
        assert citation.ref.id == BLOCKS
        assert citation.label == "블록"  # 모델이 아니라 조회 결과에서 채운다
        assert citation.note == "블록 쌓기를 즐겨 했어요"

    def test_문서_행만_인용하면_일반_추천이고_이유를_코드_문구로_덮는다(self):
        (draft,) = review(candidate(evidence=[DOC_PICK])).drafts
        assert draft.kind == "general"
        assert draft.reason == GENERAL_REASON
        assert "기록이 아직 적어서" not in draft.reason

    def test_인용이_하나도_없으면_거절한다(self):
        """일반 추천의 근거는 문서 행이다. 아이 기록도 문서도 없는 후보는 나가지 않는다."""
        assert reason_of(review(candidate(evidence=[]))) is RejectReason.BUILD

    def test_6개월_지난_확정_관심_하나만_인용하면_일반_추천이고_그_인용은_빠진다(self):
        """새로 생기는 동작이다. Curator 의 archived 는 새 관찰이 붙을 때만 갱신돼서, 몇 달
        언급이 없던 관심이 confirmed 로 남아 단독 근거로 나갈 수 있었다 (#329)."""
        old = {**SEEN, BLOCKS: replace(SEEN[BLOCKS], observed_on=date(2026, 3, 1))}

        blocks = {"id": str(BLOCKS), "note": "블록 쌓기를 즐겨 했어요"}
        (draft,) = review(candidate(evidence=[blocks, DOC_PICK]), seen=old).drafts

        assert draft.kind == "general"
        assert draft.reason == GENERAL_REASON
        assert [c.ref.kind for c in draft.citations] == ["activity_doc"]  # 아이 기록 인용은 빠진다

    def test_모델이_쓴_reason_이_그대로_추천의_reason_이다(self):
        """#329 완료 기준. 두 칸(why_this · why_now)을 합치던 단계는 #359 에서 없어졌다 —
        여기서는 고정만 한다. 앞뒤 공백만 뺀다."""
        (draft,) = review(candidate(reason="  블록을 오래 가지고 놀았어요  ")).drafts

        assert draft.kind == "personalized"
        assert draft.reason == "블록을 오래 가지고 놀았어요"


class TestEvaluative:
    @pytest.mark.parametrize("field", ["content", "reason"])
    def test_평가_표현이_있으면_거절(self, field):
        result = review(candidate(**{field: "또래보다 잘하는 블록 쌓기"}))
        assert reason_of(result) is RejectReason.EVALUATIVE

    def test_note_도_본다(self):
        pick = {"id": str(BLOCKS), "note": "또래보다 블록을 잘 쌓았어요"}
        assert reason_of(review(candidate(evidence=[pick]))) is RejectReason.EVALUATIVE

    def test_평가_표현이_먼저다(self):
        """순서가 결과를 바꾼다 — 근거 id 도 틀렸지만 앞 검사 사유가 나간다."""
        result = review(candidate(reason="재능이 보여요", evidence=[{"id": "x", "note": "n"}]))
        assert reason_of(result) is RejectReason.EVALUATIVE


class TestEvidence:
    @pytest.mark.parametrize("raw", ["not-a-uuid", str(UUID(int=999))])
    def test_조회_결과에_없는_id_는_거절(self, raw):
        """지어낸 근거로 개인화인 척하지 못한다."""
        result = review(candidate(evidence=[{"id": raw, "note": "모래놀이를 했어요"}]))
        assert reason_of(result) is RejectReason.UNKNOWN_EVIDENCE


class TestRequested:
    """보호자가 놀이를 정해 물었으면("물놀이 뭐 하지?") 그 요청에 맞춰 만든다. 아이 기록이 없으면
    일반 추천이고, 문서 행 근거가 없어도 된다 — 기준이 이번 요청이다."""

    def test_기록이_없으면_문서_근거_없이_일반_추천이고_요청_문구를_단다(self):
        result = review(candidate(content="욕조에서 컵으로 물 옮기기", evidence=[]), requested=True)
        (draft,) = result.drafts
        assert draft.kind == "general"
        assert draft.reason == REQUESTED_REASON
        assert "기록이 아직 적어서" not in draft.reason
        assert draft.citations == ()

    def test_예시를_참고했으면_그_문서도_근거로_남는다(self):
        (draft,) = review(candidate(evidence=[DOC_PICK]), requested=True).drafts
        assert draft.kind == "general"
        assert draft.reason == REQUESTED_REASON
        assert [c.ref.id for c in draft.citations] == [TOWER_DOC.id]

    def test_기록이_있으면_요청이어도_개인화다(self):
        (draft,) = review(candidate(), requested=True).drafts
        assert draft.kind == "personalized"

    def test_막연한_요청은_지금처럼_문서_근거가_있어야_한다(self):
        assert reason_of(review(candidate(evidence=[]), requested=False)) is RejectReason.BUILD

    def test_요청_기반도_안전_필터는_그대로다(self):
        result = review(
            candidate(content="밀가루 반죽 주무르기", materials=["밀가루"], evidence=[]),
            requested=True,
            safety=[SafetyEntry(kind="allergy", label="밀", status="active")],
        )
        assert result.drafts == ()
        assert len(result.removed) == 1


class TestDocEvidence:
    """일반 추천은 문서 행을 근거로 단다. 아이 기록이 아니라 개인화로 세지 않는다 (루트 §2)."""

    def doc_pick(self, note="종이컵 탑 쌓기 예시를 참고했어요"):
        return {"id": str(TOWER_DOC.id), "note": note}

    def test_문서_행만_달면_일반_추천이고_근거로_남는다(self):
        result = review(candidate(content="종이컵으로 높은 탑 쌓기", evidence=[self.doc_pick()]))
        draft = result.drafts[0]
        assert draft.kind == "general"
        assert draft.reason == GENERAL_REASON
        assert [(c.ref.kind, c.ref.id, c.label) for c in draft.citations] == [
            ("activity_doc", TOWER_DOC.id, "종이컵 탑 쌓기")
        ]

    def test_기억과_같이_달면_개인화이고_둘_다_근거다(self):
        memory = {"id": str(BLOCKS), "note": "블록 쌓기를 즐겨 했어요"}
        result = review(candidate(evidence=[memory, self.doc_pick()]))
        draft = result.drafts[0]
        assert draft.kind == "personalized"
        assert [c.ref.kind for c in draft.citations] == ["profile_affinity", "activity_doc"]

    def test_예시에_싣지_않은_문서_id_는_거절(self):
        result = review(candidate(evidence=[self.doc_pick()]), docs={})
        assert reason_of(result) is RejectReason.UNKNOWN_EVIDENCE

    def test_문서_근거_note_에_아이에_대한_반복_표현은_거절(self):
        """ "늘 좋아하는" 은 문서가 아니라 아이에 대한 말이다. 기록 없이 성향을 말하지 않는다."""
        result = review(candidate(evidence=[self.doc_pick("아이가 늘 좋아하는 놀이예요")]))
        assert reason_of(result) is RejectReason.OVERSTATED_NOTE


class TestAvoided:
    def test_싫어한다고_나온_활동은_다시_내지_않는다(self):
        """인용하지 않았어도 조회된 기피면 막는다."""
        result = review(candidate(content="아파트 물놀이터 가기", evidence=[]))
        assert reason_of(result) is RejectReason.AVOIDED

    def test_기피_대조는_활동명이_아니라_subject_로_한다(self):
        """관찰의 활동명("물감 놀이")은 후보에 그대로 나오는 일이 드물다.
        subject("물감")로 막는다 (#304 리뷰)."""
        paint = UUID(int=15)
        seen = {
            **SEEN,
            paint: RankedEvidence(
                ref=Ref(kind="observation_activity", id=paint),
                tier=3,
                label="물감 놀이",
                subject="물감",
                polarity=-1,
                observed_on=TODAY,
            ),
        }
        for content in ("손가락 물감 찍기", "물감으로 그림 그리기"):
            result = review(candidate(content=content, evidence=[]), seen=seen)
            assert reason_of(result) is RejectReason.AVOIDED

    def test_기피는_근거로는_쓸_수_있다(self):
        """ "물놀이를 싫어해서 모래놀이를 골랐어요" 가 되어야 한다 (D4)."""
        pick = {"id": str(WATER), "note": "물놀이를 할 때 울었어요"}
        result = review(
            candidate(
                content="놀이터에서 모래성 쌓기",
                reason="물놀이를 싫어해서 모래로 노는 활동을 골랐어요",
                evidence=[pick],
            )
        )
        assert result.rejections == ()
        assert result.drafts[0].citations[0].polarity == -1

    def test_기피를_인용했는데_이유에_안_밝히면_거절(self):
        pick = {"id": str(WATER), "note": "물놀이를 할 때 울었어요"}
        result = review(candidate(content="놀이터에서 모래성 쌓기", evidence=[pick]))
        assert reason_of(result) is RejectReason.BUILD

    def test_한_글자_기피_라벨로는_거절하지_않는다(self):
        """ "공" 을 싫어한다고 "공원" · "공룡" 까지 막으면 과차단이다."""
        assert MIN_AVOIDED_LABEL == 2
        result = review(candidate(content="공원에서 공룡 그림 그리기", evidence=[DOC_PICK]))
        assert result.rejections == ()


class TestOverstatedNote:
    @pytest.mark.parametrize(
        "note", ["어제도 모래놀이를 했어요", "늘 모래를 만져요", "모래놀이를 좋아하는 아이"]
    )
    def test_관찰_근거에_성향_표현을_쓰면_거절(self, note):
        """한 번의 관찰을 성향처럼 말하지 않는다 (루트 §2)."""
        pick = {"id": str(SAND), "note": note}
        result = review(candidate(content="모래성 쌓기", evidence=[pick]))
        assert reason_of(result) is RejectReason.OVERSTATED_NOTE

    def test_확정_관심에는_쓸_수_있다(self):
        pick = {"id": str(BLOCKS), "note": "블록 쌓기를 늘 즐겨요"}
        assert review(candidate(evidence=[pick])).rejections == ()

    @pytest.mark.parametrize(
        "note", ["오늘 모래놀이를 한 시간 했어요", "또래 친구와 모래놀이를 했어요"]
    )
    def test_같은_글자가_든_낱말은_걸리지_않는다(self, note):
        assert overstates(note) is False


class TestRecentDuplicate:
    def test_최근에_한_활동은_거절(self):
        result = review(candidate(content="큰 블록으로 탑 쌓기!"), recent=["큰 블록으로 탑쌓기"])
        assert reason_of(result) is RejectReason.RECENT_DUPLICATE

    @pytest.mark.parametrize(
        ("a", "b", "dup"),
        [
            ("블록 쌓기", "블록쌓기", True),
            ("색종이 접기", "색종이접기", True),
            ("레고 조립", "레고로 자동차 만들기", False),
            ("물놀이", "물감놀이", False),
        ],
    )
    def test_정규화_후_완전_일치만_중복이다(self, a, b, dup):
        assert is_recent_duplicate(a, [b]) is dup

    def test_조사를_떼지_않는다(self):
        assert normalize_activity("색종이 접기") == "색종이접기"


class TestCaregiverRole:
    @pytest.mark.parametrize(("months", "rejected"), [(17, True), (18, False)])
    def test_17개월까지는_보호자가_같이_하는_활동만(self, months, rejected):
        result = review(candidate(caregiver_role="nearby"), months=months)
        assert (reason_of(result) is RejectReason.CAREGIVER_ROLE) is rejected


class TestExplain:
    def test_모델에게는_번호와_고칠_방향만_간다(self):
        result = review(
            candidate(),
            candidate(content="아파트 물놀이터 가기", evidence=[]),
            candidate(evidence=[{"id": "x", "note": "지어낸 근거"}]),
        )
        text = explain(result.rejections)
        assert text.startswith("2번 후보")
        assert "3번 후보" in text
        assert "물놀이터" not in text
        assert "지어낸 근거" not in text


class TestPlace:
    def test_조회한_장소는_코드가_문장_앞에_붙인다(self):
        (draft,) = review(candidate(content="모래성 쌓기", place_name=PARK)).drafts
        assert draft.content == "사직어린이공원에서 모래성 쌓기"

    @pytest.mark.parametrize("places", [{PARK: PARK_ROW}, {}])
    def test_조회_결과에_없는_장소는_거절(self, places):
        """장소를 조회하지 않았거나 결과에 없는 이름이면 지어낸 것으로 본다."""
        result = review(candidate(place_name="해운대 키즈파크"), places=places)
        assert reason_of(result) is RejectReason.UNKNOWN_PLACE
        assert "해운대" not in explain(result.rejections)  # 장소 이름은 로그로 흘러가지 않는다

    @pytest.mark.parametrize(
        "text",
        [
            {"reason": "근처 공원에서 모래를 만져요 오늘 오후가 비어 있어요"},
            {"content": "근처 공원에서 모래성 쌓기"},
        ],
    )
    def test_말한_지역에서_찾은_장소에는_근처를_쓰지_않는다(self, text):
        """보호자는 그 지역에 있지 않다. 대표 지점에서 찾아서 "그 구 안" 도 단정하지 않는다."""
        near = candidate(**{"content": "모래성 쌓기", "place_name": PARK, **text})
        assert reason_of(review(near, area_places={PARK})) is RejectReason.NEARBY_WORDING
        # 보호자 위치에서 찾은 곳이면 맞는 말이다
        assert reason_of(review(near)) is None

    def test_띄어쓰기만_다르면_같은_곳이고_조회_결과의_이름을_붙인다(self):
        (draft,) = review(candidate(content="모래성 쌓기", place_name="사직 어린이공원")).drafts
        assert draft.content == "사직어린이공원에서 모래성 쌓기"

    def test_문장에_이미_있으면_다시_붙이지_않는다(self):
        content = "사직어린이공원 모래놀이터에서 모래성 쌓기"
        (draft,) = review(candidate(content=content, place_name=PARK)).drafts
        assert draft.content == content

    @pytest.mark.parametrize("content", ["공원에서 모래성 쌓기", "어린이공원에서 모래성 쌓기"])
    def test_장소_이름이_그_낱말로_끝나면_그_자리를_장소_이름으로_바꾼다(self, content):
        """앞에 붙이기만 하면 "사직어린이공원에서 공원에서 …" 가 된다 (10-28 QA)."""
        (draft,) = review(candidate(content=content, place_name=PARK)).drafts
        assert draft.content == "사직어린이공원에서 모래성 쌓기"

    @pytest.mark.parametrize(
        "content, expected",
        [
            ("놀이터에서 모래성 쌓기", "사직어린이공원 놀이터에서 모래성 쌓기"),
            ("모래밭에서 모래성 쌓기", "사직어린이공원 모래밭에서 모래성 쌓기"),
        ],
    )
    def test_어디서를_먼저_썼으면_그_앞에_장소_이름만_붙인다(self, content, expected):
        """ "~에서" 가 두 번 붙지 않게 — 공원 안의 놀이터 · 모래밭이다."""
        (draft,) = review(candidate(content=content, place_name=PARK)).drafts
        assert draft.content == expected

    @pytest.mark.parametrize("place_name", [None, "", "  "])
    def test_장소가_없으면_문장을_그대로_둔다(self, place_name):
        (draft,) = review(candidate(place_name=place_name)).drafts
        assert draft.content == "큰 블록으로 탑 쌓기"
        assert draft.place_notice is None

    def test_장소가_붙으면_기준일과_확인_문구를_칸으로_싣는다(self):
        """문장에 섞지 않는다 — 화면이 제안 문장 아래 한 줄로 세운다 (#516)."""
        (draft,) = review(candidate(content="모래성 쌓기", place_name=PARK)).drafts
        assert draft.content == "사직어린이공원에서 모래성 쌓기"
        assert draft.place_notice == (
            "공공데이터 기준 2026년 6월이에요. 가기 전에 운영 여부를 확인해 주세요."
        )
        assert draft.to_payload()["place_notice"] == draft.place_notice

    def test_놀이시설은_이용료도_확인해_달라고_한다(self):
        cafe = PlaceRow(
            name="시험키즈카페",
            category=PlaceCategory.INDOOR_PLAYGROUND,
            distance_m=900,
            source="kids_play_facility",
            data_date=None,
        )
        (draft,) = review(
            candidate(content="볼풀에서 공 던지기", place_name="시험키즈카페"),
            places={"시험키즈카페": cafe},
        ).drafts
        # 출처가 안 보이면 왜 이 가게가 나왔는지 몰라 광고처럼 읽힌다 — 날짜 없이 출처만 (#600 PM)
        assert draft.place_notice == (
            "공공데이터에 있는 시설이에요. 가기 전에 운영 여부와 이용료를 확인해 주세요."
        )

    def test_기준일자가_없는_장소는_운영_여부_확인만(self):
        undated = replace(PARK_ROW, data_date=None)
        (draft,) = review(
            candidate(content="모래성 쌓기", place_name=PARK), places={PARK: undated}
        ).drafts
        assert draft.place_notice == "가기 전에 운영 여부를 확인해 주세요."


class TestEvent:
    """행사는 장소처럼 place_name 으로 붙는다. 그날 열려야 하고, 바깥 나들이로 본다."""

    def test_조회한_행사는_이름을_문장_앞에_붙이고_확인_문구를_칸으로_싣는다(self):
        (draft,) = review(candidate(content="모래 조각 구경하기", place_name=FEST)).drafts
        assert draft.content == "광안리 가을 축제에서 모래 조각 구경하기"
        assert draft.place_notice == (
            "한국관광공사 행사 정보예요. 일정과 관람 연령이 바뀔 수 있으니 가기 전에 확인해 주세요."
        )

    def test_행사는_근거에_넣지_않는다(self):
        (draft,) = review(candidate(content="모래 조각 구경하기", place_name=FEST)).drafts
        assert [c.ref.id for c in draft.citations] == [BLOCKS]

    @pytest.mark.parametrize("day", [DayLabel.TOMORROW, DayLabel.THIS_WEEKEND])
    def test_그날_열리지_않는_행사는_거절(self, day):
        """10-01 에 끝나서 주말(10-03 · 04)에는 없다. 내일(09-30)은 열린다."""
        result = review(candidate(content="축제 구경", place_name=FEST, day=day))
        expected = None if day == DayLabel.TOMORROW else RejectReason.EVENT_CLOSED
        assert reason_of(result) is expected
        assert "광안리" not in explain(result.rejections)

    def test_행사는_바깥_나들이라_바깥이_어려운_날이면_거절(self):
        """실내 공연이어도 오가는 길과 줄이 바깥이다 — setting 이 indoor 여도 막는다."""
        indoor = candidate(content="공연 보기", setting="indoor", place_name=FEST)
        result = review(indoor, outdoor={DayLabel.TODAY: False})
        assert reason_of(result) is RejectReason.OUTDOOR_CLOSED

    @pytest.mark.parametrize(
        ("ends_on", "saturday_ok", "rejected"),
        [
            (date(2026, 10, 3), False, True),  # 토요일에만 열리고 토요일은 비 · 일요일은 맑음
            (date(2026, 10, 3), True, False),
            (date(2026, 10, 4), False, False),  # 일요일에도 열리고 일요일이 맑다
        ],
    )
    def test_주말은_행사가_열리는_날과_바깥이_되는_날이_같아야_한다(
        self, ends_on, saturday_ok, rejected
    ):
        """주말은 하루라도 맑으면 열리지만 행사는 그 맑은 날에 열려야 한다 (#668 리뷰)."""
        weekend = replace(
            FEST_ROW,
            starts_on=date(2026, 10, 3),
            ends_on=ends_on,
            schedule=daily(date(2026, 10, 3), ends_on),
        )
        result = review(
            candidate(content="축제 구경", place_name=FEST, day=DayLabel.THIS_WEEKEND),
            events={FEST: weekend},
            outdoor={DayLabel.THIS_WEEKEND: True},
            outdoor_days={date(2026, 10, 3): saturday_ok, date(2026, 10, 4): True},
        )
        assert reason_of(result) is (RejectReason.OUTDOOR_CLOSED if rejected else None)

    @pytest.mark.parametrize(
        ("day", "closed"), [(DayLabel.TODAY, True), (DayLabel.TOMORROW, False)]
    )
    def test_기간_안이어도_일정에_없는_날이면_거절(self, day, closed):
        """수요일(09-30)에만 여는 행사 — 화요일(오늘)은 기간 안이어도 닫혀 있다."""
        wednesday = replace(
            FEST_ROW, schedule=EventSchedule(open_days=frozenset({date(2026, 9, 30)}))
        )
        result = review(
            candidate(content="축제 구경", place_name=FEST, day=day), events={FEST: wednesday}
        )
        assert reason_of(result) is (RejectReason.EVENT_CLOSED if closed else None)

    def test_말한_지역에서_찾은_행사에는_근처를_쓰지_않는다(self):
        near = candidate(content="근처 축제 구경", place_name=FEST)
        assert reason_of(review(near, area_places={FEST})) is RejectReason.NEARBY_WORDING

    def test_장소_이름과_같으면_장소로_본다(self):
        """조회한 장소와 행사 이름이 겹치면 장소 문구가 붙는다 — 상설 시설이 먼저다."""
        same = replace(FEST_ROW, name=PARK)
        (draft,) = review(
            candidate(content="모래성 쌓기", place_name=PARK), events={PARK: same}
        ).drafts
        assert draft.place_notice.startswith("공공데이터 기준")
        assert draft.event_starts_on is None and draft.event_ends_on is None

    def test_행사는_기간을_칸으로_싣는다(self):
        """화면이 확인 문구 줄에 "9월 29일 ~ 10월 1일" 로 세운다 — 날짜 없이 이름만 나가면 지난
        행사인지 알 수 없다 (#516 FE 합의). 문장 · 확인 문구에는 섞지 않는다."""
        (draft,) = review(candidate(content="축제 구경", place_name=FEST)).drafts
        assert (draft.event_starts_on, draft.event_ends_on) == (
            date(2026, 9, 29),
            date(2026, 10, 1),
        )
        payload = draft.to_payload()
        assert (payload["event_starts_on"], payload["event_ends_on"]) == (
            "2026-09-29",
            "2026-10-01",
        )

    def test_장소_추천에는_기간이_없다(self):
        (draft,) = review(candidate(content="모래성 쌓기", place_name=PARK)).drafts
        payload = draft.to_payload()
        assert (payload["event_starts_on"], payload["event_ends_on"]) == (None, None)


EVENT_NOTICE = (
    "한국관광공사 행사 정보예요. 일정과 관람 연령이 바뀔 수 있으니 가기 전에 확인해 주세요."
)
EXPO = "2026 대한민국 정원문화박람회"  # 모델은 "정원문화박람회" 로 줄여 쓴다
EXPO_ROW = replace(FEST_ROW, name=EXPO, place="바람숲공원 일원")
TOUR = "골목 작품투어 (시민예술프로젝트)"  # 괄호를 떼고 "골목 작품투어" 로 쓴다
TOUR_ROW = replace(FEST_ROW, name=TOUR, place="예술공원 파빌리온")
EVENTS = {FEST: FEST_ROW, EXPO: EXPO_ROW, TOUR: TOUR_ROW}
# 문장에 "전통시장 박람회" · "드론 라이트쇼" 처럼 이름의 뒤쪽만 쓴다
MARKET_ROW = replace(FEST_ROW, name="2026 이웃 전통시장 박람회", place="바람숲공원 다솜광장")
DRONE_ROW = replace(FEST_ROW, name="모래섬 M(Magic) 드론 라이트쇼", place="모래섬 해변 일원")


class TestEventName:
    """모델이 행사를 가리키는 방식 그대로 붙인다 — 행사 이름이 문장에 나가면 반드시 붙어서 검증된다.

    11-03 실측(실제 TourAPI · 실제 모델): 첫 시도의 place_name 은 대부분 개최 장소였고, 거절되면
    place_name 을 비우고 문장에 줄인 행사 이름만 남겨 검증 없이 나갔다.
    """

    def test_개최_장소를_써도_그_행사로_붙는다(self):
        (draft,) = review(
            candidate(content="꽃 모양 찾기", place_name="바람숲공원 일원"), events=EVENTS
        ).drafts
        assert draft.content == "2026 대한민국 정원문화박람회에서 꽃 모양 찾기"
        assert draft.place_notice == EVENT_NOTICE

    def test_같은_곳에서_열리는_행사가_여럿이면_문장의_행사_이름으로_고른다(self):
        twin = replace(TOUR_ROW, name="골목 인형극", place=EXPO_ROW.place)
        events = {EXPO: EXPO_ROW, twin.name: twin}
        unnamed = candidate(content="꽃 모양 찾기", place_name="바람숲공원 일원")
        assert reason_of(review(unnamed, events=events)) is RejectReason.UNKNOWN_PLACE
        named = candidate(content="골목 인형극 보기", place_name="바람숲공원 일원")
        (draft,) = review(named, events=events).drafts
        assert draft.place_notice == EVENT_NOTICE

    def test_개최_장소를_여럿_적은_행사는_그중_한_곳만_써도_문장의_행사로_붙는다(self):
        """TourAPI 개최 장소는 "○○해수욕장, △△, □□ 일원" 처럼 여럿일 때가 있고, 모델은 그중 한 곳을
        쓴다 — 거절하면 한 바퀴를 더 돈다 (11-03 실측)."""
        dance = replace(
            FEST_ROW, name="달빛 체조 챌린지", place="시민공원 다솜광장 / 북항공원 잔디광장"
        )
        (draft,) = review(
            candidate(content="달빛 체조 챌린지 구경하기", place_name="북항공원 잔디광장"),
            events={dance.name: dance},
        ).drafts
        assert draft.place_notice == EVENT_NOTICE

    def test_개최_장소의_한_곳만_쓰고_문장에_행사가_없으면_붙이지_않는다(self):
        """그곳에서 하는 다른 활동(잔디밭 공놀이)일 수 있다 — 행사 문구가 잘못 붙는다."""
        dance = replace(
            FEST_ROW, name="달빛 체조 챌린지", place="시민공원 다솜광장 / 북항공원 잔디광장"
        )
        result = review(
            candidate(content="잔디밭에서 공 굴리기", place_name="북항공원 잔디광장"),
            events={dance.name: dance},
        )
        assert reason_of(result) is RejectReason.UNKNOWN_PLACE

    @pytest.mark.parametrize("place_name", ["대한민국 정원문화박람회", "정원문화박람회"])
    def test_줄여_쓴_행사_이름으로도_붙는다(self, place_name):
        """연도를 뗀 이름, 그 안의 긴 낱말(6자 이상)로도 알아본다."""
        (draft,) = review(
            candidate(content="꽃 모양 찾기", place_name=place_name), events=EVENTS
        ).drafts
        assert draft.place_notice == EVENT_NOTICE

    @pytest.mark.parametrize(
        ("content", "place_name"),
        [
            ("골목 작품투어에서 작품 찾아보기", TOUR),
            ("정원문화박람회에서 꽃 모양 찾기", "바람숲공원 일원"),
        ],
    )
    def test_줄여_쓴_이름이_문장에_있으면_긴_이름을_다시_붙이지_않는다(self, content, place_name):
        """ "골목 작품투어 (시민예술프로젝트)에서 골목 작품투어에서 …" 가 되지 않게 (11-03 실측)."""
        (draft,) = review(candidate(content=content, place_name=place_name), events=EVENTS).drafts
        assert draft.content == content

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("귤 축제에서 본 색 찾기", "바닷가 귤 축제에서 본 색 찾기"),
            ("축제에서 본 색 찾기", "바닷가 귤 축제에서 본 색 찾기"),
            ("귤 따기 체험하기", "바닷가 귤 축제에서 귤 따기 체험하기"),
        ],
    )
    def test_행사_이름의_뒷부분으로_시작하면_그_자리를_행사_이름으로_바꾼다(
        self, content, expected
    ):
        """앞에 붙이기만 하면 "바닷가 귤 축제에서 귤 축제에서 …" 가 된다 (11-03 실측)."""
        tangerine = replace(FEST_ROW, name="바닷가 귤 축제")
        (draft,) = review(
            candidate(content=content, place_name=tangerine.name),
            events={tangerine.name: tangerine},
        ).drafts
        assert draft.content == expected

    @pytest.mark.parametrize(
        ("event", "content", "expected"),
        [
            (
                MARKET_ROW,
                "바람숲공원에서 전통시장 박람회 구경하기",
                "바람숲공원에서 2026 이웃 전통시장 박람회 구경하기",
            ),
            (
                DRONE_ROW,
                "모래섬에서 드론 라이트쇼와 바다 풍경 보기",
                "모래섬에서 모래섬 M(Magic) 드론 라이트쇼와 바다 풍경 보기",
            ),
            (
                DRONE_ROW,
                "모래섬에서 드론라이트쇼 보기",
                "모래섬에서 모래섬 M(Magic) 드론 라이트쇼 보기",
            ),
        ],
    )
    def test_행사_이름의_뒤쪽_두_낱말_이상을_문장에_썼으면_그_자리를_행사_이름으로_바꾼다(
        self, event, content, expected
    ):
        """앞에 붙이면 "○○ 전통시장 박람회 바람숲공원에서 전통시장 박람회 …" 가 된다
        (11-03 실측, 행사를 묻지 않은 나들이 요청)."""
        (draft,) = review(
            candidate(content=content, place_name=event.place), events={event.name: event}
        ).drafts
        assert draft.content == expected

    def test_줄인_이름이_다른_낱말의_일부면_바꾸지_않는다(self):
        """ "늦가을 축제" 의 "가을 축제" 를 바꾸면 "늦광안리 가을 축제" 가 된다."""
        (draft,) = review(
            candidate(content="늦가을 축제 분위기 느끼기", place_name=FEST), events=EVENTS
        ).drafts
        assert draft.content == "광안리 가을 축제에서 늦가을 축제 분위기 느끼기"

    def test_place_name_을_비워도_문장에_행사가_있으면_붙인다(self):
        (draft,) = review(
            candidate(content="정원문화박람회에서 꽃 모양 찾기", place_name=None), events=EVENTS
        ).drafts
        assert draft.place_notice == EVENT_NOTICE
        assert draft.event_starts_on == EXPO_ROW.starts_on
        assert draft.event_ends_on == EXPO_ROW.ends_on

    @pytest.mark.parametrize(
        ("day", "outdoor", "expected"),
        [
            (DayLabel.THIS_WEEKEND, None, RejectReason.EVENT_CLOSED),  # 10-01 에 끝난다
            (DayLabel.TODAY, {DayLabel.TODAY: False}, RejectReason.OUTDOOR_CLOSED),
        ],
    )
    def test_문장에만_쓴_행사도_그날_열리는지_바깥이_되는지_본다(self, day, outdoor, expected):
        """거절된 뒤 place_name 만 비우고 행사를 문장에 남겨도 검증을 빠져나가지 못한다."""
        only_text = candidate(content="정원문화박람회 구경하기", setting="indoor", day=day)
        assert reason_of(review(only_text, events=EVENTS, outdoor=outdoor)) is expected

    @pytest.mark.parametrize("content", ["크리스마스 카드 만들기", "놀이터에서 미끄럼틀 타기"])
    def test_행사_이름의_짧은_낱말은_행사로_보지_않는다(self, content):
        """흔한 말이라 행사가 아닌 후보에 행사가 붙는다. 6자보다 짧은 낱말은 이름 전체로만 본다."""
        tree = replace(FEST_ROW, name="해운대 크리스마스 놀이터")
        (draft,) = review(candidate(content=content), events={tree.name: tree}).drafts
        assert draft.place_notice is None

    @pytest.mark.parametrize(
        ("content", "place_name"),
        [
            ("정원문화박람회에서 꽃 보기", FEST),  # 붙인 행사와 문장의 행사가 다르다
            ("정원문화박람회 보고 골목 작품투어 하기", None),  # 문장에 행사가 둘이다
            ("정원문화박람회 보고 모래성 쌓기", PARK),  # 장소를 붙였는데 다른 곳 행사를 썼다
        ],
    )
    def test_한_후보에_장소나_행사가_둘이면_거절(self, content, place_name):
        """하나만 붙일 수 있다 — 나머지는 검증 없이 나간다."""
        result = review(candidate(content=content, place_name=place_name), events=EVENTS)
        assert reason_of(result) is RejectReason.MIXED_PLACES
        assert "박람회" not in explain(result.rejections)

    def test_장소를_붙였는데_그_장소에서_열리는_행사를_쓰면_행사로_붙는다(self):
        concert = replace(FEST_ROW, name="사직 별빛 음악회", place="사직어린이공원 일원")
        (draft,) = review(
            candidate(content="사직 별빛 음악회 듣기", place_name=PARK),
            events={concert.name: concert},
        ).drafts
        assert draft.place_notice == EVENT_NOTICE

    def test_조회한_장소_이름은_행사를_알아보는_이름으로_쓰지_않는다(self):
        """행사 이름이 장소 이름과 같으면 장소가 먼저다 — 문장에 장소 이름을 써도 장소다."""
        same = replace(FEST_ROW, name=PARK)
        (draft,) = review(
            candidate(content="사직어린이공원에서 모래성 쌓기", place_name=PARK),
            events={PARK: same},
        ).drafts
        assert draft.place_notice.startswith("공공데이터 기준")

    @pytest.mark.parametrize(
        "reason",
        [
            RejectReason.UNKNOWN_PLACE,
            RejectReason.EVENT_CLOSED,
            RejectReason.EVENT_PLACE,
            RejectReason.OUTDOOR_CLOSED,
        ],
    )
    def test_place_name_만_비우라고_안내하지_않는다(self, reason):
        """비울 때는 문장에서도 뺀다 — 비우기만 하면 행사가 문장에 남는다 (11-03 실측)."""
        assert "문장에서도" in GUIDANCE[reason]


# 날마다 장소를 옮겨 가며 여는 행사. 오늘(09-29 화)은 바람숲공원, 토요일(10-03)은 모래섬 해변이다
MOVING = "달빛 체조 교실"
MOVING_ROW = replace(
    FEST_ROW,
    name=MOVING,
    place="바람숲공원 잔디광장 / 모래섬 해변",
    schedule=EventSchedule(
        open_days=frozenset({date(2026, 9, 29), date(2026, 10, 3)}),
        day_places=((date(2026, 9, 29), "바람숲공원 잔디광장"), (date(2026, 10, 3), "모래섬 해변")),
    ),
)


class TestEventPlaceOfDay:
    """한 행사가 날마다 다른 곳에서 열린다 — 그날이 아닌 날의 장소를 쓰면 보호자가 엉뚱한 곳에
    간다."""

    @pytest.mark.parametrize(
        ("content", "day", "expected"),
        [
            ("모래섬 해변에서 달빛 체조 교실 함께하기", DayLabel.TODAY, RejectReason.EVENT_PLACE),
            ("모래섬에서 달빛 체조 교실 함께하기", DayLabel.TODAY, RejectReason.EVENT_PLACE),
            ("모래섬 해변에서 달빛 체조 교실 함께하기", DayLabel.THIS_WEEKEND, None),
            ("바람숲공원에서 달빛 체조 교실 함께하기", DayLabel.TODAY, None),
        ],
    )
    def test_그날이_아닌_날의_장소를_쓰면_거절(self, content, day, expected):
        result = review(
            candidate(content=content, setting="outdoor", place_name=MOVING, day=day),
            events={MOVING: MOVING_ROW},
        )
        assert reason_of(result) is expected
        assert "모래섬" not in explain(result.rejections)

    def test_그날_장소를_place_name_에_쓰면_그_행사로_붙는다(self):
        (draft,) = review(
            candidate(
                content="체조 따라 하기",
                setting="outdoor",
                place_name="모래섬 해변",
                day=DayLabel.THIS_WEEKEND,
            ),
            events={MOVING: MOVING_ROW},
        ).drafts
        assert draft.place_notice == EVENT_NOTICE

    def test_다른_날_장소를_place_name_에_쓰면_그날_장소가_아니라고_거절(self):
        result = review(
            candidate(
                content="체조 따라 하기",
                setting="outdoor",
                place_name="바람숲공원 잔디광장",
                day=DayLabel.THIS_WEEKEND,
            ),
            events={MOVING: MOVING_ROW},
        )
        assert reason_of(result) is RejectReason.EVENT_PLACE


class TestSafety:
    """안전 필터가 맨 앞이다. 걸린 후보는 거절이 아니라 제거 — 모델에게 사유가 가지 않는다."""

    def test_걸린_후보는_거절이_아니라_제거된다(self):
        result = review(candidate(), candidate(content="구슬 꿰기", evidence=[]), months=20)
        assert [r.index for r in result.removed] == [1]
        assert result.removed[0].hits == ("small_parts",)
        assert result.rejections == ()
        assert len(result.drafts) == 1

    def test_안전이_평가_표현보다_먼저다(self):
        """위험하면서 평가 표현도 있으면 제거만 된다. 거절 사유로 모델에게 돌아가지 않는다."""
        risky = candidate(content="또래보다 잘하는 구슬 꿰기", evidence=[])
        result = review(risky, months=20)
        assert [r.index for r in result.removed] == [0]
        assert result.rejections == ()

    def test_제거된_후보는_모델_설명에_나오지_않는다(self):
        result = review(
            candidate(content="구슬 꿰기", evidence=[]),
            candidate(content="또래보다 잘하는 블록 놀이", evidence=[]),
            months=20,
        )
        assert explain(result.rejections) == "2번 후보: " + GUIDANCE[RejectReason.EVALUATIVE]

    def test_active_알레르기에_걸린_후보도_제거된다(self):
        entry = SafetyEntry(kind="allergy", label="밀", status="active")
        result = review(candidate(content="밀가루 점토 놀이", evidence=[]), safety=[entry])
        assert result.removed[0].hits == ("health_safety",)

    def test_경고_문구는_그_초안에_싣는다(self):
        """추천 하나에 붙는 문구라 결과 안내(readouts)가 아니라 초안 칸이다 (#402)."""
        result = review(candidate(), candidate(content="구슬 꿰기", evidence=[DOC_PICK]), months=40)
        small_parts = hazard_terms().axes["small_parts"].warning_text
        assert [draft.warnings for draft in result.drafts] == [(), (small_parts,)]
        assert result.drafts[1].to_payload()["warnings"] == [small_parts]
        assert result.drafts[0].to_payload()["warnings"] == []

    def test_allergens_와_items_를_코드가_채운다(self):
        """#232 — 승인 때 알레르기 안내와 일정 준비물에 쓴다. 모델이 고르지 않는다."""
        result = review(
            candidate(content="밀가루 반죽 놀이", materials=["밀가루", "밀대"], evidence=[DOC_PICK])
        )
        (draft,) = result.drafts
        assert draft.allergens == ("밀",)
        assert draft.items == ("밀가루", "밀대")


class TestFoods:
    """음식 칸(foods) — 음식 활동이면 코드가 판정할 음식 이름이 있어야 한다 (#582)."""

    PEANUT = SafetyEntry(kind="allergy", label="땅콩", status="active")

    def food(self, **kwargs) -> ActivityCandidate:
        base = dict(
            content="바나나 잘라 꼬치 만들기",
            involves_food=True,
            foods=["바나나"],
            materials=["꼬치"],
            evidence=[DOC_PICK],
        )
        return candidate(**{**base, **kwargs})

    def test_음식_활동인데_음식_칸이_비면_다시_적게_한다(self):
        result = review(self.food(foods=[]))

        assert reason_of(result) is RejectReason.FOODS_MISSING
        assert "foods" in GUIDANCE[RejectReason.FOODS_MISSING]

    def test_공백만_적은_음식_칸도_빈_칸이다(self):
        assert reason_of(review(self.food(foods=["  "]))) is RejectReason.FOODS_MISSING

    def test_음식_활동이_아니면_음식_칸이_비어도_된다(self):
        assert review(candidate(evidence=[DOC_PICK])).rejections == ()

    def test_음식_칸의_음식도_items_에_싣는다(self):
        """승인 때 일정 준비물로 쓴다 — 재료와 같은 이름은 한 번만."""
        (draft,) = review(
            self.food(materials=["꼬치", "바나나"], foods=["바나나", "요거트"])
        ).drafts

        assert draft.items == ("꼬치", "바나나", "요거트")

    def test_개인화_이유에_막힌_음식_이름이_나오면_다시_쓰게_한다(self):
        """빼는 말("땅콩 없이")이어도 화면에 등록한 알레르기 이름이 나간다 (#575 ⭐3 기준)."""
        personal = self.food(
            reason="블록 놀이 뒤 간식으로 땅콩 없이 바나나만 써요",
            evidence=[{"id": str(BLOCKS), "note": "블록 쌓기를 즐겨 했어요"}],
        )

        result = review(personal, safety=[self.PEANUT])

        assert reason_of(result) is RejectReason.FOOD_MENTION
        assert "땅콩" not in GUIDANCE[RejectReason.FOOD_MENTION]  # 걸린 낱말은 알려 주지 않는다

    def test_일반_추천의_이유는_코드_문구로_덮어서_보지_않는다(self):
        """기록 근거가 없으면 이유가 GENERAL_REASON 으로 바뀐다 — 거절하면 왕복만 는다."""
        general = self.food(reason="땅콩 없이 바나나만 써요", evidence=[DOC_PICK])

        (draft,) = review(general, safety=[self.PEANUT]).drafts

        assert draft.reason == GENERAL_REASON

    def test_알레르기와_겹치지_않는_음식_이름은_이유에_써도_된다(self):
        personal = self.food(
            reason="블록 놀이 뒤 바나나 간식이 잘 맞아요",
            evidence=[{"id": str(BLOCKS), "note": "블록 쌓기를 즐겨 했어요"}],
        )

        assert review(personal, safety=[self.PEANUT]).rejections == ()


class TestOutdoor:
    """그날 날씨 판정이 야외를 막았으면(D8) 야외 후보를 거절한다. 모델이 실내로 고친다 (#377)."""

    RAIN_TODAY = {DayLabel.TODAY: False, DayLabel.TOMORROW: True}

    def test_바깥_활동이_어려운_날_야외_후보는_거절(self):
        result = review(candidate(setting="outdoor"), outdoor=self.RAIN_TODAY)
        assert reason_of(result) is RejectReason.OUTDOOR_CLOSED

    @pytest.mark.parametrize("setting", ["indoor", "either"])
    def test_실내나_어디서든은_통과(self, setting):
        assert review(candidate(setting=setting), outdoor=self.RAIN_TODAY).rejections == ()

    def test_오늘_비가_와도_내일_야외는_통과(self):
        """요청이 내일이면 오늘 날씨로 막지 않는다 (#359 리뷰)."""
        tomorrow = candidate(setting="outdoor", day="tomorrow")
        assert review(tomorrow, outdoor=self.RAIN_TODAY).rejections == ()

    def test_판정하지_않은_날은_맑다고_가정하지_않는다(self):
        weekend = candidate(setting="outdoor", day="this_weekend")
        result = review(weekend, outdoor=self.RAIN_TODAY)
        assert reason_of(result) is RejectReason.OUTDOOR_CLOSED

    def test_either_여도_공원이_붙으면_그날_날씨를_받는다(self):
        """오늘로 찾은 공원을 비 오는 날 either 활동에 붙이면 막는다 (#600 리뷰)."""
        either_park = candidate(content="모래성 쌓기", setting="either", place_name=PARK)
        result = review(either_park, outdoor=self.RAIN_TODAY)
        assert reason_of(result) is RejectReason.OUTDOOR_CLOSED

    def test_실내_놀이시설이_붙은_either_는_통과(self):
        cafe = PlaceRow(
            name="시험키즈카페",
            category=PlaceCategory.INDOOR_PLAYGROUND,
            distance_m=900,
            source="kids_play_facility",
            data_date=None,
        )
        either_cafe = candidate(
            content="볼풀에서 공 던지기", setting="either", place_name="시험키즈카페"
        )
        result = review(either_cafe, outdoor=self.RAIN_TODAY, places={"시험키즈카페": cafe})
        assert result.rejections == ()
        assert result.outdoor == frozenset()

    def test_공원이_붙은_either_는_바깥_활동으로_센다(self):
        """날씨를 묻지 않은 날의 안내("날씨는 확인하지 않았어요")가 이 추천에도 붙는다 (#515)."""
        either_park = candidate(
            content="모래성 쌓기", setting="either", place_name=PARK, day="tomorrow"
        )
        result = review(either_park, outdoor=self.RAIN_TODAY)
        (draft,) = result.drafts
        assert result.outdoor == {(normalize_activity(draft.content), DayLabel.TOMORROW)}


class TestOutputTool:
    async def context(self, *, recent=(), safety=None, outdoor_ok=True):
        memory = InMemoryActivityMemory(
            observations={
                CHILD: [
                    ActivityObservation(
                        id=UUID(int=100 + i),
                        child_id=CHILD,
                        observed_on=TODAY - timedelta(days=days),
                        subject=name,
                        activity=name,
                        polarity=1,
                    )
                    for i, (name, days) in enumerate(recent)
                ]
            }
        )
        ctx = ActivityContext(
            child_id=CHILD,
            run_id="run-1",
            now=NOW,
            timezone=UTC,
            ports=in_memory_ports(CHILD, date(2023, 1, 1), memory=memory, safety=safety),
        )
        ctx.state.gate = await build_gate(ctx, outdoor_ok=outdoor_ok)
        ctx.state.seen_evidence.update(SEEN)
        ctx.state.seen_docs = dict(DOCS)
        return ctx

    def args(self, *candidates):
        return ProposeActivityCandidatesArgs(candidates=list(candidates))

    async def test_세_개가_통과하면_초안을_담는다(self):
        ctx = await self.context()
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[DOC_PICK]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.success is True
        assert result.data["kinds"] == ["personalized", "general", "general"]
        assert len(ctx.state.suggestions) == 3

    @pytest.mark.parametrize(
        "written",
        ["예1", " 예1 ", "[예1]", "예 1", "예시1", "(예1)", "예１"],  # 전각 1
    )
    async def test_예시의_짧은_id_는_문서_행_근거가_된다(self, written):
        """[예시]는 짧은 id 로 보여 준다. 띄어쓰기 · 괄호 · 전각 숫자만 달라도 같은 행이다(#606)."""
        ctx = await self.context()
        ctx.state.example_ids = {"예1": TOWER_DOC.id}
        pick = {"id": written, "note": "종이컵 탑 쌓기 예시를 참고했어요"}
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[pick]),
                candidate(content="종이컵 탑 쌓기", evidence=[pick]),
            ),
        )
        assert result.success is True
        tower = ctx.state.suggestions[2]
        assert tower.kind == "general"
        assert [(c.ref.kind, c.ref.id) for c in tower.citations] == [("activity_doc", TOWER_DOC.id)]

    async def test_없는_짧은_id_는_지어낸_근거로_거절한다(self):
        ctx = await self.context()
        ctx.state.example_ids = {"예1": TOWER_DOC.id}
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[{"id": "예7", "note": "참고"}]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.success is False
        assert result.error["code"] == ErrorCode.EVIDENCE_REQUIRED
        assert "예1 같은" in result.error["message"]  # 짧은 id 를 쓰라고 알려 준다

    async def test_장소_조회가_돌려준_곳을_붙여서_담는다(self):
        ctx = await self.context()
        ctx.state.seen_places[PARK] = PARK_ROW
        await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[DOC_PICK], place_name=PARK),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert ctx.state.suggestions[1].content == "사직어린이공원에서 모래성 쌓기"
        assert ctx.state.suggestions[1].place_notice is not None
        assert ctx.state.suggestions[0].place_notice is None

    async def test_행사_조회가_돌려준_행사를_붙여서_담는다(self):
        ctx = await self.context()
        ctx.state.seen_events[FEST] = FEST_ROW
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래 조각 구경하기", evidence=[DOC_PICK], place_name=FEST),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.success is True
        assert ctx.state.suggestions[1].content == "광안리 가을 축제에서 모래 조각 구경하기"

    async def test_하나라도_걸리면_사유를_돌려주고_아무것도_담지_않는다(self):
        ctx = await self.context()
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="아파트 물놀이터 가기", evidence=[DOC_PICK]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.success is False
        assert result.error["code"] == ErrorCode.CANDIDATE_REJECTED
        assert ctx.state.suggestions == ()

    async def test_지어낸_근거_id_는_EVIDENCE_REQUIRED(self):
        ctx = await self.context()
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(evidence=[{"id": str(UUID(int=999)), "note": "블록을 즐겨 했어요"}]),
                candidate(content="모래성 쌓기", evidence=[DOC_PICK]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.error["code"] == ErrorCode.EVIDENCE_REQUIRED
        assert ctx.state.suggestions == ()

    async def test_사유가_섞이면_EVIDENCE_REQUIRED_가_대표이고_설명은_후보마다_싣는다(self):
        """로그는 대표 코드 하나로 세고, 모델은 후보마다 고칠 방향을 받는다 (#195 리뷰)."""
        ctx = await self.context()
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(content="아파트 물놀이터 가기", evidence=[DOC_PICK]),
                candidate(evidence=[{"id": str(UUID(int=999)), "note": "블록을 즐겨 했어요"}]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.error["code"] == ErrorCode.EVIDENCE_REQUIRED
        assert "1번 후보" in result.error["message"]
        assert "2번 후보" in result.error["message"]

    @pytest.mark.parametrize("days_ago", [0, 6])
    async def test_오늘을_포함한_7일_안에_한_활동은_거절(self, days_ago):
        """창은 오늘과 앞 6일이다. 6일 전이 마지막 날이다."""
        ctx = await self.context(recent=[("종이컵 탑 쌓기", days_ago)])
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[DOC_PICK]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.success is False
        assert "3번 후보" in result.error["message"]

    @pytest.mark.parametrize("days_ago", [7, 8])
    async def test_7일_전에_한_활동은_다시_낼_수_있다(self, days_ago):
        """정확히 7일 전이 경계다 — 창 밖이다 (#195 리뷰)."""
        ctx = await self.context(recent=[("종이컵 탑 쌓기", days_ago)])
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[DOC_PICK]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.success is True

    async def test_안전_필터로_빠지면_모델에게_돌려주지_않고_남은_것과_제외_목록을_담는다(self):
        """재호출 1회는 run() 이 제외 목록으로 한다. 사유는 넣지 않는다 (Tool_공통.md §5-2)."""
        entry = SafetyEntry(kind="allergy", label="밀", status="active")
        ctx = await self.context(safety=InMemorySafety([entry]))
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="밀가루 점토 놀이", evidence=[DOC_PICK]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert result.success is True
        assert result.data["count"] == 2
        assert len(ctx.state.suggestions) == 2
        assert ctx.state.excluded == ("밀가루 점토 놀이",)

    async def test_식품_사전에_없는_알레르기는_확인_문구를_run_state_에_담는다(self):
        """화면 자리(#249)가 생기기 전까지 run state 에 둔다. 이름이 문장에 없으면 후보는
        그대로다."""
        entry = SafetyEntry(kind="allergy", label="꽃가루", status="active")
        ctx = await self.context(safety=InMemorySafety([entry]))
        await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[DOC_PICK]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert len(ctx.state.suggestions) == 3
        assert ctx.state.cautions == (
            "등록된 알레르기(꽃가루)가 있어요. 장소랑 재료를 한 번 확인해 주세요.",
        )

    async def test_출력_검증_동안_안전_정보를_다시_읽지_않는다(self):
        """build_gate 가 한 번 읽은 값을 쓴다. 다시 읽으면 게이트와 필터가 다른 행을 볼 수 있다."""
        safety = InMemorySafety([])
        ctx = await self.context(safety=safety)
        assert safety.calls == 1
        await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[DOC_PICK]),
                candidate(content="종이컵 탑 쌓기", evidence=[DOC_PICK]),
            ),
        )
        assert safety.calls == 1

    async def test_build_gate_전에_불리면_실패한다(self):
        ctx = await self.context()
        ctx.state.safety_entries = None
        with pytest.raises(RuntimeError):
            await propose_activity_candidates(ctx, self.args(candidate(), candidate(), candidate()))


class TestSaidInRequest:
    """requested_play 는 보호자가 이번 요청에서 한 말에 있어야 한다 (#597 리뷰)."""

    @pytest.mark.parametrize(
        ("play", "said"),
        [
            ("물놀이", "물놀이 뭐 하지?"),
            ("티니핑", "티니핑으로 놀고 싶대"),
            ("공놀이", "공 굴리기 놀이"),  # "놀이" 를 떼면 "공" — 낱말이 같다
            ("전래놀이", "전래놀이 해보고 싶어"),
            ("쿠키 만들기", "아이랑 쿠키 만들기 하고 싶어"),
            # 정한 놀이 옆의 때 · 일반 표현은 빼고 본다
            ("물놀이 추천", "물놀이 추천해줘"),
            ("주말 물놀이", "주말에 물놀이 하려는데"),
        ],
    )
    def test_보호자_말에_있으면_요청이다(self, play, said):
        assert said_in_request(play, [said]) is True

    @pytest.mark.parametrize(
        ("play", "said"),
        [
            ("블록놀이", "오늘 뭐 하고 놀까?"),  # 막연한 요청에 모델이 지어 넣은 놀이
            ("놀이", "놀이 추천해줘"),  # "놀이" 만으로는 정한 게 아니다
            ("공", "공룡 그림책 읽고 할 놀이"),  # 한 글자는 낱말이 같아야 한다
            # 언제 · 어디서 · 날씨 · 일반 표현만 남으면 막연한 요청이다 (#597 리뷰)
            ("놀이 추천", "놀이 추천해줘"),
            ("오늘", "오늘 뭐 하고 놀까?"),
            ("주말", "주말에 뭐 하고 놀까?"),
            ("실내 놀이", "비 오는 날 실내 놀이 추천해줘"),
            ("비 오는 날 놀이", "비 오는 날 놀 거 추천해줘"),
            # 낱말 하나만 겹쳐서는 안 된다 — 남은 낱말이 모두 보호자 말에 있어야 한다
            ("실내 블록놀이", "비 오는 날 실내 놀이 추천해줘"),
            (None, "물놀이 뭐 하지?"),
            ("", "물놀이 뭐 하지?"),
        ],
    )
    def test_보호자_말에_없으면_요청이_아니다(self, play, said):
        assert said_in_request(play, [said]) is False
