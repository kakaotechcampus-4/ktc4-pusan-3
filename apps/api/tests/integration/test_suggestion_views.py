"""05 응답을 만드는 읽기 (#249 · #323 최소분)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import ChildGrowthLog, ParentChildRelation
from app.domains.child.repository import create_child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationHealth,
    ObservationStatus,
)
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity
from app.domains.safety.labels import list_safety_labels
from app.domains.safety.models import HealthSafety, SafetyKind, SafetyStatus
from app.domains.suggestion import repository
from app.domains.suggestion.evidence_view import GROWTH_LOG_LABEL, HEALTH_LABEL, describe_evidence
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvidence,
    SuggestionKind,
    SuggestionStatus,
)

NOW = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
TODAY = date(2026, 10, 14)


@pytest.fixture
async def kid(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = await create_child(
        session,
        owner_parent_id=owner.id,
        nickname="첫째",
        birth_date=date(2023, 1, 1),
        relation=ParentChildRelation.MOTHER,
    )
    return owner, child


@pytest.fixture
async def other_kid(session):
    """보호자가 다른 아이. 한 보호자는 아이 하나만 갖는다(uq_parent_child_parent_id)."""
    stranger = Parent()
    session.add(stranger)
    await session.flush()
    return await create_child(
        session,
        owner_parent_id=stranger.id,
        nickname="다른 아이",
        birth_date=date(2024, 1, 1),
        relation=ParentChildRelation.FATHER,
    )


async def _food(session, child, writer, *, on: date, status=ObservationStatus.ACTIVE):
    row = ObservationFood(
        child_id=child.id,
        source_writer=writer.id,
        raw_text="계란말이를 다 먹었다",
        subject="계란말이",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(on, on + timedelta(days=1)),
        status=status,
    )
    session.add(row)
    await session.flush()
    return row


async def _suggestion(
    session,
    child,
    *,
    run_id="run-1",
    status=SuggestionStatus.DRAFT,
    agent=SuggestionAgent.FOOD,
    expires_at=None,
    kind=SuggestionKind.PERSONALIZED,
):
    row = Suggestion(
        child_id=child.id,
        agent=agent,
        kind=kind,
        content="계란말이",
        reason="r",
        status=status,
        expires_at=expires_at or NOW + timedelta(hours=24),
        run_id=run_id,
    )
    session.add(row)
    await session.flush()
    return row


async def _evidence(session, suggestion, kind, source_id, note="잘 먹음"):
    row = SuggestionEvidence(
        suggestion_id=suggestion.id, source_kind=kind, source_id=source_id, note=note
    )
    session.add(row)
    await session.flush()
    return row


async def test_관찰_근거는_대상_날짜_낡음을_싣는다(session, kid):
    owner, child = kid
    obs = await _food(session, child, owner, on=date(2026, 3, 1))
    s = await _suggestion(session, child)
    ev = await _evidence(session, s, "observation_food", obs.id)

    views = await describe_evidence(session, child_id=child.id, rows=[ev], today=TODAY)

    [view] = views[s.id]
    assert view.label == "계란말이"
    assert view.observed_to == date(2026, 3, 1)
    assert view.confidence_source == "parent_direct"
    assert view.is_stale is True


async def test_관심_근거는_마지막_관찰일로_판정한다(session, kid):
    _, child = kid
    aff = ProfileAffinity(
        child_id=child.id, merge_key="계란 반찬", domain=MemoryDomain.FOOD, last_observed_on=TODAY
    )
    session.add(aff)
    s = await _suggestion(session, child)
    await session.flush()
    ev = await _evidence(session, s, "profile_affinity", aff.id, "자주 찾음")

    [view] = (await describe_evidence(session, child_id=child.id, rows=[ev], today=TODAY))[s.id]

    assert (view.label, view.is_stale, view.confidence_source) == ("계란 반찬", False, "")


async def test_성장_기록_근거는_숫자_없는_이름으로_싣는다(session, kid):
    """키 · 몸무게 숫자를 근거 이름에 싣지 않는다 — 평가처럼 읽히는 문장을 안 만든다 (루트 §2)."""
    _, child = kid
    log = ChildGrowthLog(child_id=child.id, measured_on=TODAY, height_cm=Decimal("95.5"))
    session.add(log)
    s = await _suggestion(session, child)
    await session.flush()
    ev = await _evidence(session, s, "child_growth_log", log.id, "최근에 쟀어요")

    [view] = (await describe_evidence(session, child_id=child.id, rows=[ev], today=TODAY))[s.id]

    assert view.label == GROWTH_LOG_LABEL
    assert "95" not in view.label
    assert view.observed_to == TODAY


async def test_건강_관찰_근거는_내용_없이_고정_이름이다(session, kid):
    """건강 기록의 문장(증상 · 원문)을 추천 근거 화면에 되풀이하지 않는다."""
    owner, child = kid
    row = ObservationHealth(
        child_id=child.id,
        source_writer=owner.id,
        raw_text="어제부터 기침을 해요",
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        observed_range=Range(TODAY, TODAY + timedelta(days=1)),
        symptom=["기침"],
    )
    session.add(row)
    s = await _suggestion(session, child)
    await session.flush()
    ev = await _evidence(session, s, "observation_health", row.id, "기록이 있어요")

    [view] = (await describe_evidence(session, child_id=child.id, rows=[ev], today=TODAY))[s.id]

    assert view.label == HEALTH_LABEL
    assert "기침" not in view.label


async def test_지운_관찰은_근거에서_빠진다(session, kid):
    """보호자가 지운 기록의 내용을 추천 근거로 다시 보여 주지 않는다."""
    owner, child = kid
    obs = await _food(session, child, owner, on=TODAY, status=ObservationStatus.DELETED)
    s = await _suggestion(session, child)
    ev = await _evidence(session, s, "observation_food", obs.id)

    assert (await describe_evidence(session, child_id=child.id, rows=[ev], today=TODAY)) == {
        s.id: []
    }


async def test_다른_아이의_관찰은_근거로_읽지_않는다(session, kid, other_kid):
    owner, child = kid
    other = other_kid
    obs = await _food(session, other, owner, on=TODAY)
    s = await _suggestion(session, child)
    ev = await _evidence(session, s, "observation_food", obs.id)

    assert (await describe_evidence(session, child_id=child.id, rows=[ev], today=TODAY)) == {
        s.id: []
    }


async def test_run_추천은_채택한_것과_만료_전_draft(session, kid):
    _, child = kid
    keep = await _suggestion(session, child)
    approved = await _suggestion(
        session, child, status=SuggestionStatus.APPROVED, expires_at=NOW - timedelta(hours=1)
    )
    await _suggestion(session, child, expires_at=NOW - timedelta(minutes=1))  # 만료된 draft
    await _suggestion(session, child, status=SuggestionStatus.REJECTED)
    await _suggestion(session, child, run_id="other")
    await _suggestion(session, child, agent=SuggestionAgent.ACTIVITY)

    rows = await repository.list_run_suggestions(
        session, child_id=child.id, run_id="run-1", agent="food", now=NOW
    )

    assert {r.id for r in rows} == {keep.id, approved.id}


async def test_id_로_읽으면_저장_순서를_지키고_다른_아이는_섞이지_않는다(session, kid, other_kid):
    owner, child = kid
    other = other_kid
    first = await _suggestion(session, child)
    second = await _suggestion(session, child)
    foreign = await _suggestion(session, other)

    rows = await repository.list_suggestions_by_ids(
        session, child_id=child.id, ids=[second.id, foreign.id, first.id]
    )

    assert [r.id for r in rows] == [second.id, first.id]


async def test_기록_건수는_남아_있는_관찰만(session, kid):
    owner, child = kid
    await _food(session, child, owner, on=TODAY)
    await _food(session, child, owner, on=TODAY, status=ObservationStatus.STAND_ALONE)
    await _food(session, child, owner, on=TODAY, status=ObservationStatus.INACTIVE)
    await _food(session, child, owner, on=TODAY, status=ObservationStatus.DELETED)

    count = await repository.count_child_records(
        session, child_id=child.id, kinds=["observation_food"]
    )

    assert count == 2


async def test_문서_근거는_아이_기록_근거와_따로_읽는다(session, kid):
    owner, child = kid
    obs = await _food(session, child, owner, on=TODAY)
    s = await _suggestion(session, child)
    await _evidence(session, s, "observation_food", obs.id)
    doc = await _evidence(session, s, "food_doc", obs.id, "질감 안내")

    found = await repository.list_general_evidence_batch(session, suggestion_ids=[s.id])

    assert [(row.source_kind, row.note) for row in found[s.id]] == [(doc.source_kind, "질감 안내")]


async def test_사전검사용_행은_모든_status(session, kid):
    _, child = kid
    for label, status in (("우유", SafetyStatus.ACTIVE), ("밀", SafetyStatus.RETRACTED)):
        session.add(
            HealthSafety(child_id=child.id, kind=SafetyKind.ALLERGY, label=label, status=status)
        )
    await session.flush()

    assert sorted(await list_safety_labels(session, child_id=child.id)) == [
        ("allergy", "밀", "retracted"),
        ("allergy", "우유", "active"),
    ]
