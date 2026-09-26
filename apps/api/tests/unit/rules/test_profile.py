"""프로필 상태 전이와 strength 변화 규칙.

순수 함수다. DB 없음. 경계값 양쪽을 본다.
"""

from datetime import date, timedelta

import pytest

from app.rules.profile import (
    DEMOTION_WINDOW_DAYS,
    PROMOTION_THRESHOLD,
    PROMOTION_THRESHOLD_WITH_G,
    PROMOTION_WINDOW_DAYS,
    STRENGTH_DEFAULT,
    STRENGTH_MAX,
    STRENGTH_MIN,
    VERDICT_DECAY,
    apply_correction,
    apply_transition,
    compute_profile_status,
)

# -- 상수 검증 --


class TestPolicyConstants:
    def test_승격_윈도우는_7일(self):
        assert PROMOTION_WINDOW_DAYS == 7

    def test_하강_윈도우는_21일(self):
        assert DEMOTION_WINDOW_DAYS == 21

    def test_승격_기준은_3(self):
        assert PROMOTION_THRESHOLD == 3

    def test_G_있으면_승격_기준은_2(self):
        assert PROMOTION_THRESHOLD_WITH_G == 2

    def test_strength_기본값은_0_5(self):
        assert STRENGTH_DEFAULT == 0.5

    def test_strength_범위는_0에서_1(self):
        assert STRENGTH_MIN == 0.0
        assert STRENGTH_MAX == 1.0

    def test_verdict_감소율_3종(self):
        assert VERDICT_DECAY["wrong"] == 0.93
        assert VERDICT_DECAY["outdated"] == 0.95
        assert VERDICT_DECAY["need_more_observation"] == 0.97


# -- compute_profile_status --


class TestComputeProfileStatus:
    """상태 전이 공식: 위에서부터 먼저 맞는 것 적용.
    1. today - last_observed_on > 21  →  archived
    2. O >= 3 + W  또는 (O >= 2 + W 그리고 G)  →  confirmed
    3. 그 외  →  candidate
    """

    TODAY = date(2026, 9, 26)

    # -- archived (순서 1) --

    def test_22일_전_마지막_관찰이면_archived(self):
        last = self.TODAY - timedelta(days=22)
        assert (
            compute_profile_status(
                obs_count=0,
                wrong_count=0,
                has_signal=False,
                last_observed_on=last,
                today=self.TODAY,
            )
            == "archived"
        )

    def test_정확히_21일이면_archived_아님(self):
        last = self.TODAY - timedelta(days=21)
        assert (
            compute_profile_status(
                obs_count=0,
                wrong_count=0,
                has_signal=False,
                last_observed_on=last,
                today=self.TODAY,
            )
            == "candidate"
        )

    def test_archived는_O가_충분해도_우선(self):
        last = self.TODAY - timedelta(days=22)
        assert (
            compute_profile_status(
                obs_count=10,
                wrong_count=0,
                has_signal=True,
                last_observed_on=last,
                today=self.TODAY,
            )
            == "archived"
        )

    # -- confirmed (순서 2) --

    def test_O_3이면_confirmed(self):
        assert (
            compute_profile_status(
                obs_count=3,
                wrong_count=0,
                has_signal=False,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "confirmed"
        )

    def test_O_2이면_G_없으면_candidate(self):
        assert (
            compute_profile_status(
                obs_count=2,
                wrong_count=0,
                has_signal=False,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "candidate"
        )

    def test_O_2이고_G_있으면_confirmed(self):
        assert (
            compute_profile_status(
                obs_count=2,
                wrong_count=0,
                has_signal=True,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "confirmed"
        )

    def test_O_1이고_G_있어도_candidate(self):
        assert (
            compute_profile_status(
                obs_count=1,
                wrong_count=0,
                has_signal=True,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "candidate"
        )

    # -- W(wrong) 패널티 --

    def test_W_1이면_기준이_1_올라간다(self):
        assert (
            compute_profile_status(
                obs_count=3,
                wrong_count=1,
                has_signal=False,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "candidate"
        )
        assert (
            compute_profile_status(
                obs_count=4,
                wrong_count=1,
                has_signal=False,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "confirmed"
        )

    def test_W_패널티는_G_분기에도_적용(self):
        assert (
            compute_profile_status(
                obs_count=2,
                wrong_count=1,
                has_signal=True,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "candidate"
        )
        assert (
            compute_profile_status(
                obs_count=3,
                wrong_count=1,
                has_signal=True,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "confirmed"
        )

    # -- candidate (순서 3) --

    def test_21일_이내_관찰_있지만_승격_미달이면_candidate(self):
        last = self.TODAY - timedelta(days=10)
        assert (
            compute_profile_status(
                obs_count=1,
                wrong_count=0,
                has_signal=False,
                last_observed_on=last,
                today=self.TODAY,
            )
            == "candidate"
        )

    def test_O_0이고_오늘_관찰이면_candidate(self):
        assert (
            compute_profile_status(
                obs_count=0,
                wrong_count=0,
                has_signal=False,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "candidate"
        )

    # -- 경계 조합 --

    @pytest.mark.parametrize(
        ("oc", "wc", "sig", "days_ago", "expected"),
        [
            (0, 0, False, 0, "candidate"),
            (3, 0, False, 0, "confirmed"),
            (2, 0, True, 0, "confirmed"),
            (2, 0, False, 0, "candidate"),
            (5, 2, False, 0, "confirmed"),  # 5 >= 3+2
            (4, 2, False, 0, "candidate"),  # 4 < 3+2
            (4, 2, True, 0, "confirmed"),  # 4 >= 2+2 and G
            (3, 2, True, 0, "candidate"),  # 3 < 2+2
            (10, 0, True, 22, "archived"),  # 22일 지남
            (0, 0, False, 21, "candidate"),  # 정확히 21일
        ],
    )
    def test_경계_조합(self, oc, wc, sig, days_ago, expected):
        last = self.TODAY - timedelta(days=days_ago)
        assert (
            compute_profile_status(
                obs_count=oc,
                wrong_count=wc,
                has_signal=sig,
                last_observed_on=last,
                today=self.TODAY,
            )
            == expected
        )


# -- apply_correction --


class TestApplyCorrection:
    def test_wrong은_7퍼센트_감소(self):
        assert apply_correction(0.5, "wrong") == pytest.approx(0.5 * 0.93)

    def test_outdated는_5퍼센트_감소(self):
        assert apply_correction(0.5, "outdated") == pytest.approx(0.5 * 0.95)

    def test_need_more_observation은_3퍼센트_감소(self):
        assert apply_correction(0.5, "need_more_observation") == pytest.approx(0.5 * 0.97)

    def test_0_5에서_wrong_2회로_0_3_미만이_아니다(self):
        """완료 기준: 2회 → 0.432"""
        s = apply_correction(apply_correction(0.5, "wrong"), "wrong")
        assert s >= 0.3
        assert s == pytest.approx(0.5 * 0.93**2)

    def test_0_5에서_wrong_8회면_0_3_미만(self):
        """완료 기준: 8회째 0.280"""
        s = 0.5
        for _ in range(8):
            s = apply_correction(s, "wrong")
        assert s < 0.3
        assert s == pytest.approx(0.5 * 0.93**8)

    def test_하한_클램프(self):
        s = 0.001
        result = apply_correction(s, "wrong")
        assert result >= STRENGTH_MIN

    def test_once_only는_profile_verdict가_아니다(self):
        with pytest.raises(ValueError, match="verdict"):
            apply_correction(0.5, "once_only")


# -- apply_transition --


class TestApplyTransition:
    def test_candidate에서_confirmed_승격(self):
        assert apply_transition(0.5, "candidate", "confirmed") == pytest.approx(0.55)

    def test_confirmed에서_candidate_강등(self):
        assert apply_transition(0.5, "confirmed", "candidate") == pytest.approx(0.45)

    def test_confirmed에서_archived_강등(self):
        assert apply_transition(0.5, "confirmed", "archived") == pytest.approx(0.45)

    def test_candidate에서_archived_강등(self):
        assert apply_transition(0.5, "candidate", "archived") == pytest.approx(0.45)

    def test_archived에서_candidate_부활은_보너스_없음(self):
        assert apply_transition(0.5, "archived", "candidate") == pytest.approx(0.5)

    def test_archived에서_confirmed_부활_승격(self):
        assert apply_transition(0.5, "archived", "confirmed") == pytest.approx(0.55)

    def test_같은_상태면_변화_없음(self):
        for state in ("candidate", "confirmed", "archived"):
            assert apply_transition(0.5, state, state) == 0.5

    def test_상한_클램프(self):
        assert apply_transition(0.95, "candidate", "confirmed") <= STRENGTH_MAX

    def test_하한_클램프(self):
        assert apply_transition(0.05, "confirmed", "archived") >= STRENGTH_MIN

    def test_승격_후_재계산해도_중복_적용_안_됨(self):
        s1 = apply_transition(0.5, "candidate", "confirmed")  # 0.55
        s2 = apply_transition(s1, "confirmed", "confirmed")  # 같은 상태 → 그대로
        assert s2 == s1


# -- 순수 함수 조합 시나리오 --


class TestScenario:
    TODAY = date(2026, 9, 26)

    def test_신규_profile_3회_관찰로_승격(self):
        status = compute_profile_status(
            obs_count=3,
            wrong_count=0,
            has_signal=False,
            last_observed_on=self.TODAY,
            today=self.TODAY,
        )
        strength = apply_transition(STRENGTH_DEFAULT, "candidate", status)
        assert status == "confirmed"
        assert strength == pytest.approx(0.55)

    def test_confirmed에서_wrong_받으면_W가_늘어_candidate(self):
        s = apply_correction(0.55, "wrong")  # 0.55 * 0.93
        status = compute_profile_status(
            obs_count=3,
            wrong_count=1,
            has_signal=False,
            last_observed_on=self.TODAY,
            today=self.TODAY,
        )
        s = apply_transition(s, "confirmed", status)  # 강등 * 0.90
        assert status == "candidate"
        assert s == pytest.approx(0.55 * 0.93 * 0.90)

    def test_윈도우_밖으로_관찰이_빠지면_자연_하강(self):
        """7일 윈도우 안 관찰이 3→2건으로 줄면 confirmed→candidate"""
        # 승격 중이었음
        assert (
            compute_profile_status(
                obs_count=3,
                wrong_count=0,
                has_signal=False,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "confirmed"
        )
        # 1건이 윈도우 밖으로 빠짐 → obs_count=2
        assert (
            compute_profile_status(
                obs_count=2,
                wrong_count=0,
                has_signal=False,
                last_observed_on=self.TODAY,
                today=self.TODAY,
            )
            == "candidate"
        )

    def test_21일_지나면_archived(self):
        last = self.TODAY - timedelta(days=22)
        status = compute_profile_status(
            obs_count=0,
            wrong_count=0,
            has_signal=False,
            last_observed_on=last,
            today=self.TODAY,
        )
        s = apply_transition(0.55, "confirmed", status)
        assert status == "archived"
        assert s == pytest.approx(0.55 * 0.90)

    def test_archived에서_새_관찰로_부활(self):
        """archived 상태에서 새 관찰 3건이 들어오면 confirmed"""
        status = compute_profile_status(
            obs_count=3,
            wrong_count=0,
            has_signal=False,
            last_observed_on=self.TODAY,
            today=self.TODAY,
        )
        s = apply_transition(0.4, "archived", status)
        assert status == "confirmed"
        assert s == pytest.approx(0.4 * 1.10)  # 부활+승격

    def test_correction_연속_적용(self):
        """wrong → outdated → need_more_observation 순서로 3연속"""
        s = STRENGTH_DEFAULT
        s = apply_correction(s, "wrong")  # * 0.93
        s = apply_correction(s, "outdated")  # * 0.95
        s = apply_correction(s, "need_more_observation")  # * 0.97
        assert s == pytest.approx(0.5 * 0.93 * 0.95 * 0.97)
        assert s > 0.3  # 3회로는 0.3 아래 안 감
