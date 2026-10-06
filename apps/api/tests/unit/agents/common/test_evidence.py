"""근거 티어 정렬.

docs/agents/shared/Tool_공통.md §4 의 표를 그대로 옮긴다.

**전제: `profile_affinity` 는 이미 만들어져 있다.** Curator(백엔드 배치)가 관찰을 승격해
둔 결과를 Agent 가 읽는 자리다. 시드는 그 상태를 그대로 넣고, 감쇠·승격이 제대로 도는지는
여기서 보지 않는다 — Curator 쪽 테스트의 몫이다.
"""

from datetime import date, timedelta
from uuid import uuid4

from app.agents.common.evidence import (
    OBSERVATION_WINDOW_DAYS,
    AffinityRow,
    ObservationRow,
    pick_followup,
    rank_evidence,
)

TODAY = date(2026, 9, 22)


def affinity(*, state="confirmed", polarity=1, strength=0.8, days_ago=3, label="당근"):
    return AffinityRow(
        id=uuid4(),
        merge_key=label,
        state=state,
        polarity=polarity,
        strength=strength,
        last_observed_on=TODAY - timedelta(days=days_ago),
    )


def observation(*, polarity=1, days_ago=3, kind="observation_food", subject="당근"):
    return ObservationRow(
        id=uuid4(),
        kind=kind,
        subject=subject,
        polarity=polarity,
        observed_on=TODAY - timedelta(days=days_ago),
    )


def rank(affinities=(), observations=(), **kwargs):
    return rank_evidence(tuple(affinities), tuple(observations), today=TODAY, **kwargs)


class TestTiers:
    def test_confirmed_는_티어1(self):
        assert rank([affinity(state="confirmed")])[0].tier == 1

    def test_candidate_는_임계_이상일_때만_티어2(self):
        assert rank([affinity(state="candidate", strength=0.6)])[0].tier == 2
        assert rank([affinity(state="candidate", strength=0.4)]) == ()

    def test_archived_는_빠진다(self):
        assert rank([affinity(state="archived")]) == ()

    def test_관찰은_티어3(self):
        assert rank(observations=[observation()])[0].tier == 3

    def test_티어_순으로_정렬된다(self):
        result = rank(
            [affinity(state="candidate", strength=0.9, label="B"), affinity(label="A")],
            [observation(subject="C")],
        )
        assert [item.tier for item in result] == [1, 2, 3]
        assert [item.label for item in result] == ["A", "B", "C"]

    def test_같은_티어는_최근순(self):
        result = rank([affinity(days_ago=10, label="옛것"), affinity(days_ago=1, label="새것")])
        assert [item.label for item in result] == ["새것", "옛것"]


class TestPolarity:
    def test_기피도_근거다(self):
        """제외 필터가 아니다. 후보에서 지우는 것은 안전 필터의 일이다."""
        result = rank([affinity(polarity=-1, label="브로콜리")])
        assert len(result) == 1
        assert result[0].is_avoidance is True

    def test_polarity_NULL_인_candidate_는_근거가_아니다(self):
        assert rank([affinity(state="candidate", polarity=None, strength=0.9)]) == ()

    def test_polarity_NULL_인_confirmed_도_빠진다(self):
        assert rank([affinity(state="confirmed", polarity=None)]) == ()

    def test_polarity_0_관찰도_쓴다(self):
        """관찰 티어는 polarity 무관이다. 반응이 딱히 없던 것도 근거다."""
        assert len(rank(observations=[observation(polarity=0)])) == 1

    def test_기피_관찰만_있어도_근거_0행이_아니다(self):
        assert len(rank(observations=[observation(polarity=-1)])) == 1


class TestObservationWindow:
    def test_14일_안쪽만_본다(self):
        inside = observation(days_ago=OBSERVATION_WINDOW_DAYS)
        outside = observation(days_ago=OBSERVATION_WINDOW_DAYS + 1)
        assert len(rank(observations=[inside])) == 1
        assert rank(observations=[outside]) == ()


class TestDecayIsCuratorsJob:
    """감쇠는 Curator 가 이미 반영해 둔다. Agent 는 그 결과만 읽는다."""

    def test_archived_면_아무리_최근이어도_안_쓴다(self):
        assert rank([affinity(state="archived", days_ago=0)]) == ()

    def test_strength_가_내려가_있으면_티어2에_못_든다(self):
        """Curator 가 감쇠로 strength 를 내렸다면 그것으로 충분하다."""
        assert rank([affinity(state="candidate", strength=0.2)]) == ()

    def test_오래돼도_confirmed_면_근거다(self):
        """유예일을 Agent 가 다시 세지 않는다.

        걸러야 했다면 Curator 가 archived 로 바꿨거나 strength 를 내렸을 것이다.
        """
        result = rank([affinity(state="confirmed", days_ago=400)])
        assert len(result) == 1
        assert result[0].tier == 1


class TestFollowup:
    def test_polarity_를_모르는_candidate_를_집는다(self):
        row = affinity(state="candidate", polarity=None)
        assert pick_followup((row,)) is row

    def test_신호가_가장_센_하나만(self):
        weak = affinity(state="candidate", polarity=None, strength=0.3, label="약")
        strong = affinity(state="candidate", polarity=None, strength=0.9, label="강")
        assert pick_followup((weak, strong)).merge_key == "강"

    def test_없으면_None(self):
        assert pick_followup((affinity(),)) is None
        assert pick_followup(()) is None
