"""안전 필터 — 위험 용어(월령별 차단 · 경고) · health_safety(active) (D6 · D7 · 4-1).

필터는 순수 함수다. health_safety 는 build_gate 가 읽은 행을 인자로만 받는다.
"""

import pytest

from app.agents.activity.schemas.recommend import ActivityCandidate
from app.agents.activity.store.ports import SafetyEntry
from app.agents.activity.tools import filters
from app.agents.activity.tools.filters import (
    SAFETY_PROMOTE_BELOW_MONTH,
    check_candidate,
    filter_activity_safety,
    safety_terms,
)
from app.agents.common.reference import hazard_terms


def candidate(content: str, materials: tuple[str, ...] = ()) -> ActivityCandidate:
    return ActivityCandidate.model_validate(
        dict(
            content=content,
            setting="indoor",
            materials=list(materials),
            physical_intensity="low",
            involves_food=False,
            caregiver_role="together",
            why_this="이유",
            why_now="지금",
        )
    )


def check(content, materials=(), *, months=40, entries=()):
    return check_candidate(
        candidate(content, materials), months=months, terms=safety_terms(entries)
    )


def allergy(label, status="active"):
    return SafetyEntry(kind="allergy", label=label, status=status)


class TestHazardTerms:
    @pytest.mark.parametrize(("months", "blocked"), [(35, True), (36, False)])
    def test_작은_부품은_36개월_미만_차단(self, months, blocked):
        assert check("구슬 꿰기", months=months).blocked is blocked

    def test_경고_구간은_통과하고_상수_문구를_붙인다(self):
        result = check("구슬 꿰기", months=40)
        axis = hazard_terms().axes["small_parts"]
        assert result.warnings == (axis.warning_text,)
        assert result.hits == ("small_parts",)

    @pytest.mark.parametrize(
        ("months", "blocked"),
        [(SAFETY_PROMOTE_BELOW_MONTH - 1, True), (SAFETY_PROMOTE_BELOW_MONTH, False)],
    )
    def test_18개월_미만은_경고도_차단으로_올린다(self, months, blocked):
        """물놀이는 72개월 미만 경고뿐인 축이다. 17개월까지는 그래도 차단이다 (D6 규칙 ②)."""
        assert check("욕조 물놀이", months=months).blocked is blocked

    def test_차단이면_경고_문구를_싣지_않는다(self):
        assert check("구슬 꿰기", months=20).warnings == ()

    def test_재료에만_있어도_걸린다(self):
        assert check("목걸이 만들기", ("구슬", "실"), months=20).blocked is True

    def test_재료는_항목마다_따로_대조한다(self):
        """이어 붙이면 "작은" + "블록 담는 통" 이 "작은 블록" 이 된다."""
        assert check("상자 만들기", ("작은", "블록 담는 통"), months=20).blocked is False

    def test_평범한_놀이는_걸리지_않는다(self):
        result = check("큰 블록으로 탑 쌓기", ("큰 블록",), months=12)
        assert (result.blocked, result.hits, result.warnings) == (False, (), ())


class TestHealthSafety:
    def test_active_알레르기는_사전으로_넓혀_차단한다(self):
        """별칭 칸이 DB 에서 빠졌다(10/4). "밀" 로 등록돼도 "밀가루 점토" 가 걸린다 (D7)."""
        result = check("밀가루 점토 만들기", entries=[allergy("밀")])
        assert result.blocked is True
        assert result.hits == ("health_safety",)

    @pytest.mark.parametrize("status", ["none", "retracted"])
    def test_active_가_아니면_막지_않는다(self, status):
        assert check("밀가루 점토 만들기", entries=[allergy("밀", status)]).blocked is False

    def test_행이_없으면_unknown_이라_막지_않는다(self):
        assert check("밀가루 점토 만들기", entries=[]).blocked is False

    def test_19종_밖은_등록된_이름으로_대조한다(self):
        assert check("키위 껍질 만져 보기", entries=[allergy("키위")]).blocked is True

    def test_사전에_없는_환경_알레르기도_이름으로_대조한다(self):
        """꽃가루 · 동물털도 kind='allergy' 다. 'environmental' 은 고소공포 같은 것이다."""
        assert check("꽃가루 관찰하기", entries=[allergy("꽃가루")]).blocked is True

    def test_environmental_도_이름으로_대조한다(self):
        entry = SafetyEntry(kind="environmental", label="고소공포", status="active")
        assert check("고소공포 이겨 내기 놀이", entries=[entry]).blocked is True

    @pytest.mark.parametrize(
        ("label", "content", "blocked"),
        [
            ("갑각류", "꽃게 껍데기 관찰", True),
            ("갑각류", "새우깡 탑 쌓기", True),
            ("우유 알레르기", "생크림 케이크 꾸미기", True),
            ("우유, 계란", "달걀 껍데기 모자이크", True),
            ("Milk", "치즈 모양 맞추기", True),
            ("견과류", "호두 굴리기", True),
            ("쑥 알레르기", "쑥 캐기", True),
            ("우유 알레르기", "큰 블록 쌓기", False),
        ],
    )
    def test_보호자가_적은_이름을_읽어서_막는다(self, label, content, blocked):
        """label 은 자유 입력이다. 별칭과 똑같을 때만 읽으면 나머지가 통과한다 (#264)."""
        assert check(content, entries=[allergy(label)]).blocked is blocked

    @pytest.mark.parametrize(
        "content",
        ["공 밀기 놀이", "카드 게임", "비밀 상자 찾기", "닭 그림 그리기", "굴렁쇠 굴리기"],
    )
    def test_사전의_한_글자_별칭은_놀이_문장에_쓰지_않는다(self, content):
        """ "밀" · "게" · "닭" · "굴" 을 그대로 대조하면 평범한 놀이 문장이 알레르기로 걸린다."""
        entries = [allergy("밀"), allergy("게"), allergy("닭고기"), allergy("조개류")]
        result = check(content, entries=entries)
        assert (result.blocked, result.allergens) == (False, ())

    @pytest.mark.parametrize(
        ("label", "content"),
        [("잣", "잣 까기"), ("대두", "콩주머니 던지기"), ("콩", "콩 고르기"), ("쑥", "쑥 캐기")],
    )
    def test_허용_목록의_한_글자는_막는다(self, label, content):
        """잣 · 콩 · 깨 · 쑥은 놀이 문장에서도 거의 그 음식이다 (#261 리뷰)."""
        assert check(content, entries=[allergy(label)]).blocked is True

    def test_사전에_없는_한_글자는_글자_그대로_대조하지_않는다(self):
        """강아지 알레르기를 "개" 로 적어도 "블록 3개" 를 막지 않는다 (#298 리뷰)."""
        assert check("블록 3개 쌓기", entries=[allergy("개")]).blocked is False

    @pytest.mark.parametrize(
        ("content", "label"),
        [
            ("플라스틱 포크로 점토 찍기", "돼지고기"),
            ("에그 쉐이커 흔들기", "난류"),
            ("밀크 카톤으로 집 만들기", "우유"),
            ("피치색 물감 칠하기", "복숭아"),
            ("전복 모양 종이 접기", "조개류"),
            ("돼지저금통 동전 넣기", "돼지고기"),
        ],
    )
    def test_놀이_문장에서_다른_뜻인_별칭은_쓰지_않는다(self, content, label):
        """공용 사전의 두 글자 별칭 중 놀이 문장에서 다른 뜻으로 읽히는 것 (#261 리뷰)."""
        result = check(content, entries=[allergy(label)])
        assert (result.blocked, result.allergens) == (False, ())


class TestAllergens:
    """승인할 때 "이 놀이에는 ○○ 가 들어가요" 안내에 쓴다 (#232). 상태는 담지 않는다."""

    def test_19종은_정식_명칭으로_담는다(self):
        assert check("콩나물 기르기").allergens == ("대두",)
        assert check("달걀 껍데기 모자이크").allergens == ("난류",)

    def test_19종_밖은_등록된_항목만_잡힌다(self):
        """쑥처럼 사전에 없는 항목은 그 아이에게 등록돼 있어야 이름을 안다."""
        assert check("쑥 캐기").allergens == ()
        assert check("쑥 캐기", entries=[allergy("쑥 알레르기", "none")]).allergens == ("쑥",)

    def test_재료에서도_찾는다(self):
        assert check("점토 놀이", ("밀가루", "물")).allergens == ("밀",)


class TestFilter:
    def test_후보마다_같은_순서로_판정한다(self):
        result = filter_activity_safety(
            [candidate("구슬 꿰기"), candidate("큰 블록 쌓기")], months=20, safety=[]
        )
        assert [r.blocked for r in result] == [True, False]

    def test_위험_용어_사전을_못_읽으면_예외를_그대로_올린다(self, monkeypatch):
        """빈 사전으로 통과시키면 위험한 놀이가 조용히 나간다."""

        def broken():
            raise ValueError("hazard_terms.yaml 을 읽지 못했다")

        monkeypatch.setattr(filters, "hazard_terms", broken)
        with pytest.raises(ValueError):
            check("큰 블록 쌓기")
