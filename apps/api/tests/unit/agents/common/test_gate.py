"""게이팅 입력.

F-4 — 거르는 것은 `state='active'` 뿐이다. 막는 것은 조회 실패 하나다.
`unknown` 은 확인 안내만 붙이고, 0행(건강정보 동의 안 함)은 일반 식단으로 간다.
"""

from datetime import date

import pytest

from app.agents.common.gate import DataReady, Gate
from app.rules.age import life_stage

STAGE = life_stage(date(2022, 9, 22), date(2026, 9, 22))  # 48개월

# 온보딩이 19행을 만든다. 테스트에서는 개수보다 state 분포가 중요해 짧게 쓴다.
ALL_NONE = ("none",) * 3
HAS_ONE = ("active", "none", "none")


def gate(**kwargs):
    base = dict(
        stage=STAGE,
        consent_child_health=True,
        safety_ok=True,
        allergy_states=ALL_NONE,
    )
    return Gate(**{**base, **kwargs})


class TestAllergyFilterReady:
    def test_전부_none_이면_연다(self):
        """없다고 확인한 아이다. 여기서 막으면 알레르기 없는 아이가 추천을 영영 못 받는다."""
        assert gate(allergy_states=ALL_NONE).allergy_filter_ready is True

    def test_active_가_있어도_연다(self):
        """그 행들이 곧 필터 목록이다."""
        assert gate(allergy_states=HAS_ONE).allergy_filter_ready is True

    def test_unknown_은_막지_않는다(self):
        """아직 안 물어본 항목일 뿐이다. 추천은 내보내고 확인 안내만 붙인다."""
        assert gate(allergy_states=("none", "unknown", "none")).allergy_filter_ready is True
        assert gate(allergy_states=("active", "unknown")).allergy_filter_ready is True

    def test_0행도_연다(self):
        """건강정보 동의를 안 하면 행이 안 쌓인다. 그렇다고 식단을 못 받으면 안 된다."""
        assert gate(allergy_states=()).allergy_filter_ready is True

    def test_retracted_는_none_과_같다(self):
        """보호자가 취소한 것이고 지금은 해당 없음이다."""
        assert gate(allergy_states=("retracted", "none")).allergy_filter_ready is True

    @pytest.mark.parametrize("states", [ALL_NONE, HAS_ONE, ()])
    def test_조회_실패는_무조건_막는다(self, states):
        """조회 실패는 0행이 아니다. 빈 목록으로 숨기지 않는다."""
        assert gate(allergy_states=states, safety_ok=False).allergy_filter_ready is False


class TestAllergyUnconfirmed:
    def test_unknown_이_있으면_참(self):
        assert gate(allergy_states=("none", "unknown")).allergy_unconfirmed is True

    def test_전부_답했으면_거짓(self):
        assert gate(allergy_states=ALL_NONE).allergy_unconfirmed is False
        assert gate(allergy_states=HAS_ONE).allergy_unconfirmed is False

    def test_안내와_필터는_별개다(self):
        """확인 안내가 붙어도 추천은 나간다."""
        g = gate(allergy_states=("active", "unknown"))
        assert g.allergy_filter_ready is True and g.allergy_unconfirmed is True


class TestDataReady:
    def test_기본값(self):
        ready = DataReady()
        assert ready.daycare_meal is False
        assert ready.notice is False
        assert ready.book_api is True  # API 는 살아 있다고 보고 장애 때 끈다

    def test_게이트마다_따로_든다(self):
        first = gate(data=DataReady(daycare_meal=True))
        second = gate()
        assert first.data.daycare_meal is True
        assert second.data.daycare_meal is False


class TestGateCarriesAge:
    def test_월령은_stage_가_들고_있다(self):
        """age_months 를 따로 넘기지 않는다."""
        assert gate().stage.months == 48
        assert gate().stage.stage == "preschool"
