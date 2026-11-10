"""삭제 이력 트리거 (#758) — 지운 행의 구분 값만 남고, 빠진 표가 없는지.

백업을 복원할 때 이 이력을 다시 적용해서 이미 지운 정보가 되살아나지 않게 한다. 그래서 두 가지가
깨지면 안 된다.
- 지우면 남는다 — 직접 지운 것도, CASCADE 로 함께 지워진 것도. 기본 키가 여러 칸인 표도.
  상태만 바꾸는 삭제(기록 deleted · 알레르기 retracted)도 — 원문은 빼고. 알레르기 고치기는 빼고.
  동의 철회(withdrawn 행 추가)도.
- 빠진 표가 없다 — 새 표를 만들고 트리거를 안 걸면 그 표의 삭제는 복원 때 되살아난다.
"""

import uuid
from datetime import date, timedelta

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import Range

from app.domains.child.models import Child
from app.domains.consent.location_log import erase_location_logs
from app.domains.consent.models import (
    Consent,
    ConsentAction,
    ConsentScope,
    LocationPurpose,
    LocationUseLog,
)
from app.domains.deletion_log.models import (
    ERASE_ON_DELETE,
    NOT_LOGGED,
    REINSERTS,
    STATUS_DELETES,
    DeletionLog,
)
from app.domains.identity.models import AuthSession, Parent
from app.domains.memory.observation.models import (
    ConfidenceSource,
    ObservationFood,
    ObservationStatus,
)
from app.domains.policy.models import PolicyVersion
from app.domains.safety.models import HealthSafety, SafetyKind
from app.domains.safety.repository import (
    create_safety,
    find_active_safety_for_update,
    retract_safety,
    revise_safety,
)
from app.domains.tour.models import TourSeen

TABLES_SQL = text(
    """
    SELECT c.relname,
           -- 이름만 보지 않는다: 꺼 두지 않았고(tgenabled), 이 함수를 부르고,
           -- 행 단위 · AFTER · DELETE 인지 (tgtype 비트 — 1 행 단위, 2 BEFORE, 8 DELETE)
           EXISTS (SELECT 1 FROM pg_trigger t
                    WHERE t.tgrelid = c.oid AND t.tgname = 'deletion_log' AND NOT t.tgisinternal
                      AND t.tgenabled <> 'D' AND t.tgfoid = 'log_deletion'::regproc
                      AND t.tgtype & 1 = 1 AND t.tgtype & 2 = 0 AND t.tgtype & 8 = 8),
           EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid AND i.indisprimary)
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    """
)

STATUS_TRIGGERS_SQL = text(
    """
    SELECT c.relname,
           -- 트리거 인자(이력에 남길 칸 — escape 로 풀면 \\000 로 이어 붙어 있다)와 WHEN 절
           encode(t.tgargs, 'escape'),
           pg_get_triggerdef(t.oid),
           t.tgfoid::regproc::text,
           t.tginitdeferred
      FROM pg_trigger t
      JOIN pg_class c ON c.oid = t.tgrelid
     WHERE t.tgname = 'deletion_log_status' AND NOT t.tgisinternal AND t.tgenabled <> 'D'
       AND t.tgtype & 1 = 1 AND t.tgtype & 2 = 0 AND t.tgtype & 16 = 16
    """
)

HOW_TO = (
    "새 표는 만드는 마이그레이션에서 삭제 트리거를 같이 건다 — "
    "app/domains/deletion_log/models.py 머리말. 걸지 않을 표면 NOT_LOGGED 에 이유를 적는다."
)


async def _logged(session, table: str, parent_id: uuid.UUID) -> list[dict]:
    """그 보호자의 행만 — 다른 테스트가 커밋하며 남긴 이력이 섞이지 않게."""
    rows = await session.scalars(
        select(DeletionLog.row_key).where(
            DeletionLog.table_name == table,
            DeletionLog.row_key.op("->>")(("parent_id" if table != "parent" else "id"))
            == str(parent_id),
        )
    )
    return list(rows)


async def test_NOT_LOGGED_밖의_모든_표에_트리거가_있다(session):
    rows = (await session.execute(TABLES_SQL)).all()
    tables = {name: (has_trigger, has_pk) for name, has_trigger, has_pk in rows}

    missing = sorted(t for t, (trig, _) in tables.items() if t not in NOT_LOGGED and not trig)
    assert not missing, f"삭제 트리거가 없는 표: {missing}. {HOW_TO}"
    # 이력이 빈 구분 값을 남기지 않게 — 트리거는 기본 키가 없으면 DELETE 를 실패시킨다
    no_pk = sorted(t for t, (trig, pk) in tables.items() if trig and not pk)
    assert not no_pk, f"기본 키 없이 삭제 트리거가 걸린 표: {no_pk}"
    # 제외 목록이 실제와 어긋나지 않게 — 없는 표나, 제외했는데 트리거가 걸린 표
    stale = sorted(t for t in NOT_LOGGED if t not in tables)
    assert not stale, f"NOT_LOGGED 에 있는데 DB 에 없는 표: {stale}"
    both = sorted(t for t in NOT_LOGGED if tables[t][0])
    assert not both, f"NOT_LOGGED 인데 트리거가 걸린 표: {both}"


async def test_탈퇴하면_보호자와_CASCADE_로_지워진_행이_구분_값만_남는다(session):
    parent = Parent()
    session.add(parent)
    await session.flush()
    login = AuthSession(
        parent_id=parent.id,
        refresh_hash=uuid.uuid4().bytes.ljust(32, b"-"),
        refresh_expires_at=text("now() + interval '1 day'"),
        absolute_expires_at=text("now() + interval '60 days'"),
    )
    session.add_all([TourSeen(parent_id=parent.id, tour_key="home"), login])
    await session.flush()
    login_id = login.id

    await session.execute(delete(Parent).where(Parent.id == parent.id))

    assert await _logged(session, "parent", parent.id) == [{"id": str(parent.id)}]
    # 기본 키가 두 칸이어도 칸 이름과 값만 — seen_at 같은 나머지 칸은 남기지 않는다
    assert await _logged(session, "tour_seen", parent.id) == [
        {"parent_id": str(parent.id), "tour_key": "home"}
    ]
    # 로그인 세션은 CASCADE 로 지워지지만, 복원할 때 통째로 비우므로 이력을 남기지 않는다
    assert await session.scalar(select(AuthSession.id).where(AuthSession.id == login_id)) is None
    assert not await session.scalar(
        select(DeletionLog.id).where(DeletionLog.table_name == "session").limit(1)
    )


# 상태 이력에 남겨도 되는 칸 — 원문 · 이름 · 임베딩이 들어가면 안 된다
SAFE_STATUS_COLUMNS = {"status", "updated_at", "updated_by"}


async def test_상태만_바꾸는_삭제를_하는_표에_상태_트리거가_있다(session):
    rows = (await session.execute(STATUS_TRIGGERS_SQL)).all()
    triggers = {
        table: (tuple(args.split("\\000")[:-1]), definition, function, deferred)
        for table, args, definition, function, deferred in rows
    }

    assert set(triggers) == set(STATUS_DELETES)
    for table, (deleted, columns) in STATUS_DELETES.items():
        args, definition, function, deferred = triggers[table]
        assert f"'{deleted}'" in definition, f"{table} 트리거의 삭제 상태가 다르다"
        assert set(columns) <= SAFE_STATUS_COLUMNS, f"{table} 이 내용 칸을 남긴다: {columns}"
        if table == "health_safety":
            # 고치기를 가르려면 커밋 때 돌아야 한다. 남기는 칸은 함수 안에 고정돼 있다
            assert (function, deferred, args) == ("log_safety_retraction", True, ())
        else:
            assert (function, deferred) == ("log_status_deletion", False)
            assert args == columns, f"{table} 트리거가 남기는 칸이 STATUS_DELETES 와 다르다"


async def test_보관할_증빙이_생기면_남기는_트리거가_있다(session):
    rows = await session.execute(
        text(
            """
            SELECT c.relname, pg_get_triggerdef(t.oid)
              FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
             WHERE t.tgname = 'deletion_log_insert' AND t.tgenabled <> 'D'
               AND t.tgfoid = 'log_insertion'::regproc
            """
        )
    )
    triggers = dict(rows.all())
    assert set(triggers) == set(REINSERTS)
    for table in REINSERTS:
        assert "WHEN" not in triggers[table], f"{table} 은 생길 때마다 남겨야 한다"


async def _family(session) -> tuple[Parent, Child]:
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(owner_parent_id=parent.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return parent, child


async def _status_logs(session, table: str, row_id: uuid.UUID) -> list[tuple[str, dict]]:
    rows = await session.execute(
        select(DeletionLog.action, DeletionLog.changes).where(
            DeletionLog.table_name == table, DeletionLog.row_key == {"id": str(row_id)}
        )
    )
    return [tuple(r) for r in rows]


async def test_기록을_지우면_상태와_시각만_남고_다른_수정은_남지_않는다(session):
    _, child = await _family(session)
    today = date(2026, 11, 1)
    food = ObservationFood(
        child_id=child.id,
        raw_text="원문-남으면-안-됨",
        subject="사과",
        polarity=1,
        confidence_source=ConfidenceSource.PARENT_DIRECT,
        status=ObservationStatus.ACTIVE,
        observed_range=Range(today, today + timedelta(days=1)),
    )
    session.add(food)
    await session.flush()
    rows = update(ObservationFood).where(ObservationFood.id == food.id)

    await session.execute(rows.values(subject="배"))  # 지우는 게 아닌 수정
    await session.execute(rows.values(status=ObservationStatus.DELETED))
    await session.execute(rows.values(status=ObservationStatus.DELETED))  # 이미 지운 것

    [(action, changes)] = await _status_logs(session, "observation_food", food.id)
    assert action == "status"
    assert set(changes) == {"status", "updated_at"}
    assert changes["status"] == "deleted"


async def test_알레르기를_지우면_회수_상태와_지운_보호자가_남는다(session):
    parent, child = await _family(session)
    safety = HealthSafety(child_id=child.id, kind=SafetyKind.ALLERGY, label="우유")
    session.add(safety)
    await session.flush()

    await retract_safety(session, child_id=child.id, safety_id=safety.id, parent_id=parent.id)
    await _commit_point(session)

    [(action, changes)] = await _status_logs(session, "health_safety", safety.id)
    assert action == "status"
    assert set(changes) == {"status", "updated_by", "updated_at"}
    assert changes["status"] == "retracted"
    assert changes["updated_by"] == str(parent.id)


async def _commit_point(session) -> None:
    """알레르기 회수 트리거는 커밋 때 돈다. 테스트는 커밋하지 않으니 여기서 그 시점을 당긴다."""
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


async def _revise(session, child: Child, parent: Parent, safety_id: uuid.UUID) -> HealthSafety:
    current = await find_active_safety_for_update(session, child_id=child.id, safety_id=safety_id)
    return await revise_safety(
        session,
        current=current,
        parent_id=parent.id,
        severity=None,
        reactions=["두드러기"],
        notes=None,
    )


async def test_알레르기를_고치면_남기지_않는다(session):
    """고치기는 옛 행 회수 + 새 행이다. 남기면 재적용이 옛 버전을 회수해, 새 버전이 없는 복원본에서
    알레르기가 0건이 된다."""
    parent, child = await _family(session)
    first = await create_safety(
        session, child_id=child.id, parent_id=parent.id, kind=SafetyKind.ALLERGY, label="땅콩"
    )

    await _revise(session, child, parent, first.id)
    await _commit_point(session)

    assert await _status_logs(session, "health_safety", first.id) == []


async def test_고친_뒤_지우면_그_항목의_모든_버전이_남는다(session):
    """백업에는 고치기 전 버전이 살아 있다 — 그것도 회수돼야 지운 알레르기가 돌아오지 않는다."""
    parent, child = await _family(session)
    first = await create_safety(
        session, child_id=child.id, parent_id=parent.id, kind=SafetyKind.ALLERGY, label="땅콩"
    )
    second = await _revise(session, child, parent, first.id)
    await _commit_point(session)

    await retract_safety(session, child_id=child.id, safety_id=second.id, parent_id=parent.id)
    await _commit_point(session)

    for version in (first, second):
        [(action, changes)] = await _status_logs(session, "health_safety", version.id)
        assert (action, changes["status"]) == ("status", "retracted")


async def test_동의와_철회는_생길_때마다_행_전체가_남는다(session):
    parent, _ = await _family(session)
    policy_version_id = await session.scalar(
        select(PolicyVersion.id).where(PolicyVersion.scope == ConsentScope.SERVICE_TERMS).limit(1)
    )
    rows = {
        action: Consent(
            subject_parent_id=parent.id,
            actor_parent_id=parent.id,
            actor_ref=parent.id,
            scope=ConsentScope.SERVICE_TERMS,
            action=action,
            policy_version_id=policy_version_id,
        )
        for action in (ConsentAction.GRANTED, ConsentAction.WITHDRAWN)
    }
    session.add_all(rows.values())
    await session.flush()

    for consent_action, row in rows.items():
        [(action, changes)] = await _status_logs(session, "consent", row.id)
        assert action == "insert"
        assert changes["action"] == consent_action
        assert changes["subject_parent_id"] == str(parent.id)


async def test_위치_기록을_지우면_이력_속_사본도_바로_지운다(session):
    """위치 동의를 철회하면 확인자료를 지체 없이 파기한다 (위치정보법 제24조 ④). 이력에 남은 사본도
    같이 — 35일 남으면 그 약속이 깨진다. 지웠다는 이력(구분 값만)은 남는다."""
    triggers = await session.scalars(
        text(
            """
            SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
             WHERE t.tgname = 'deletion_log_erase' AND t.tgenabled <> 'D'
               AND t.tgfoid = 'erase_insert_copy'::regproc
            """
        )
    )
    assert set(triggers) == ERASE_ON_DELETE
    parent, _ = await _family(session)
    use = LocationUseLog(parent_id=parent.id, purpose=LocationPurpose.SUGGESTION)
    session.add(use)
    await session.flush()
    assert [a for a, _ in await _status_logs(session, "location_use_log", use.id)] == ["insert"]

    await erase_location_logs(session, parent_id=parent.id)

    assert [a for a, _ in await _status_logs(session, "location_use_log", use.id)] == ["delete"]
