"""기억 상태를 다시 계산하는 일 단위 배치 (#636)

공식 자체는 test_profile_transition 이 본다. 여기서는 **새 기록 없이 날짜만 지났을 때** 배치가
저장된 상태를 맞추는지, 커밋까지 하는지, 건드리면 안 되는 것을 건드리지 않는지를 본다.

세션은 바깥 트랜잭션 안의 savepoint 다 (tests/conftest.py) — run_recompute 뒤 rollback 을 불러도
값이 남아 있으면 커밋된 것이다 (test_cleanup_batch 와 같은 방식).
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.correction.models import Correction, CorrectionTargetKind, CorrectionVerdict
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ConfidenceSource, ObservationFood
from app.domains.memory.profile.models import MemoryDomain, ProfileAffinity, ProfileState
from app.rules.profile import STRENGTH_DEFAULT
from app.workers import profile_recompute as batch
from app.workers.profile_recompute import _recompute_child, run_recompute

TODAY = date(2026, 9, 26)


@pytest.fixture
async def family(session):
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return owner, child


async def _profile(session, child_id, *, state, observed_days_ago, merge_key="사과::p1"):
    """`observed_days_ago` 날마다 active 관찰 1건을 묶은 기억. 마지막 관찰일은 가장 최근 날."""
    profile = ProfileAffinity(
        child_id=child_id,
        merge_key=merge_key,
        domain=MemoryDomain.FOOD,
        state=ProfileState(state),
        polarity=1,
        strength=STRENGTH_DEFAULT,
        last_observed_on=TODAY - timedelta(days=min(observed_days_ago)),
    )
    session.add(profile)
    await session.flush()
    for ago in observed_days_ago:
        day = TODAY - timedelta(days=ago)
        session.add(
            ObservationFood(
                child_id=child_id,
                raw_text="test",
                subject="사과",
                polarity=1,
                confidence_source=ConfidenceSource.PARENT_DIRECT,
                observed_range=Range(day, day + timedelta(days=1)),
                affinity_id=profile.id,
            )
        )
    await session.flush()
    return profile.id


async def _child(session, owner, nickname="test"):
    child = Child(owner_parent_id=owner.id, nickname=nickname, birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return child


async def _wrong(session, owner, child, profile_id, *, days_ago):
    """`days_ago` 날 UTC 0시(= KST 9시)에 남긴 기억 wrong. 창은 KST 0시부터 센다."""
    session.add(
        Correction(
            child_id=child.id,
            target_kind=CorrectionTargetKind.PROFILE_AFFINITY,
            target_id=profile_id,
            verdict=CorrectionVerdict.WRONG,
            created_by=owner.id,
            created_at=datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time(), UTC),
        )
    )
    await session.flush()


async def _state(session, profile_id):
    row = (
        await session.execute(
            select(ProfileAffinity.state, ProfileAffinity.strength).where(
                ProfileAffinity.id == profile_id
            )
        )
    ).one()
    return row.state, row.strength


async def test_14일_창을_벗어나면_확인됨이_후보로_내려가고_커밋된다(session, family):
    _, child = family
    # 저장할 때는 사흘 관찰로 confirmed. 오늘 기준 14일 창 안에는 하루(13일 전)만 남았다
    pid = await _profile(session, child.id, state="confirmed", observed_days_ago=[15, 14, 13])

    result = await run_recompute(session, today=TODAY)
    await session.rollback()

    # 건수는 개발 DB 에 이미 있는 기억도 센다 (테스트가 같은 DB 를 쓴다) — 이 행의 값만 본다
    assert result.changed >= 1 and result.failed_children == 0
    assert await _state(session, pid) == (
        ProfileState.CANDIDATE,
        pytest.approx(STRENGTH_DEFAULT * 0.90),
    )


async def test_마지막_관찰_뒤_21일이_지나면_archived(session, family):
    _, child = family
    pid = await _profile(session, child.id, state="confirmed", observed_days_ago=[24, 23, 22])

    await run_recompute(session, today=TODAY)

    assert (await _state(session, pid))[0] == ProfileState.ARCHIVED


async def test_wrong_이_21일_창을_벗어나면_후보가_다시_확인됨(session, family):
    owner, child = family
    pid = await _profile(session, child.id, state="candidate", observed_days_ago=[2, 1, 0])
    # 21일 전 wrong — 창(오늘 포함 21일) 바로 밖. 창 안이면 O=3 < 3+1 이라 후보에 머문다
    await _wrong(session, owner, child, pid, days_ago=21)

    await run_recompute(session, today=TODAY)

    assert (await _state(session, pid))[0] == ProfileState.CONFIRMED


async def test_같은_날_두_번_돌려도_같고_두_번째는_바뀐_것이_없다(session, family):
    _, child = family
    pid = await _profile(session, child.id, state="confirmed", observed_days_ago=[15, 14, 13])

    await run_recompute(session, today=TODAY)
    first = await _state(session, pid)
    second_run = await run_recompute(session, today=TODAY)

    assert await _state(session, pid) == first
    assert second_run.changed == 0


async def test_archived_와_지워진_아이의_기억은_대상이_아니다(session, family):
    owner, child = family
    archived = await _profile(session, child.id, state="archived", observed_days_ago=[2, 1, 0])
    gone = Child(
        owner_parent_id=owner.id,
        nickname="gone",
        birth_date=date(2023, 1, 1),
        deleted_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    session.add(gone)
    await session.flush()
    gone_pid = await _profile(session, gone.id, state="confirmed", observed_days_ago=[15, 14, 13])

    await run_recompute(session, today=TODAY)

    # 관찰 사흘이 있어도 archived 는 시간만으로 되살리지 않는다 — 새 관찰 때 Curator 몫
    assert (await _state(session, archived))[0] == ProfileState.ARCHIVED
    assert (await _state(session, gone_pid))[0] == ProfileState.CONFIRMED


# ---------------------------------------------------------------------------
# 경계 — 한 끗 차이로 결과가 갈리는 쌍. 창 계산이 하루 밀리면 둘 중 하나가 깨진다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("observed_days_ago", "expected"),
    [
        ([13, 12, 11], ProfileState.CONFIRMED),  # 14일 창(오늘 포함) 안에 사흘 — 그대로
        ([14, 13, 12], ProfileState.CANDIDATE),  # 가장 오래된 하루가 막 빠졌다
    ],
)
async def test_14일_창_경계(session, family, observed_days_ago, expected):
    _, child = family
    pid = await _profile(session, child.id, state="confirmed", observed_days_ago=observed_days_ago)

    await run_recompute(session, today=TODAY)

    assert (await _state(session, pid))[0] == expected


@pytest.mark.parametrize(
    ("last_days_ago", "expected"),
    [
        (21, ProfileState.CANDIDATE),  # 21일째는 아직 보관하지 않는다 (> 21)
        (22, ProfileState.ARCHIVED),
    ],
)
async def test_21일_무관찰_경계(session, family, last_days_ago, expected):
    _, child = family
    pid = await _profile(session, child.id, state="confirmed", observed_days_ago=[last_days_ago])

    await run_recompute(session, today=TODAY)

    assert (await _state(session, pid))[0] == expected


async def test_wrong_이_21일_창_안이면_후보에_머문다(session, family):
    """재승격 테스트의 짝 — 이게 없으면 wrong 을 아예 안 세도 재승격 테스트가 통과한다."""
    owner, child = family
    pid = await _profile(session, child.id, state="candidate", observed_days_ago=[2, 1, 0])
    await _wrong(session, owner, child, pid, days_ago=19)

    await run_recompute(session, today=TODAY)

    assert (await _state(session, pid))[0] == ProfileState.CANDIDATE


# ---------------------------------------------------------------------------
# 실패 · 로그
# ---------------------------------------------------------------------------


async def test_한_아이가_실패해도_다른_아이는_반영되고_ERROR_가_남는다(
    session, family, monkeypatch, caplog
):
    owner, ok_child = family
    bad_child = await _child(session, owner, nickname="bad")
    bad_child_id = bad_child.id  # 롤백 뒤에는 객체가 만료돼 속성을 읽으면 DB 를 다시 부른다
    ok_pid = await _profile(session, ok_child.id, state="confirmed", observed_days_ago=[15, 14, 13])
    bad_pid = await _profile(
        session,
        bad_child.id,
        state="confirmed",
        observed_days_ago=[15, 14, 13],
        merge_key="비밀이름",
    )

    real = batch.recompute_profile

    async def fail_for_bad(session, *, profile_id, today):
        result = await real(session, profile_id=profile_id, today=today)
        if profile_id == bad_pid:
            # 상태를 바꿔 flush 한 **뒤에** 실패 — 롤백이 없으면 반쯤 바뀐 값이 세션에 남아
            # 다음 아이의 커밋에 같이 실린다
            raise RuntimeError("boom")
        return result

    monkeypatch.setattr(batch, "recompute_profile", fail_for_bad)

    with caplog.at_level(logging.INFO, logger="app.workers.profile_recompute"):
        result = await run_recompute(session, today=TODAY)

    assert result.failed_children == 1
    assert (await _state(session, ok_pid))[0] == ProfileState.CANDIDATE
    assert (await _state(session, bad_pid))[0] == ProfileState.CONFIRMED  # 롤백돼 그대로
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    # 🚨 로그에는 건수만 — 아이 · 기억 id 와 기억 이름(보호자가 쓴 말)은 남기지 않는다
    for leaked in (str(bad_pid), str(bad_child_id), str(ok_pid), "비밀이름"):
        assert leaked not in caplog.text


async def test_잠금을_기다리는_사이_지워진_기억은_건너뛰고_세지_않는다(session, family):
    """대상을 고른 뒤 · 아이를 잠그기 전에 기억이 지워질 수 있다 (Correction · 아이 삭제)."""
    _, child = family
    pid = await _profile(session, child.id, state="confirmed", observed_days_ago=[15, 14, 13])

    done = await _recompute_child(session, child.id, [uuid.uuid4(), pid], today=TODAY)

    assert done == (1, 1)  # (다시 계산한 수, 바뀐 수) — 없는 기억은 둘 다에서 빠진다
