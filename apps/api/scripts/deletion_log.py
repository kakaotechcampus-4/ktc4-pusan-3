"""백업을 복원한 뒤 삭제 이력을 다시 적용한다 (#758). 복원할 때만 손으로 돌린다.

    # ① 서비스를 막는다 — 막기 전에 빼면 그 사이에 지운 것이 파일에도 백업에도 없다
    # ② 지금 운영 DB 의 이력을 서버 파일로 빼 둔다 (주인만 읽게)
    umask 077; docker exec ktc4-api uv run --no-sync python -m scripts.deletion_log export \\
        > /var/tmp/deletion-log.jsonl
    # ③ 백업을 복원하고, 곧바로 이력 id 를 비켜 둔 뒤(reserve) alembic upgrade heads 한다
    #   — 복원본의 순번은 백업 시각에 멈춰 있어, 그대로 두면 마이그레이션 · 배치가 이 사이에 남긴
    #     이력이 운영 DB 이력과 같은 id 를 받는다
    docker exec -i ktc4-api uv run --no-sync python -m scripts.deletion_log reserve \\
        < /var/tmp/deletion-log.jsonl
    # ④ 그 파일을 복원한 DB 에 다시 적용한다 — 먼저 --dry-run 으로 건수만 보고
    docker exec -i ktc4-api uv run --no-sync python -m scripts.deletion_log apply --dry-run \\
        < /var/tmp/deletion-log.jsonl
    docker exec -i ktc4-api uv run --no-sync python -m scripts.deletion_log apply \\
        < /var/tmp/deletion-log.jsonl
    # ⑤ 정리 배치(app.workers.cleanup)와 기억 다시 계산(app.workers.profile_recompute)을 한 번씩
    #   바로 돌린다 ⑥ 서비스를 연다
    # ⑦ 빼 둔 파일을 지운다 — 구분 값과 동의 행 사본이 들어 있어 35일 넘게 남으면 안 된다
    shred -u /var/tmp/deletion-log.jsonl
    #   순서 · 사고 구간을 고르는 법은 docs/ops/backup-restore-v1.md (#758 PR 3)

apply 가 하는 일 — 한 트랜잭션이다. 하나라도 이상하면 아무것도 바꾸지 않는다.
  · **파일의 이력을 복원본의 deletion_log 에 id · 시각 그대로 옮긴다.** 그래야 복원본의 이력이 원래
    운영 DB 의 이력과 같아진다. 옮기지 않으면, 복원본에 이미 없어 아무것도 안 바뀐 삭제는 이력도
    남지 않는다 — 그 뒤 다른 백업(버려진 시간선의 더 늦은 백업)으로 또 복원하면 그 행이 되살아난다.
  · 그다음 이력 id 오름차순으로, 원래와 같은 방법으로 다시 한다. 지운 행은 DELETE(FK 동작이
    그대로 돈다 — CASCADE · SET NULL 도 다시 일어난다), 상태만 바꾼 "삭제" 는 그 상태로 UPDATE 하고
    임베딩이 있는 표면 임베딩을 비운다. 이미 없거나 이미 그 상태인 행은 건너뛴다 — 몇 번 돌려도
    같다.
  · 다시 하는 동안 기록 트리거는 끈다(`deletion_log.replaying`) — 옮긴 이력이 이미 있으니 같은 일을
    또 남기지 않는다. CASCADE 로 함께 지워지는 행도 원래 이력에 이미 들어 있다. 위치 기록 사본을
    지우는 트리거는 끄지 않는다(파기 약속).
  · **복원한 DB 의 deletion_log 에 이미 있는 id 는 건너뛴다.** 그 이력은 백업에 이미 반영돼 있다.
    내용까지 같을 때만이다 — 다르면 복원 뒤에 누가 같은 id 로 이력을 쓴 것이라 멈춘다(reserve 누락).
    시각으로 자르지 않는 이유 — 덤프는 커밋된 것만 보는데 deleted_at 은 커밋 전 시각이라, "백업 시각
    뒤" 로 자르면 덤프 직전에 시작해 직후에 커밋된 삭제를 놓친다. id 로 빼면 시계가 필요 없고, 백업
    전에 지웠다가 같은 키로 다시 만든 행(tour_seen 같은 복합 키)을 다시 지우지도 않는다.
  · 생긴 행(action='insert' — 동의 · 철회, 동의 증빙, 위치 이용 확인자료)은 다시 넣는다. 대상
    보호자 · 아이가 복원본에 없으면(백업 뒤에 가입한 경우) 넣을 곳이 없어 건너뛴다 — 그 사람
    자체가 복원본에 없다. 증빙은 보호자 · 아이를 가리키지 않아(FK 없음) 백업 뒤에 가입했다
    탈퇴한 사람의 것도 다시 넣는다.
  · 다시 넣는 행의 약관 버전은 (범위, 버전 이름)으로 다시 찾는다. 약관 버전 id 는 그 판을 등록하는
    마이그레이션이 돌 때 새로 만들어진다 — 그 마이그레이션보다 앞선 백업(배포 전 백업이 그렇다)을
    복원하면 upgrade heads 가 같은 판을 다른 id 로 다시 등록해, 옛 id 로는 넣을 수 없다.
  · 알레르기 · 건강 기록 회수를 다시 적용한 아이의 id 를 출력한다. 백업 뒤에 지웠다가 다시 넣은
    알레르기는 복원으로 사라진다(새 행이 백업에 없다) — 이 아이들의 보호자에게 알레르기 정보를
    다시 확인해 달라고 따로 알린다 (승인 게이트 ㉡ — 건강 · 알레르기 확정은 사람이 한다).
  · 상태만 바꾼 "삭제" 를 되돌려도 기억(profile_affinity)은 다시 계산되지 않는다 — 실제 삭제는
    기억을 다시 계산하라고 표시하지만 여기서는 상태만 바꾼다. 그래서 ⑤ 에서 기억 다시 계산을 돌린다.
  · 로그인 세 표(session · session_access · auth_handoff)와 세션에 묶인 알림 기기(push_device)를
    비운다 — 전원이 다시 로그인한다. 로그아웃한 세션이 백업에서 되살아나지 않게. 알림 기기는
    앱을 열어 로그인하면 다시 등록된다. CASCADE 를 쓰지 않는다 — 세션에 묶인 표가 새로 생기면
    조용히 비우지 않고 실패한다.
  · 이력에 적힌 보호자가 복원본에 없으면(백업 뒤에 들어온 보호자) "SET NULL" 칸(고친 보호자 ·
    한 보호자)은 비워서 적용한다 — 그 보호자가 탈퇴한 것과 같은 모습이다.
  · 이력 id 의 순번을 옮긴 이력 · 지금 순번 중 큰 값 뒤로 당긴다 — 앞으로만, --dry-run 에서는
    건드리지 않는다(순번은 롤백해도 되돌아가지 않는다). 복원본의 순번은 백업 시각에 멈춰 있어,
    그대로 두면 새 이력이 버려진 시간선의 id 를 다시 쓴다 — 그 뒤 다른 백업으로 또 복원할 때
    "이미 있는 id" 로 잘못 건너뛴다.

🚨 이력은 **복원하기 전의 운영 DB** 에서 뺀다. 복원한 덤프 안의 이력은 백업 시각까지만 있다.
   운영 DB 를 통째로 잃었으면 마지막 백업 뒤의 이력도 없다 — 그 구간은 복원 절차 문서를 따른다.
🚨 잘못 지워진 행을 살리려고 복원할 때는, 사고 시각 뒤의 delete · status 이력 중 보호자가 지운
   것만 남기고 파일에서 지운 뒤 적용한다 — 트리거는 보호자가 지운 것과 버그가 지운 것을 구분하지
   못한다. insert 이력(동의 · 철회 · 증빙 · 위치 기록)은 빼지 않는다 — 빼면 철회한 동의가 동의
   상태로 돌아가고 보관할 증빙이 사라진다.
🚨 출력은 표 · 종류별 건수뿐이다. 이력 파일에는 구분 값(id)만 있고 내용은 없다.
🚨 서버 코드에서 import 하지 않는다 — 프로세스로 실행하는 손 배치다.
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TextIO

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.deletion_log.models import NOT_LOGGED, REINSERTS, STATUS_DELETES
from app.infra.db.session import async_session_factory, engine

# 복원할 때 통째로 비우는 표 — 로그인 세 표와 세션에 묶인 알림 기기
LOGIN_TABLES = ("session", "session_access", "auth_handoff", "push_device")


class BadEntry(ValueError):
    """이력 파일의 한 줄이 이 DB 와 맞지 않는다.

    메시지에는 줄 번호 · 표 이름만 — 값은 싣지 않는다.
    """


@dataclass(frozen=True)
class Entry:
    id: int
    table: str
    key: dict[str, Any]
    action: str
    changes: dict[str, Any] | None
    # 다시 넣는 행이 가리키는 약관 버전의 (범위, 버전 이름) — 복원본에서 id 를 다시 찾는다
    policy: tuple[str, str] | None = None
    # 원래 이력의 시각 — 복원본에 그대로 옮긴다 (35일 정리도 원래 일정대로)
    deleted_at: datetime | None = None


async def export_entries(session: AsyncSession) -> list[Entry]:
    rows = await session.execute(
        text(
            """
            SELECT l.id, l.table_name, l.row_key, l.action, l.changes, l.deleted_at,
                   p.scope::text AS scope, p.version
              FROM deletion_log l
              LEFT JOIN policy_version p
                ON l.action = 'insert' AND p.id::text = l.changes ->> 'policy_version_id'
             ORDER BY l.id
            """
        )
    )
    return [
        Entry(
            r.id,
            r.table_name,
            r.row_key,
            r.action,
            r.changes,
            (r.scope, r.version) if r.scope is not None else None,
            r.deleted_at,
        )
        for r in rows
    ]


def dump_entries(entries: Iterable[Entry], out: TextIO) -> int:
    n = 0
    for e in entries:
        out.write(
            json.dumps(
                {
                    "id": e.id,
                    "table": e.table,
                    "key": e.key,
                    "action": e.action,
                    "changes": e.changes,
                    **({"policy": list(e.policy)} if e.policy else {}),
                    **({"deleted_at": e.deleted_at.isoformat()} if e.deleted_at else {}),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        n += 1
    return n


def read_entries(lines: Iterable[str]) -> Iterator[Entry]:
    for no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            if not isinstance(d["key"], dict) or not isinstance(d["changes"], (dict, type(None))):
                raise TypeError("key · changes 의 모양")
            policy = d.get("policy")
            if policy is not None and not (
                isinstance(policy, list)
                and len(policy) == 2
                and all(isinstance(v, str) for v in policy)
            ):
                raise TypeError("policy 의 모양")
            yield Entry(
                int(d["id"]),
                str(d["table"]),
                d["key"],
                str(d["action"]),
                d["changes"],
                (policy[0], policy[1]) if policy else None,
                datetime.fromisoformat(d["deleted_at"]) if d.get("deleted_at") else None,
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise BadEntry(f"{no}번째 줄을 읽지 못했다 ({type(exc).__name__})") from None


@dataclass
class _Table:
    pk: tuple[str, ...]
    columns: frozenset[str]
    # ON DELETE SET NULL 인 한 칸짜리 외래 키 — 칸 → (참조 표, 참조 칸)
    set_null: dict[str, tuple[str, str]]


async def _catalog(session: AsyncSession) -> dict[str, _Table]:
    """public 의 표마다 기본 키 칸과 모든 칸. 이력의 표 · 칸 이름은 여기 있는 것만 SQL 에 쓴다."""
    rows = await session.execute(
        text(
            """
            SELECT c.relname,
                   ARRAY(SELECT a.attname::text FROM pg_index i
                           JOIN pg_attribute a
                             ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
                          WHERE i.indrelid = c.oid AND i.indisprimary ORDER BY a.attnum),
                   ARRAY(SELECT a.attname::text FROM pg_attribute a
                          WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped),
                   ARRAY(SELECT ARRAY[a.attname::text, r.relname::text, ra.attname::text]
                           FROM pg_constraint f
                           JOIN pg_attribute a ON a.attrelid = f.conrelid AND a.attnum = f.conkey[1]
                           JOIN pg_class r ON r.oid = f.confrelid
                           JOIN pg_attribute ra
                             ON ra.attrelid = f.confrelid AND ra.attnum = f.confkey[1]
                          WHERE f.conrelid = c.oid AND f.contype = 'f' AND f.confdeltype = 'n'
                            AND cardinality(f.conkey) = 1)
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
            """
        )
    )
    return {
        name: _Table(tuple(pk), frozenset(cols), {c: (rt, rc) for c, rt, rc in fks})
        for name, pk, cols, fks in rows
    }


def _check(entry: Entry, no: int, catalog: dict[str, _Table]) -> _Table:
    table = catalog.get(entry.table)
    if table is None or entry.table in NOT_LOGGED:
        raise BadEntry(
            f"{no}번째 이력의 표 {entry.table!r} 는 이 DB 에서 이력 대상이 아니다 — "
            "복원한 DB 를 먼저 alembic upgrade heads 했는지 본다"
        )
    if set(entry.key) != set(table.pk):
        raise BadEntry(f"{no}번째 이력({entry.table})의 구분 값 칸이 기본 키와 다르다")
    if entry.action == "delete":
        if entry.changes is not None:
            raise BadEntry(f"{no}번째 이력({entry.table})은 delete 인데 changes 가 있다")
    elif entry.action == "status":
        allowed = STATUS_DELETES.get(entry.table)
        if allowed is None or not entry.changes or not set(entry.changes) <= set(allowed[1]):
            raise BadEntry(f"{no}번째 이력({entry.table})의 상태 칸이 허용 목록과 다르다")
        if entry.changes.get("status") != allowed[0]:
            raise BadEntry(f"{no}번째 이력({entry.table})의 상태가 삭제 상태가 아니다")
    elif entry.action == "insert":
        changes = entry.changes or {}
        if entry.table not in REINSERTS or not changes or not set(changes) <= table.columns:
            raise BadEntry(f"{no}번째 이력({entry.table})은 다시 넣을 수 있는 행이 아니다")
        if any(changes.get(c) != entry.key[c] for c in table.pk):
            raise BadEntry(f"{no}번째 이력({entry.table})의 행이 다시 넣을 행과 다르다")
    else:
        raise BadEntry(f"{no}번째 이력({entry.table})의 종류 {entry.action!r} 를 모른다")
    if entry.policy is not None and (
        entry.action != "insert" or "policy_version_id" not in (entry.changes or {})
    ):
        raise BadEntry(f"{no}번째 이력({entry.table})에 약관 버전이 붙을 수 없다")
    if entry.deleted_at is None or entry.deleted_at.tzinfo is None:
        raise BadEntry(f"{no}번째 이력({entry.table})에 시각이 없다")
    return table


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _value(table: _Table, col: str) -> str:
    """이력의 값 k.col — SET NULL 외래 키면 참조할 행이 복원본에 있을 때만, 없으면 NULL."""
    if col not in table.set_null:
        return f"k.{_q(col)}"
    ref_table, ref_col = table.set_null[col]
    return f"(SELECT r.{_q(ref_col)} FROM {_q(ref_table)} AS r WHERE r.{_q(ref_col)} = k.{_q(col)})"


def _match(pk: tuple[str, ...]) -> str:
    return " AND ".join(f"t.{_q(c)} = k.{_q(c)}" for c in pk)


@dataclass
class Applied:
    """다시 적용한 결과. 모두 건수 · id 뿐이다."""

    counts: Counter[str] = field(default_factory=Counter)  # "표 종류" → 실제로 바뀐 행 수
    skipped: Counter[str] = field(default_factory=Counter)  # 넣을 곳이 없어 건너뛴 다시 넣기
    safety_children: set[str] = field(default_factory=set)  # 알레르기 회수를 다시 적용한 아이
    moved: int = 0  # 복원본의 deletion_log 로 옮긴 이력 수


async def _apply_one(session: AsyncSession, entry: Entry, table: _Table, out: Applied) -> None:
    t = _q(entry.table)
    name = f"{entry.table} {entry.action}"
    if entry.action == "insert":
        assert entry.changes is not None
        row = dict(entry.changes)
        if entry.policy is not None:
            policy_id = await session.scalar(
                text("SELECT id FROM policy_version WHERE scope::text = :s AND version = :v"),
                {"s": entry.policy[0], "v": entry.policy[1]},
            )
            if policy_id is not None:
                row["policy_version_id"] = str(policy_id)
        cols = ", ".join(_q(c) for c in row)
        values = ", ".join(_value(table, c) for c in row)
        sql = (
            f"INSERT INTO {t} ({cols}) SELECT {values} "
            f"FROM jsonb_populate_record(NULL::{t}, CAST(:rec AS jsonb)) AS k "
            "ON CONFLICT DO NOTHING"
        )
        try:
            async with session.begin_nested():
                result = await session.execute(text(sql), {"rec": json.dumps(row)})
        except IntegrityError as exc:
            # 외래 키 — 대상(보호자 · 아이 · 약관 버전)이 복원본에 없다
            if getattr(exc.orig, "sqlstate", None) != "23503":
                raise
            out.skipped[name] += 1
            return
        if result.rowcount:
            out.counts[name] += result.rowcount
        return
    if entry.action == "delete":
        sql = (
            f"DELETE FROM {t} AS t "
            f"USING jsonb_populate_record(NULL::{t}, CAST(:rec AS jsonb)) AS k "
            f"WHERE {_match(table.pk)}"
        )
        rec = entry.key
    else:
        assert entry.changes is not None
        sets = [f"{_q(c)} = {_value(table, c)}" for c in entry.changes]
        if "embedding" in table.columns:
            sets.append('"embedding" = NULL')
        returning = ' RETURNING t."child_id"' if entry.table == "health_safety" else ""
        sql = (
            f"UPDATE {t} AS t SET {', '.join(sets)} "
            f"FROM jsonb_populate_record(NULL::{t}, CAST(:rec AS jsonb)) AS k "
            f'WHERE {_match(table.pk)} AND t."status" IS DISTINCT FROM k."status"{returning}'
        )
        rec = {**entry.changes, **entry.key}
    result = await session.execute(text(sql), {"rec": json.dumps(rec)})
    if entry.table == "health_safety" and entry.action == "status":
        changed = [str(child_id) for (child_id,) in result.all()]
        if changed:
            out.safety_children.update(changed)
            out.counts[name] += len(changed)
    elif result.rowcount:
        out.counts[name] += result.rowcount


async def apply_entries(
    session: AsyncSession, entries: Iterable[Entry], *, dry_run: bool = False
) -> Applied:
    """이력을 복원본에 옮기고 id 오름차순으로 다시 적용한 뒤 로그인 세 표를 비운다.

    커밋은 호출자가 한다. dry_run 이면 롤백할 것이므로 순번만 건드리지 않는다.

    복원본에 이미 있어 건너뛴 이력 · 이미 없거나 그 상태인 행은 세지 않는다.
    이력 전부를 먼저 검사한다 — 복원본에 이미 있어 건너뛸 이력도. 파일이 이 DB 와 안 맞으면
    하나도 바꾸지 않는다.
    """
    # 서비스를 막지 못해 api 가 아직 붙어 있으면 TRUNCATE 가 끝없이 기다린다 — 실패시켜 알린다
    await session.execute(text("SET LOCAL lock_timeout = '10s'"))
    catalog = await _catalog(session)
    ordered = sorted(entries, key=lambda e: e.id)
    checked = [(e, _check(e, no, catalog)) for no, e in enumerate(ordered, 1)]
    restored = set(await session.scalars(text("SELECT id FROM deletion_log")))
    await _check_overlap(session, [e for e, _ in checked if e.id in restored])
    todo = [(e, t) for e, t in checked if e.id not in restored]
    out = Applied()
    # 원래 이력을 id · 시각 그대로 옮긴다 — 다른 백업으로 또 복원해도 이력이 이어지게
    if todo:
        await session.execute(
            text(
                "INSERT INTO deletion_log (id, table_name, row_key, deleted_at, action, changes) "
                "OVERRIDING SYSTEM VALUE VALUES "
                "(:id, :table, CAST(:key AS jsonb), :at, :action, CAST(:changes AS jsonb))"
            ),
            [
                {
                    "id": e.id,
                    "table": e.table,
                    "key": json.dumps(e.key),
                    "at": e.deleted_at,
                    "action": e.action,
                    "changes": None if e.changes is None else json.dumps(e.changes),
                }
                for e, _ in todo
            ],
        )
        out.moved = len(todo)
    # 다시 하는 동작이 이력을 또 남기지 않게 기록 트리거를 끈다. 알레르기 회수 트리거는 커밋 때
    # 도니, 끈 채로 지금 돌리고 나서 다시 켠다 — 커밋 때 돌면 그때는 켜져 있다.
    await session.execute(text("SET LOCAL deletion_log.replaying = 'on'"))
    for entry, table in todo:
        await _apply_one(session, entry, table, out)
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await session.execute(text("SET LOCAL deletion_log.replaying = 'off'"))
    await session.execute(text(f"TRUNCATE {', '.join(LOGIN_TABLES)}"))
    if not dry_run:
        # 순번을 옮긴 이력 · 지금 순번 중 큰 값 뒤로, 앞으로만 — 새 이력이 버려진 시간선의 id 를
        # 다시 쓰지 않게. 순번은 롤백해도 되돌아가지 않아 dry-run 에서는 건드리지 않는다.
        await session.execute(
            text(
                "SELECT setval(s.seq, GREATEST((SELECT coalesce(max(id), 0) FROM deletion_log), "
                "coalesce(pg_sequence_last_value(s.seq), 0), 1)) FROM (SELECT "
                "CAST(pg_get_serial_sequence('deletion_log', 'id') AS regclass) AS seq) AS s"
            )
        )
    return out


async def _check_overlap(session: AsyncSession, overlap: list[Entry]) -> None:
    """복원본에 이미 있는 id 가 파일의 그 이력과 같은지 본다.

    다르면 복원과 apply 사이에 무언가 이력을 써서 운영 DB 이력의 id 를 받은 것이다 — 그대로 두면
    파일의 그 이력을 "이미 반영됨" 으로 건너뛰어 지운 것이 되살아난다. reserve 를 빠뜨린 경우다.
    """
    if not overlap:
        return
    rows = await session.execute(
        text(
            "SELECT id, table_name, row_key, action, changes, deleted_at FROM deletion_log "
            "WHERE id = ANY(:ids)"
        ),
        {"ids": [e.id for e in overlap]},
    )
    there = {r.id: (r.table_name, r.row_key, r.action, r.changes, r.deleted_at) for r in rows}
    for e in overlap:
        if there[e.id] != (e.table, e.key, e.action, e.changes, e.deleted_at):
            raise BadEntry(
                f"{e.id}번 이력이 복원본의 같은 id 와 다르다 — 복원 뒤 apply 전에 이력이 쓰였다. "
                "백업을 다시 복원하고 곧바로 reserve 를 돌린다"
            )


async def reserve_ids(session: AsyncSession, entries: Iterable[Entry]) -> int | None:
    """복원본의 이력 순번을 파일 · 복원본 · 지금 순번 중 큰 값 뒤로 당기고 그 값을 돌려준다.

    백업을 복원한 직후, 마이그레이션보다 먼저 부른다. 앞으로만 당긴다. 백업에 이력 표가 없으면
    (이 기능 전의 백업) 아무것도 하지 않고 None — 그때 겹치는 id 는 apply 가 잡아 멈춘다.
    """
    if await session.scalar(text("SELECT to_regclass('public.deletion_log')")) is None:
        return None
    top = max((e.id for e in entries), default=0)
    return await session.scalar(
        text(
            "SELECT setval(s.seq, GREATEST(:top, (SELECT coalesce(max(id), 0) FROM deletion_log), "
            "coalesce(pg_sequence_last_value(s.seq), 0), 1)) FROM (SELECT "
            "CAST(pg_get_serial_sequence('deletion_log', 'id') AS regclass) AS seq) AS s"
        ),
        {"top": top},
    )


async def _run(args: argparse.Namespace) -> None:
    try:
        async with async_session_factory() as session:
            if args.command == "export":
                n = dump_entries(await export_entries(session), sys.stdout)
                print(f"이력 {n}건을 뺐다", file=sys.stderr)
                return
            if args.command == "reserve":
                value = await reserve_ids(session, read_entries(sys.stdin))
                await session.commit()
                if value is None:
                    print("백업에 이력 표가 없다 — 비켜 둘 것이 없다", file=sys.stderr)
                else:
                    print(f"이력 순번을 {value} 뒤로 당겼다", file=sys.stderr)
                return
            applied = await apply_entries(session, read_entries(sys.stdin), dry_run=args.dry_run)
            print(f"  이력 {applied.moved}건을 복원본에 옮겼다", file=sys.stderr)
            for name, n in sorted(applied.counts.items()):
                print(f"  {name}: {n}", file=sys.stderr)
            for name, n in sorted(applied.skipped.items()):
                print(f"  {name}: {n} (넣을 곳이 없어 건너뜀)", file=sys.stderr)
            if applied.safety_children:
                print(
                    "⚠️ 알레르기 · 건강 기록 회수를 다시 적용한 아이 "
                    f"{len(applied.safety_children)}명 — "
                    "보호자에게 알레르기 정보 확인을 따로 알린다:",
                    file=sys.stderr,
                )
                for child_id in sorted(applied.safety_children):
                    print(f"  {child_id}", file=sys.stderr)
            if args.dry_run:
                await session.rollback()
                print("dry-run — 아무것도 바꾸지 않았다", file=sys.stderr)
            else:
                await session.commit()
                print("적용했다. 로그인 세 표도 비웠다 — 전원 다시 로그인", file=sys.stderr)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export", help="지금 DB 의 이력을 stdout 으로 (JSON Lines)")
    sub.add_parser("reserve", help="복원 직후 — stdin 의 이력 id 뒤로 순번을 당긴다")
    apply = sub.add_parser("apply", help="stdin 의 이력을 지금 DB 에 다시 적용")
    apply.add_argument("--dry-run", action="store_true", help="건수만 보고 되돌린다")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except BadEntry as exc:
        print(f"🔴 {exc} — 아무것도 바꾸지 않았다", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
