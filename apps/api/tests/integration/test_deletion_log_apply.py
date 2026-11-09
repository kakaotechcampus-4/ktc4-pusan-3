"""삭제 이력 재적용 (#758) — 복원으로 되살아난 것을 다시 지우고, 이상한 이력은 아무것도 안 바꾼다.

복원은 테스트 안에서 흉내 낸다 — 지운 행을 같은 id 로 다시 넣고, 지운 상태를 살아 있는 상태로
돌리고, 백업 뒤에 생긴 이력을 지운 뒤(백업에 남아 있던 모습) 빼 둔 이력을 적용한다.
"""

import io
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.consent.models import Consent, ConsentAction, ConsentRetention, ConsentScope
from app.domains.consent.retention import purge_child
from app.domains.deletion_log.models import DeletionLog
from app.domains.identity.models import AuthSession, Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
)
from app.domains.policy.models import PolicyVersion
from app.domains.safety.models import HealthSafety, SafetyKind, SafetyStatus
from app.domains.safety.repository import (
    create_safety,
    find_active_safety_for_update,
    retract_safety,
    revise_safety,
)
from scripts.deletion_log import (
    BadEntry,
    Entry,
    apply_entries,
    dump_entries,
    export_entries,
    read_entries,
    reserve_ids,
)

TODAY = date(2026, 11, 1)


def _food(child_id: uuid.UUID, food_id: uuid.UUID | None = None) -> ObservationFood:
    return ObservationFood(
        id=food_id,
        child_id=child_id,
        raw_text="test",
        subject="사과",
        polarity=1,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        status=ObservationStatus.ACTIVE,
        observed_range=Range(TODAY, TODAY + timedelta(days=1)),
    )


async def _mine(session, since_id: int) -> list[Entry]:
    """이 테스트가 남긴 이력만 — 다른 테스트가 커밋한 이력이 섞이지 않게. 파일 왕복도 거친다."""
    entries = [e for e in await export_entries(session) if e.id > since_id]
    buf = io.StringIO()
    dump_entries(entries, buf)
    return list(read_entries(buf.getvalue().splitlines()))


async def test_복원으로_되살아난_것을_다시_지우고_로그인을_비운다(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    food = _food(child.id)
    safety = HealthSafety(child_id=child.id, kind=SafetyKind.ALLERGY, label="우유")
    login = AuthSession(
        parent_id=parent.id,
        refresh_hash=uuid.uuid4().bytes.ljust(32, b"-"),
        refresh_expires_at=func.now() + timedelta(days=1),
        absolute_expires_at=func.now() + timedelta(days=60),
    )
    session.add_all([food, safety, login])
    await session.flush()
    food_id, safety_id, login_id = food.id, safety.id, login.id
    since = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0)))

    # 보호자가 지운다 — 기록 하나는 행째, 알레르기는 회수(상태만, 커밋 때 남는다)
    await session.execute(delete(ObservationFood).where(ObservationFood.id == food_id))
    await retract_safety(session, child_id=child.id, safety_id=safety_id, parent_id=parent.id)
    await _commit_point(session)
    entries = await _mine(session, since)
    assert [(e.table, e.action) for e in entries] == [
        ("observation_food", "delete"),
        ("health_safety", "status"),
    ]

    # 그 전 백업을 복원한 모습 — 지운 기록이 있고 알레르기가 살아 있고, 그 뒤의 이력은 없다
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    session.add(_food(child.id, food_id))
    await session.execute(
        update(HealthSafety).where(HealthSafety.id == safety_id).values(status=SafetyStatus.ACTIVE)
    )
    await session.flush()
    session.expunge_all()

    applied = await apply_entries(session, entries)

    assert applied.counts == {"observation_food delete": 1, "health_safety status": 1}
    assert applied.safety_children == {str(child.id)}
    assert await session.get(ObservationFood, food_id) is None
    assert (await session.get(HealthSafety, safety_id)).status == SafetyStatus.RETRACTED
    assert await session.get(AuthSession, login_id) is None  # 로그인은 통째로 비운다
    # 원래 이력이 id · 시각 그대로 옮겨지고, 다시 한 동작은 이력을 또 남기지 않는다(커밋 때 도는
    # 알레르기 회수 트리거까지)
    assert await _mine(session, since) == entries
    # 몇 번 돌려도 같다 — 옮긴 이력은 이제 복원본에 있다
    assert (await apply_entries(session, entries)).counts == {}


async def test_다른_백업으로_또_복원해도_지운_것이_되살아나지_않는다(session):
    """백업 A → 기록 생성 → 백업 B → 기록 삭제. A 로 복원하면 기록이 없어 다시 지울 것이 없지만,
    그 이력은 복원본에 남아야 한다 — 이력 파일을 파기한 뒤 B 로 또 복원해도 그 기록을 지우게."""
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    since = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0)))  # 백업 A
    food = _food(child.id)
    session.add(food)
    await session.flush()
    food_id = food.id  # 백업 B 에 들어 있다
    await session.execute(delete(ObservationFood).where(ObservationFood.id == food_id))
    first = await _mine(session, since)

    # A 로 복원 — 기록도, 그 뒤의 이력도 없다
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    session.expunge_all()
    assert (await apply_entries(session, first)).counts == {}  # 지울 것이 없다

    # 파일을 파기하고 B 로 복원 — 지금 DB 에서 이력을 다시 뺀다. B 에는 기록이 있고 뒤 이력은 없다
    second = await _mine(session, since)
    assert second == first
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    session.add(_food(child.id, food_id))
    await session.flush()
    session.expunge_all()

    assert (await apply_entries(session, second)).counts == {"observation_food delete": 1}
    assert await session.get(ObservationFood, food_id) is None


@pytest.mark.parametrize(
    "entry",
    [
        Entry(1, "place", {"id": str(uuid.uuid4())}, "delete", None),  # 이력 대상이 아닌 표
        Entry(1, "no_such_table", {"id": str(uuid.uuid4())}, "delete", None),
        Entry(1, "parent", {"id": "x", "extra": "y"}, "delete", None),  # 기본 키와 다른 칸
        Entry(1, "observation_food", {"id": str(uuid.uuid4())}, "status", {"raw_text": "x"}),
        Entry(1, "observation_food", {"id": str(uuid.uuid4())}, "status", {"status": "active"}),
        Entry(1, "parent", {"id": str(uuid.uuid4())}, "drop", None),
        Entry(1, "parent", {"id": "x"}, "insert", {"id": "x"}),  # 다시 넣을 수 없는 표
        Entry(1, "consent", {"id": "x"}, "insert", {"id": "y", "action": "withdrawn"}),
        Entry(1, "consent", {"id": "x"}, "insert", {"id": "x", "action": "withdrawn", "z": 1}),
        Entry(1, "parent", {"id": "x"}, "delete", None, ("service_terms", "v1")),  # 약관 버전
    ],
)
async def test_이상한_이력이_하나라도_있으면_아무것도_바꾸지_않는다(session, entry):
    parent = Parent()
    session.add(parent)
    await session.flush()
    good = Entry(0, "parent", {"id": str(parent.id)}, "delete", None, deleted_at=datetime.now(UTC))

    with pytest.raises(BadEntry):
        await apply_entries(session, [good, entry])

    assert await session.get(Parent, parent.id) is not None


async def test_복원본에_이미_있는_이력은_건너뛴다(session):
    """백업 전에 지웠다가 같은 키로 다시 만든 행 — 백업에 들어 있는 새 행을 지우면 안 된다."""
    parent = Parent()
    session.add(parent)
    await session.flush()
    # 복원한 DB 의 이력에 이미 있는 줄 — 그 삭제는 백업에 반영돼 있다
    log = DeletionLog(table_name="parent", row_key={"id": str(parent.id)})
    session.add(log)
    await session.flush()

    await session.refresh(log)
    entry = Entry(log.id, "parent", log.row_key, "delete", None, deleted_at=log.deleted_at)
    applied = await apply_entries(session, [entry])

    assert applied.counts == {}
    assert await session.get(Parent, parent.id) is not None


async def _commit_point(session) -> None:
    """알레르기 회수 트리거는 커밋 때 돈다. 테스트는 커밋하지 않으니 여기서 그 시점을 당긴다."""
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


async def test_고친_뒤_지운_알레르기는_백업_속_옛_버전도_회수한다(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    first = await create_safety(
        session, child_id=child.id, parent_id=parent.id, kind=SafetyKind.ALLERGY, label="땅콩"
    )
    first_id = first.id
    since = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0)))
    current = await find_active_safety_for_update(session, child_id=child.id, safety_id=first_id)
    second = await revise_safety(
        session, current=current, parent_id=parent.id, severity=None, reactions=[], notes=None
    )
    await _commit_point(session)
    await retract_safety(session, child_id=child.id, safety_id=second.id, parent_id=parent.id)
    await _commit_point(session)
    entries = await _mine(session, since)

    # 백업에는 고치기 전 버전이 살아 있고, 고친 버전은 없다
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    await session.execute(delete(HealthSafety).where(HealthSafety.id == second.id))
    await session.execute(
        update(HealthSafety).where(HealthSafety.id == first_id).values(status=SafetyStatus.ACTIVE)
    )
    session.expunge_all()

    applied = await apply_entries(session, entries)

    assert (await session.get(HealthSafety, first_id)).status == SafetyStatus.RETRACTED
    assert applied.safety_children == {str(child.id)}


async def _withdrawal(session, parent: Parent, actor: Parent | None = None) -> Consent:
    policy_version_id = await session.scalar(
        select(PolicyVersion.id).where(PolicyVersion.scope == ConsentScope.SERVICE_TERMS).limit(1)
    )
    row = Consent(
        subject_parent_id=parent.id,
        actor_parent_id=(actor or parent).id,
        actor_ref=(actor or parent).id,
        scope=ConsentScope.SERVICE_TERMS,
        action=ConsentAction.WITHDRAWN,
        policy_version_id=policy_version_id,
    )
    session.add(row)
    await session.flush()
    return row


async def test_복원으로_사라진_동의_철회를_다시_넣는다(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    since = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0)))
    withdrawal_id = (await _withdrawal(session, parent)).id
    entries = await _mine(session, since)

    # 백업에는 철회 행이 없다
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    await session.execute(delete(Consent).where(Consent.id == withdrawal_id))
    session.expunge_all()

    applied = await apply_entries(session, entries)

    assert applied.counts == {"consent insert": 1}
    assert (await session.get(Consent, withdrawal_id)).action == ConsentAction.WITHDRAWN


async def test_복원_뒤에_아이를_다시_지우면서_동의_증빙도_되살린다(session):
    """아이 삭제는 동의를 증빙으로 복사하고 원본을 지운다. 백업에는 원본만 있다 — 재적용이 원본을
    다시 지우는데 증빙을 되살리지 않으면 1년 보관해야 할 증빙이 사라진다."""
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    consent = Consent(
        child_id=child.id,
        actor_parent_id=parent.id,
        actor_ref=parent.id,
        scope=ConsentScope.CHILD_HEALTH,
        action=ConsentAction.GRANTED,
        policy_version_id=await session.scalar(
            select(PolicyVersion.id)
            .where(PolicyVersion.scope == ConsentScope.CHILD_HEALTH)
            .limit(1)
        ),
    )
    session.add(consent)
    await session.flush()
    backup = {c.name: getattr(consent, c.key) for c in Consent.__table__.columns}
    child_id = child.id
    since = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0)))
    await purge_child(session, child_id=child_id, now=datetime.now(UTC))
    entries = await _mine(session, since)
    [retention] = (
        await session.scalars(
            select(ConsentRetention).where(ConsentRetention.child_ref == child_id)
        )
    ).all()
    retention_id, kept = retention.id, (retention.retained_at, retention.purge_at)

    # 복원본 — 아이와 동의 원본이 있고 증빙은 없다
    await session.execute(delete(ConsentRetention).where(ConsentRetention.id == retention_id))
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    session.add(
        Child(id=child_id, owner_parent_id=parent.id, nickname="test", birth_date=date(2023, 1, 1))
    )
    await session.flush()
    await session.execute(Consent.__table__.insert().values(**backup))
    session.expunge_all()

    applied = await apply_entries(session, entries)

    assert applied.counts == {"consent_retention insert": 1, "consent delete": 1, "child delete": 1}
    assert await session.get(Child, child_id) is None
    restored = await session.get(ConsentRetention, retention_id)
    assert restored.source_consent_id == backup["id"]
    assert (restored.retained_at, restored.purge_at) == kept  # 1년 기한이 다시 시작되지 않는다
    assert (await apply_entries(session, entries)).counts == {}


async def test_백업_뒤에_생긴_약관_버전의_증빙은_버전_이름으로_다시_찾아_넣는다(session):
    """약관 버전 id 는 그 판을 등록하는 마이그레이션이 돌 때 새로 만들어진다. 그보다 앞선 백업을
    복원하면 upgrade heads 가 같은 판을 다른 id 로 다시 등록한다. 그 사이 새 판에 동의한 아이를
    지웠다면 — 아이도 동의도 복원본에 없지만, 증빙은 새 id 로 다시 들어가야 한다."""

    def version() -> PolicyVersion:
        return PolicyVersion(
            scope=ConsentScope.CHILD_HEALTH,
            version="test-remap",
            content="test",
            content_hash="test",
            label="test",
            effective_at=datetime.now(UTC),
        )

    parent = Parent()
    session.add(parent)
    await session.flush()
    since = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0)))
    old = version()
    child = Child(owner_parent_id=parent.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add_all([old, child])
    await session.flush()
    session.add(
        Consent(
            child_id=child.id,
            actor_parent_id=parent.id,
            actor_ref=parent.id,
            scope=ConsentScope.CHILD_HEALTH,
            action=ConsentAction.GRANTED,
            policy_version_id=old.id,
        )
    )
    await session.flush()
    child_id = child.id
    await purge_child(session, child_id=child_id, now=datetime.now(UTC))
    entries = await _mine(session, since)

    # 복원본 — 아이 · 동의 · 증빙이 없고, 같은 판이 다른 id 로 다시 등록돼 있다
    await session.execute(delete(ConsentRetention).where(ConsentRetention.child_ref == child_id))
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    await session.execute(delete(PolicyVersion).where(PolicyVersion.id == old.id))
    new = version()
    session.add(new)
    await session.flush()
    new_id = new.id
    session.expunge_all()

    applied = await apply_entries(session, entries)

    assert applied.counts == {"consent_retention insert": 1}
    [retention] = (
        await session.scalars(
            select(ConsentRetention).where(ConsentRetention.child_ref == child_id)
        )
    ).all()
    assert retention.policy_version_id == new_id


async def test_철회한_보호자가_복원본에_없으면_건너뛴다(session):
    """백업 뒤에 가입했다가 철회한 보호자 — 복원본에는 그 사람도, 그 사람의 동의도 없다."""
    parent = Parent()
    session.add(parent)
    await session.flush()
    since = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0)))
    await _withdrawal(session, parent)
    entries = [e for e in await _mine(session, since) if e.table == "consent"]

    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    await session.execute(delete(Consent).where(Consent.subject_parent_id == parent.id))
    await session.execute(delete(Parent).where(Parent.id == parent.id))
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    session.expunge_all()

    applied = await apply_entries(session, entries)

    assert applied.counts == {}
    assert applied.skipped == {"consent insert": 1}


async def test_이력의_보호자가_복원본에_없으면_그_칸만_비우고_적용한다(session):
    """백업 뒤에 들어온 보호자가 알레르기를 지우거나 철회를 대신한 경우 — 그 보호자는 복원본에 없다.
    고친 보호자 · 한 보호자 칸은 SET NULL 이라 비워서 적용한다(탈퇴한 것과 같은 모습)."""
    owner, newcomer = Parent(), Parent()
    session.add_all([owner, newcomer])
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    safety = await create_safety(
        session, child_id=child.id, parent_id=owner.id, kind=SafetyKind.ALLERGY, label="우유"
    )
    safety_id, newcomer_id = safety.id, newcomer.id
    since = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0)))
    await retract_safety(session, child_id=child.id, safety_id=safety_id, parent_id=newcomer_id)
    await _commit_point(session)
    withdrawal_id = (await _withdrawal(session, owner, actor=newcomer)).id
    entries = await _mine(session, since)

    # 복원본 — 새로 들어온 보호자와 그 뒤의 일이 없다
    await session.execute(delete(Consent).where(Consent.id == withdrawal_id))
    await session.execute(
        update(HealthSafety).where(HealthSafety.id == safety_id).values(status=SafetyStatus.ACTIVE)
    )
    await session.execute(delete(Parent).where(Parent.id == newcomer_id))
    await session.execute(delete(DeletionLog).where(DeletionLog.id > since))
    session.expunge_all()

    applied = await apply_entries(session, entries)

    assert applied.counts == {"health_safety status": 1, "consent insert": 1}
    retracted = await session.get(HealthSafety, safety_id)
    assert (retracted.status, retracted.updated_by) == (SafetyStatus.RETRACTED, None)
    assert (await session.get(Consent, withdrawal_id)).actor_parent_id is None


async def test_순번은_앞으로만_당기고_dry_run_에서는_건드리지_않는다(session):
    """복원본의 순번은 백업 시각에 멈춰 있다 — 그대로 두면 새 이력이 버려진 시간선의 id 를 다시
    써서, 다른 백업으로 또 복원할 때 "이미 있는 id" 로 잘못 건너뛴다. 순번은 롤백해도 되돌아가지
    않아 dry-run 에서는 건드리지 않고, 이력이 정리돼 max(id) 가 작아졌어도 뒤로 돌리지 않는다."""

    async def last() -> int:
        return await session.scalar(
            text(
                "SELECT pg_sequence_last_value("
                "CAST(pg_get_serial_sequence('deletion_log', 'id') AS regclass))"
            )
        )

    top = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0))) + 1000
    first, second = Parent(), Parent()
    session.add_all([first, second])
    await session.flush()
    entry = Entry(
        top, "parent", {"id": str(first.id)}, "delete", None, deleted_at=datetime.now(UTC)
    )
    before = await last()

    dry = await session.begin_nested()
    await apply_entries(session, [entry], dry_run=True)
    await dry.rollback()
    assert await last() == before

    await apply_entries(session, [entry])
    await session.execute(delete(Parent).where(Parent.id == second.id))
    [new_id] = await session.scalars(
        select(DeletionLog.id).where(DeletionLog.row_key == {"id": str(second.id)})
    )
    assert new_id > top

    await session.execute(delete(DeletionLog).where(DeletionLog.id >= top))  # 35일 정리처럼
    await apply_entries(session, [])
    assert await last() >= new_id


async def test_복원_직후_id_를_비켜_두고_비켜_두지_않아_겹치면_멈춘다(session):
    """복원본의 순번은 백업 시각에 멈춰 있다. 그 사이 마이그레이션 · 배치가 남긴 이력이 운영 DB
    이력과 같은 id 를 받으면, apply 가 파일의 그 이력을 "이미 반영됨" 으로 건너뛰어 지운 것이
    되살아난다. reserve 가 막고, 빠뜨렸으면 apply 가 멈춘다."""
    top = await session.scalar(select(func.coalesce(func.max(DeletionLog.id), 0))) + 1000
    gone, other = Parent(), Parent()
    session.add_all([gone, other])
    await session.flush()
    entry = Entry(top, "parent", {"id": str(gone.id)}, "delete", None, deleted_at=datetime.now(UTC))

    assert await reserve_ids(session, [entry]) >= top
    await session.execute(delete(Parent).where(Parent.id == other.id))  # 복원 뒤의 다른 삭제
    [after] = await session.scalars(
        select(DeletionLog.id).where(DeletionLog.row_key == {"id": str(other.id)})
    )
    assert after > top

    # reserve 를 빠뜨려 같은 id 를 다른 이력이 받은 모습
    await session.execute(
        text(
            "INSERT INTO deletion_log (id, table_name, row_key) OVERRIDING SYSTEM VALUE "
            "VALUES (:id, 'parent', CAST(:key AS jsonb))"
        ),
        {"id": top, "key": f'{{"id": "{other.id}"}}'},
    )
    with pytest.raises(BadEntry):
        await apply_entries(session, [entry])
    assert await session.get(Parent, gone.id) is not None
