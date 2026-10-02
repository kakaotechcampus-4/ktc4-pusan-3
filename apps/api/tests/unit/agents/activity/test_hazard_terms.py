"""위험 용어 사전 내용 — 걸려야 할 문장은 걸리고, 평범한 놀이 문장은 통과한다 (설계 3-5).

월령별 차단 · 경고 판정과 0–17개월 승격은 안전 필터 테스트가 본다. 여기는 "어느 축에 걸리나"까지.
매칭은 공통 `term_match` 다. 문장마다 · 재료마다 따로 스캔한다 — 이어 붙이지 않는다.
"""

import pytest

from app.agents.common.reference import hazard_terms
from app.rules.term_match import match_terms, normalize


def axes_hit(*texts: str) -> set[str]:
    dictionary = hazard_terms()
    hits: set[str] = set()
    for text in texts:
        hits.update(dictionary.axis_of(key).name for key in match_terms(text, dictionary.terms))
    return hits


MUST_HIT = {
    # small_parts
    "구슬 꿰기": "small_parts",
    "수정토 촉감놀이": "small_parts",
    "워터비즈 촉감놀이": "small_parts",
    "동전 모으기": "small_parts",
    "단추형 전지": "small_parts",
    "네오디뮴 자석 놀이": "small_parts",
    "마른 콩 촉감놀이": "small_parts",
    "캡슐 장난감 뽑기": "small_parts",
    "작은 레고로 집 짓기": "small_parts",
    "레고 조립": "small_parts",
    "비비탄 총 놀이": "small_parts",
    # food_choking
    "팝콘 만들기": "food_choking",
    "곤약 젤리 먹기": "food_choking",
    "땅콩 까기": "food_choking",
    "마시멜로 탑 쌓기": "food_choking",
    "통 소시지 꼬치 굽기": "food_choking",
    "풍선껌 불기": "food_choking",
    "통포도 먹기": "food_choking",
    # suffocation_film
    "풍선 배구 놀이": "suffocation_film",
    "물풍선 던지기": "suffocation_film",
    "비닐봉지 낙하산 만들기": "suffocation_film",
    "지퍼백 촉감 주머니": "suffocation_film",
    "비닐장갑 풍선 만들기": "suffocation_film",
    "비닐랩으로 감싸기 놀이": "suffocation_film",
    "주방 랩 씌우기": "suffocation_film",
    # water
    "욕조 물놀이": "water",
    "해수욕장 가기": "water",
    "계곡 물놀이": "water",
    "물놀이 튜브 타기": "water",
    "목욕 놀이": "water",
    "바닥분수 뛰어놀기": "water",
    "대야에 물 받아 놀기": "water",
    "호수공원에서 물놀이": "water",  # 공원 이름 guard 가 물놀이까지 덮지 않는다
    # wheeled_motorized · trampoline · infant_walker
    "전동킥보드 타기": "wheeled_motorized",
    "공유 킥보드 타기": "wheeled_motorized",
    "아빠 오토바이 타기": "wheeled_motorized",
    "트램펄린 타기": "trampoline",
    "방방 타러 가기": "trampoline",
    "키즈카페 에어바운스": "trampoline",
    "보행기 태우기": "infant_walker",
}

# 평범한 놀이 문장. 걸리면 과차단이다
MUST_PASS = [
    "가위바위보",
    "비눗방울 놀이",
    "큰 블록으로 탑 쌓기",
    "블록 쌓기",
    "물티슈로 손 닦기",
    "물감 놀이",
    "랩 따라 부르기",
    "랩 음악 듣기",
    "스파게티 만들기",
    "타임캡슐 만들기",
    "콩주머니 던지기",
    "콩나물 기르기",
    "바다 그림 그리기",
    "파도 소리 듣기",
    "약국 놀이",
    "호두까기 인형 음악 듣기",
    "방방곡곡 여행 그림책",
    "자석칠판에 그림 그리기",
    "고무줄놀이",
    "오토바이 그림 그리기",
    "장난감 오토바이 밀기",
    "큰 공 굴리기",
    "모래놀이",
    "방울토마토 씻기",
    "빨대 불기",
    "솜사탕 먹기",
    "공놀이",
    "보물찾기",
    # 9/29 검수로 뺀 항목
    "면봉 찍기 그림",
    "모루로 동물 만들기",
    "반짝이 가루 뿌리기",
    "시리얼 꿰기 목걸이",
    "탱탱볼 놀이",
    "고카트 타기",
    # 10/02 검수로 고친 항목
    "수영복 입고 사진",
    "일산호수공원 산책",
    "수변공원 산책",
    "강변공원 산책",
    "강변 산책",
    "자석 낚시 놀이",
    "볼트 너트 장난감 조립",
    "쌀 촉감놀이",
    "경찰 오토바이 구경",
    "개구리알 관찰",
    "분수 구경",
    "물웅덩이 첨벙",
    "레고 듀플로로 집 만들기",
    "큰 레고 쌓기",
]


@pytest.mark.parametrize(("text", "axis"), MUST_HIT.items())
def test_걸려야_할_문장(text, axis):
    assert axis in axes_hit(text)


@pytest.mark.parametrize("text", MUST_PASS)
def test_평범한_놀이_문장은_통과한다(text):
    assert axes_hit(text) == set()


def test_재료는_항목마다_따로_스캔한다():
    """이어 붙이면 "작은" + "블록 담는 통"이 "작은 블록"이 된다."""
    assert axes_hit("상자 만들기", "작은", "블록 담는 통") == set()
    assert axes_hit("목걸이 만들기", "구슬", "실") == {"small_parts"}


def test_풍선껌은_풍선이_아니라_음식이다():
    assert axes_hit("풍선껌 불기") == {"food_choking"}


class TestGuards:
    @pytest.mark.parametrize(
        ("label", "guard"),
        [(term.key, guard) for term in hazard_terms().terms for guard in term.guards],
    )
    def test_guard_문구만_스캔하면_자기_축에는_걸리지_않는다(self, label, guard):
        """guard 에 "더 위험한 표현"을 넣지 않는다 (3-5). 다른 축으로 넘기는 guard 는 된다 —
        풍선 → 풍선껌은 food_choking 이 잡는다. 그 축이 잡으니 위험이 숨지 않는다.
        """
        own_axis = hazard_terms().axis_of(label).name
        assert own_axis not in axes_hit(guard), f"{label} 의 guard {guard!r}"

    @pytest.mark.parametrize(
        ("label", "guard"),
        [(term.key, guard) for term in hazard_terms().terms for guard in term.guards],
    )
    def test_guard_는_자기_용어의_별칭을_덮는다(self, label, guard):
        """아무 별칭도 덮지 않는 guard 는 쓸모가 없다 — 오타이거나 다른 용어에 붙었다."""
        term = next(t for t in hazard_terms().terms if t.key == label)
        assert any(normalize(alias) in normalize(guard) for alias in term.aliases)


def test_축마다_출처_링크가_있다():
    for axis in hazard_terms().axes.values():
        assert "http" in axis.source, axis.name


def test_경고가_있는_축은_화면_문구가_있다():
    """경고 문구는 LLM 이 쓰지 않는다. 상수로 박는다 (4-1)."""
    for axis in hazard_terms().axes.values():
        if axis.warn_below_month is not None:
            assert axis.warning_text, axis.name
