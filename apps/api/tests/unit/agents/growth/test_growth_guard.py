"""Growth 문장 가드 — 모델이 쓴 문장의 진단명 · 강압 · 구매 유도를 목록으로 막는다.

목록은 `reference/growth_guard.yaml` 이다. 사람이 정하고, 오탐 · 놓침은 지어낸 문장으로 잰다.
걸리면 고치지 않고 후보를 뺀다. 어느 낱말에 걸렸는지는 종류 키만 남긴다(원문 없음).
"""

import pytest

from app.agents.growth import guard as guard_module
from app.agents.growth.doc_seed import growth_doc_seed
from app.agents.growth.guard import (
    GUARD_KINDS,
    GuardVerdict,
    check_growth_text,
    guard_terms,
    parse_guard_terms,
)

# (문장, 종류) — 코드가 막아야 하는 것. 전부 지어낸 문장이다
BLOCKED = [
    ("게임 중독을 막으려면 시간을 정해요", "diagnosis"),
    ("ADHD 아이는 이렇게 도와요", "diagnosis"),
    ("주의력결핍이 의심되면 놀이를 바꿔요", "diagnosis"),
    ("과잉행동을 줄이는 놀이예요", "diagnosis"),
    ("자폐 아동에게 맞는 활동이에요", "diagnosis"),
    ("틱이 생길 수 있으니 조심해요", "diagnosis"),
    ("강박을 줄이는 방법이에요", "diagnosis"),
    ("우울증 걱정이 있을 때 해 봐요", "diagnosis"),
    ("발달 장애를 예방하는 놀이예요", "diagnosis"),
    ("이 증후군에 좋은 활동이에요", "diagnosis"),
    ("질환이 있는 아이에게 권해요", "diagnosis"),
    ("치료 효과가 있는 놀이예요", "diagnosis"),
    ("처방처럼 매일 해 주세요", "diagnosis"),
    ("말을 안 들으면 손들고 서 있게 해요", "coercion"),
    ("체벌 대신 이렇게 해요", "coercion"),
    ("회초리를 들어 보세요", "coercion"),
    ("때리는 시늉을 하며 겁을 줘요", "coercion"),
    ("벌을 세워 보세요", "coercion"),
    ("방에 가둬 두면 조용해져요", "coercion"),
    ("밥을 굶겨서라도 먹여요", "coercion"),
    ("말 안 들으면 두고 간다고 해요", "coercion"),
    ("협박하듯 말해요", "coercion"),
    ("창피를 줘서 그만두게 해요", "coercion"),
    ("아이를 꾸짖어서 멈추게 해요", "coercion"),
    ("혼내면서 가르쳐요", "coercion"),
    ("학습 앱을 결제해 보세요", "commerce"),
    ("장난감을 사 주면 그만해요", "commerce"),
    ("유료 강의를 구독해 보세요", "commerce"),
    ("다운로드한 영상을 보여 줘요", "commerce"),
    ("게임 앱 설치 후 시간을 정해요", "commerce"),
    ("새 교재를 구매해서 따라 해요", "commerce"),
    ("열매를 따 오지 않으면 매를 들어요", "coercion"),
    ("교재를 구매해 보세요", "commerce"),
    ("병원에서 치료가 필요한 아이예요", "diagnosis"),
    ("아이 학습 앱을 결제해요", "commerce"),
]

# 비슷하게 생겼지만 좋은 문장. 걸리면 목록이나 guard 를 고친다
ALLOWED = [
    "장애물 넘기 놀이",
    "플라스틱 컵 쌓기",
    "식중독 예방 손 씻기",
    "인사 주고받기 놀이",
    "신발을 한 켤레씩 묶어 세기",
    "소리를 지르고 싶을 때 쓸 말 알려 주기",
    "스틱으로 북 치기",
    "블록을 쌓아 탑 만들기",
    "그림책을 읽고 이야기 나누기",
    "화면을 끄고 블록 쌓기",
    "약속 카드를 붙여 두고 지켜 보기",
    "공을 굴려 상자에 넣기",
    "손뼉을 치며 노래 부르기",
    "아이가 마음을 말로 표현하도록 기다려 주기",
    "속상한 마음을 먼저 받아 주기",
    "숨을 크게 쉬며 천천히 세기",
    "종이 가방에 모양 카드를 담아 보기",
    "낙엽을 주워 색깔별로 나누기",
    "모래시계가 끝나면 장난감 정리하기",
    "가족이 함께 규칙을 정해 보기",
    "오늘 있었던 일 한 가지씩 말하기",
    "아이가 고른 책 한 권 같이 읽기",
    "컵에 물을 따라 옮기는 연습",
    "손잡고 천천히 걸어 보기",
    "인형에게 밥 먹여 주는 놀이",
    "아이 말을 끝까지 들어 주기",
    "달리기 놀이 후 물 마시기",
    "스티커로 칭찬 판 꾸미기",
    "벌레 관찰 일기 그려 보기",
    "함께 정한 약속을 지켰을 때 칭찬해 주기",
    "감정 얼굴 카드 고르기",
    "도토리 열매를 관찰해요",
    "열매를 모아 분류하기",
    "소매를 걷고 손 씻기",
    "끈으로 묶어 두 개씩 세기",
    "틱택토 놀이 하기",
    "로보틱스 블록 놀이",
    "가게 놀이로 결제하는 흉내 내기",
    "병원 놀이로 인형 치료해 주기",
    "약국 놀이로 처방전 그리기",
    "장애인 친구와 함께하는 놀이",
]


class TestCheck:
    @pytest.mark.parametrize(("text", "kind"), BLOCKED)
    def test_진단명_강압_구매_문장은_걸린다(self, text, kind):
        verdict = check_growth_text((text,))
        assert verdict.allowed is False and kind in verdict.hits

    @pytest.mark.parametrize("text", ALLOWED)
    def test_비슷한_좋은_문장은_걸리지_않는다(self, text):
        assert check_growth_text((text,)) == GuardVerdict(allowed=True, hits=())

    def test_시드_문서_행_문장은_가드에_걸리지_않는다(self):
        rows = [e.to_row() for e in growth_doc_seed() if e.meta.status == "approved"]
        assert rows
        found = [
            row.doc_key
            for row in rows
            if not check_growth_text((row.title, row.body, *row.materials)).allowed
        ]
        assert found == []

    def test_걸린_종류만_남기고_원문은_남기지_않는다(self):
        verdict = check_growth_text(("게임 중독을 막으려면 학습 앱을 결제해요",))
        assert verdict.hits == ("diagnosis", "commerce")
        assert "중독" not in str(verdict) and "결제" not in str(verdict)

    def test_칸을_이어_붙이지_않는다(self):
        # 제목 끝 "사" 와 단계 첫 "줘" 가 이어져 "사 줘" 로 걸리면 안 된다
        assert check_growth_text(("인사", "줘 보기")).allowed is True

    def test_빈_글은_통과한다(self):
        assert check_growth_text(()).allowed is True
        assert check_growth_text(("", "  ")).allowed is True


class TestLoader:
    def test_실제_목록을_읽는다(self):
        terms = guard_terms()
        assert {term.key for term in terms} == set(GUARD_KINDS)

    def _data(self, **override):
        base = {
            "diagnosis": [{"label": "중독", "aliases": ["중독"], "guards": ["식중독"]}],
            "coercion": [{"label": "체벌", "aliases": ["체벌"]}],
            "commerce": [{"label": "결제", "aliases": ["결제"]}],
        }
        return {**base, **override}

    def test_모양이_맞으면_종류_키로_읽는다(self):
        terms = parse_guard_terms(self._data())
        assert [term.key for term in terms] == ["diagnosis", "coercion", "commerce"]

    def test_종류가_빠지면_거절한다(self):
        data = self._data()
        del data["commerce"]
        with pytest.raises(ValueError, match="commerce"):
            parse_guard_terms(data)

    def test_한_글자_별칭은_guard_가_있어야_한다(self):
        bad = self._data(diagnosis=[{"label": "틱", "aliases": ["틱"]}])
        with pytest.raises(ValueError, match="한 글자"):
            parse_guard_terms(bad)
        good = self._data(diagnosis=[{"label": "틱", "aliases": ["틱"], "guards": ["스틱"]}])
        assert parse_guard_terms(good)

    def test_정해지지_않은_칸이나_겹치는_label_은_거절한다(self):
        with pytest.raises(ValueError, match="정해지지 않은 칸"):
            parse_guard_terms(self._data(coercion=[{"label": "체벌", "aliases": ["체벌"], "x": 1}]))
        dup = self._data(
            coercion=[
                {"label": "체벌", "aliases": ["체벌"]},
                {"label": "체벌", "aliases": ["회초리"]},
            ]
        )
        with pytest.raises(ValueError, match="겹친다"):
            parse_guard_terms(dup)

    def test_빈_목록은_거절한다(self):
        with pytest.raises(ValueError, match="비었다"):
            parse_guard_terms(self._data(coercion=[]))


class TestMatcherReuse:
    def test_목록은_한_번만_컴파일한다(self, monkeypatch):
        check_growth_text(("준비",))  # 처음 한 번은 만든다
        built: list[int] = []
        real = guard_module.TermMatcher

        class Counting(real):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                built.append(1)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(guard_module, "TermMatcher", Counting)
        for _ in range(3):
            check_growth_text(("블록 쌓기", "색깔 찾기"))
        assert built == []

    def test_다른_목록을_주면_그_목록으로_본다(self):
        own = parse_guard_terms(
            {
                "diagnosis": [{"label": "x", "aliases": ["가나다"]}],
                "coercion": [{"label": "y", "aliases": ["라마바"]}],
                "commerce": [{"label": "z", "aliases": ["사아자"]}],
            }
        )
        assert check_growth_text(("가나다",), terms=own).hits == ("diagnosis",)
        assert check_growth_text(("체벌",), terms=own).allowed is True
