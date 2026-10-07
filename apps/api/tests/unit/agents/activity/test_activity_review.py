"""출력 검증 — 설계 3-3 순서 중 안전 필터를 뺀 나머지.

- 검사마다 거절 · 통과 양쪽을 본다.
- 지어낸 근거 id 는 거절된다. 통과한 근거는 공통 EvidenceCitation 으로 바뀐다.
- 거절 사유를 모델에게 돌려줄 때 활동명 · 근거 문장을 싣지 않는다.
- 장소 이름은 이번 run 장소 조회 결과에 있어야 하고, 통과하면 코드가 문장 앞에 붙인다.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.result import ErrorCode
from app.agents.activity.review import (
    GENERAL_REASON,
    GUIDANCE,
    MIN_AVOIDED_LABEL,
    RejectReason,
    explain,
    review_candidates,
)
from app.agents.activity.rules import is_recent_duplicate, normalize_activity, overstates
from app.agents.activity.schemas.common import PlaceCategory
from app.agents.activity.schemas.recommend import ActivityCandidate, ProposeActivityCandidatesArgs
from app.agents.activity.store.inmemory import (
    InMemoryActivityMemory,
    InMemorySafety,
    in_memory_ports,
)
from app.agents.activity.store.ports import ActivityObservation, PlaceRow, SafetyEntry
from app.agents.activity.tools.filters import safety_terms
from app.agents.activity.tools.recommend import propose_activity_candidates
from app.agents.common.evidence import RankedEvidence
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
        why_this="블록을 오래 가지고 놀았어요",
        why_now="오늘 오후가 비어 있어요",
        evidence=[{"id": str(BLOCKS), "note": "블록 쌓기를 즐겨 했어요"}],
    )
    return ActivityCandidate.model_validate({**base, **kwargs})


PARK = "사직어린이공원"  # 이번 run 에 장소 조회가 돌려준 곳


def review(
    *candidates, months=40, seen=SEEN, recent=(), places=(PARK,), safety=(), outdoor_ok=True
):
    return review_candidates(
        candidates,
        months=months,
        seen=seen,
        recent_activities=recent,
        places=places,
        safety=safety_terms(safety),
        outdoor_ok=outdoor_ok,
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

    def test_근거가_없으면_일반_추천이고_이유를_코드_문구로_덮는다(self):
        (draft,) = review(candidate(evidence=[])).drafts
        assert draft.kind == "general"
        assert draft.reason == GENERAL_REASON


class TestEvaluative:
    @pytest.mark.parametrize("field", ["content", "why_this", "why_now"])
    def test_평가_표현이_있으면_거절(self, field):
        result = review(candidate(**{field: "또래보다 잘하는 블록 쌓기"}))
        assert reason_of(result) is RejectReason.EVALUATIVE

    def test_note_도_본다(self):
        pick = {"id": str(BLOCKS), "note": "또래보다 블록을 잘 쌓았어요"}
        assert reason_of(review(candidate(evidence=[pick]))) is RejectReason.EVALUATIVE

    def test_평가_표현이_먼저다(self):
        """순서가 결과를 바꾼다 — 근거 id 도 틀렸지만 앞 검사 사유가 나간다."""
        result = review(candidate(why_this="재능이 보여요", evidence=[{"id": "x", "note": "n"}]))
        assert reason_of(result) is RejectReason.EVALUATIVE


class TestEvidence:
    @pytest.mark.parametrize("raw", ["not-a-uuid", str(UUID(int=999))])
    def test_조회_결과에_없는_id_는_거절(self, raw):
        """지어낸 근거로 개인화인 척하지 못한다."""
        result = review(candidate(evidence=[{"id": raw, "note": "모래놀이를 했어요"}]))
        assert reason_of(result) is RejectReason.UNKNOWN_EVIDENCE


class TestAvoided:
    def test_싫어한다고_나온_활동은_다시_내지_않는다(self):
        """인용하지 않았어도 조회된 기피면 막는다."""
        result = review(candidate(content="아파트 물놀이터 가기", evidence=[]))
        assert reason_of(result) is RejectReason.AVOIDED

    def test_기피는_근거로는_쓸_수_있다(self):
        """ "물놀이를 싫어해서 모래놀이를 골랐어요" 가 되어야 한다 (D4)."""
        pick = {"id": str(WATER), "note": "물놀이를 할 때 울었어요"}
        result = review(
            candidate(
                content="놀이터에서 모래성 쌓기",
                why_this="물놀이를 싫어해서 모래로 노는 활동을 골랐어요",
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
        result = review(candidate(content="공원에서 공룡 그림 그리기", evidence=[]))
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

    @pytest.mark.parametrize("places", [(PARK,), ()])
    def test_조회_결과에_없는_장소는_거절(self, places):
        """장소를 조회하지 않았거나 결과에 없는 이름이면 지어낸 것으로 본다."""
        result = review(candidate(place_name="해운대 키즈파크"), places=places)
        assert reason_of(result) is RejectReason.UNKNOWN_PLACE
        assert "해운대" not in explain(result.rejections)  # 장소 이름은 로그로 흘러가지 않는다

    def test_띄어쓰기만_다르면_같은_곳이고_조회_결과의_이름을_붙인다(self):
        (draft,) = review(candidate(content="모래성 쌓기", place_name="사직 어린이공원")).drafts
        assert draft.content == "사직어린이공원에서 모래성 쌓기"

    def test_문장에_이미_있으면_다시_붙이지_않는다(self):
        content = "사직어린이공원 모래놀이터에서 모래성 쌓기"
        (draft,) = review(candidate(content=content, place_name=PARK)).drafts
        assert draft.content == content

    @pytest.mark.parametrize("place_name", [None, "", "  "])
    def test_장소가_없으면_문장을_그대로_둔다(self, place_name):
        (draft,) = review(candidate(place_name=place_name)).drafts
        assert draft.content == "큰 블록으로 탑 쌓기"


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
        entry = SafetyEntry(kind="allergy", label="밀", status="active", category=("food",))
        result = review(candidate(content="밀가루 점토 놀이", evidence=[]), safety=[entry])
        assert result.removed[0].hits == ("health_safety",)

    def test_경고_문구는_초안과_같은_순서로_짝짓는다(self):
        result = review(candidate(), candidate(content="구슬 꿰기", evidence=[]), months=40)
        small_parts = hazard_terms().axes["small_parts"].warning_text
        assert result.warnings == ((), (small_parts,))

    def test_allergens_와_items_를_코드가_채운다(self):
        """#232 — 승인 때 알레르기 안내와 일정 준비물에 쓴다. 모델이 고르지 않는다."""
        result = review(
            candidate(content="밀가루 반죽 놀이", materials=["밀가루", "밀대"], evidence=[])
        )
        (draft,) = result.drafts
        assert draft.allergens == ("밀",)
        assert draft.items == ("밀가루", "밀대")


class TestOutdoor:
    """날씨 판정이 야외를 막은 날(D8)에는 야외 후보를 거절한다. 모델이 실내로 고친다."""

    def test_바깥_활동이_어려운_날_야외_후보는_거절(self):
        result = review(candidate(setting="outdoor"), outdoor_ok=False)
        assert reason_of(result) is RejectReason.OUTDOOR_CLOSED

    @pytest.mark.parametrize("setting", ["indoor", "either"])
    def test_실내나_어디서든은_통과(self, setting):
        assert review(candidate(setting=setting), outdoor_ok=False).rejections == ()

    def test_바깥이_괜찮은_날은_야외도_통과(self):
        assert review(candidate(setting="outdoor"), outdoor_ok=True).rejections == ()


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
        return ctx

    def args(self, *candidates):
        return ProposeActivityCandidatesArgs(candidates=list(candidates))

    async def test_세_개가_통과하면_초안을_담는다(self):
        ctx = await self.context()
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[]),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
            ),
        )
        assert result.success is True
        assert result.data["kinds"] == ["personalized", "general", "general"]
        assert len(ctx.state.suggestions) == 3

    async def test_장소_조회가_돌려준_곳을_붙여서_담는다(self):
        ctx = await self.context()
        ctx.state.seen_places[PARK] = PlaceRow(
            name=PARK, category=PlaceCategory.PARK, distance_m=800, source="city_park"
        )
        await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[], place_name=PARK),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
            ),
        )
        assert ctx.state.suggestions[1].content == "사직어린이공원에서 모래성 쌓기"

    async def test_하나라도_걸리면_사유를_돌려주고_아무것도_담지_않는다(self):
        ctx = await self.context()
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="아파트 물놀이터 가기", evidence=[]),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
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
                candidate(content="모래성 쌓기", evidence=[]),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
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
                candidate(content="아파트 물놀이터 가기", evidence=[]),
                candidate(evidence=[{"id": str(UUID(int=999)), "note": "블록을 즐겨 했어요"}]),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
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
                candidate(content="모래성 쌓기", evidence=[]),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
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
                candidate(content="모래성 쌓기", evidence=[]),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
            ),
        )
        assert result.success is True

    async def test_안전_필터로_빠지면_모델에게_돌려주지_않고_남은_것과_제외_목록을_담는다(self):
        """재호출 1회는 run() 이 제외 목록으로 한다. 사유는 넣지 않는다 (Tool_공통.md §5-2)."""
        entry = SafetyEntry(kind="allergy", label="밀", status="active", category=("food",))
        ctx = await self.context(safety=InMemorySafety([entry]))
        result = await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="밀가루 점토 놀이", evidence=[]),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
            ),
        )
        assert result.success is True
        assert result.data["count"] == 2
        assert len(ctx.state.suggestions) == 2
        assert ctx.state.excluded == ("밀가루 점토 놀이",)

    async def test_출력_검증_동안_안전_정보를_다시_읽지_않는다(self):
        """build_gate 가 한 번 읽은 값을 쓴다. 다시 읽으면 게이트와 필터가 다른 행을 볼 수 있다."""
        safety = InMemorySafety([])
        ctx = await self.context(safety=safety)
        assert safety.calls == 1
        await propose_activity_candidates(
            ctx,
            self.args(
                candidate(),
                candidate(content="모래성 쌓기", evidence=[]),
                candidate(content="종이컵 탑 쌓기", evidence=[]),
            ),
        )
        assert safety.calls == 1

    async def test_build_gate_전에_불리면_실패한다(self):
        ctx = await self.context()
        ctx.state.safety_entries = None
        with pytest.raises(RuntimeError):
            await propose_activity_candidates(ctx, self.args(candidate(), candidate(), candidate()))
