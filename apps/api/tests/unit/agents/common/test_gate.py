"""게이팅 입력.

F-4 — `health_safety` 0행이 "없다고 확인함"인지 "물어본 적 없음"인지는 행으로 표현할 수 없다.
"""

from datetime import date

import pytest

from app.agents.common.gate import DataReady, Gate
from app.rules.age import life_stage

STAGE = life_stage(date(2022, 9, 22), date(2026, 9, 22))  # 48개월


def gate(**kwargs):
    base = dict(
        stage=STAGE,
        consent_child_health=True,
        safety_ok=True,
        allergy_status="none",
    )
    return Gate(**{**base, **kwargs})


class TestAllergyFilterReady:
    def test_none_은_0행이어도_연다(self):
        """없다고 확인한 아이다. 여기서 막으면 알레르기 없는 아이가 추천을 영영 못 받는다."""
        assert gate(allergy_status="none", safety_row_count=0).allergy_filter_ready is True

    def test_has_는_등록이_있어야_연다(self):
        assert gate(allergy_status="has", safety_row_count=1).allergy_filter_ready is True
        assert gate(allergy_status="has", safety_row_count=0).allergy_filter_ready is False

    def test_unknown_은_막는다(self):
        """물어본 적이 없어 0행의 뜻을 모른다."""
        assert gate(allergy_status="unknown", safety_row_count=0).allergy_filter_ready is False
        assert gate(allergy_status="unknown", safety_row_count=3).allergy_filter_ready is False

    @pytest.mark.parametrize("status", ["none", "has", "unknown"])
    def test_조회_실패는_무조건_막는다(self, status):
        """조회 실패는 0행이 아니다. 빈 목록으로 숨기지 않는다."""
        assert (
            gate(allergy_status=status, safety_ok=False, safety_row_count=5).allergy_filter_ready
            is False
        )


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
        """age_months · corrected_months 를 따로 넘기지 않는다."""
        assert gate().stage.months == 48
        assert gate().stage.stage == "preschool"
