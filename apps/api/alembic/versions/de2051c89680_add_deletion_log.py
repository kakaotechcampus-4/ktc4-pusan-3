"""삭제 이력 표와 삭제 트리거를 만든다 (#758)

- deletion_log — 지운 행의 (표 이름, 기본 키, 지운 시각)만. 내용 · 이름은 남기지 않는다.
- log_deletion() — 지운 행의 기본 키를 카탈로그에서 읽어 deletion_log 에 한 행을 남긴다.
- 아래 TABLES 38개에 AFTER DELETE 트리거. CASCADE 로 함께 지워지는 행에도 걸린다.
  걸지 않는 표와 이유는 app/domains/deletion_log/models.py 의 NOT_LOGGED.
- 상태만 바꾸는 "삭제"도 남긴다 (action='status', changes 에는 상태 · 시각 · 고친 사람 칸만 —
  원문 · 임베딩은 넣지 않는다).
  · 관찰 5표의 deleted — log_status_deletion() 을 그 상태로 바뀔 때 AFTER UPDATE 로.
  · health_safety 의 retracted — 고치기(revise_safety)도 "옛 행 회수 + 새 행" 이라, 커밋 때
    판정하는 지연 트리거 log_safety_retraction() 으로. 같은 항목의 active 행이 있으면 고치기라
    남기지 않는다 — 재적용이 옛 버전을 회수하면 복원 뒤 알레르기가 0건이 된다(새 버전은 백업에
    없다). 진짜 삭제면 그 항목의 모든 버전 id 를 남겨, 고친 뒤 지운 경우 백업 속 옛 버전도 회수된다.
- 복원 뒤에도 유지해야 하는 동의 · 보관 기록은 생기는 것도 남긴다 — consent(동의 · 철회),
  consent_retention(아이 삭제 · 탈퇴 때 복사한 동의 증빙), location_use_log(위치 이용 확인자료).
  백업 뒤에 생긴 행은 복원하면 사라진다 — 철회 행이 사라지면 동의한 상태로 돌아가고, 증빙은 보관
  약속이 깨진다.
  log_insertion() 이 그 행 전체를 action='insert' 로 남기고 재적용이 다시 넣는다. 세 표에는 id ·
  범위 · 약관 버전 · 시각뿐이라 행 전체를 남겨도 된다.
  위치 이용 확인자료는 지워지면 그 사본도 erase_insert_copy() 가 바로 지운다 — 철회 · 탈퇴 때 지체
  없이 파기해야 한다(위치정보법 제24조 ④).
- 백업을 복원할 때 이 이력을 다시 적용해 이미 지운 정보가 되살아나지 않게 한다 (처리방침 5장 ③).

🚨 표 목록과 SQL 을 여기 고정해 둔다 — 다른 revision 처럼 앱 코드를 import 하지 않는다. 앱 코드가
   나중에 바뀌어도 이 revision 이 하는 일은 같아야 한다.
🚨 새 표를 만드는 revision 은 표를 만든 뒤 트리거 한 줄을 더한다 (models.py 머리말).

Revision ID: de2051c89680
Revises: f3a1c8e5b902
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "de2051c89680"
down_revision: Union[str, Sequence[str], None] = "f3a1c8e5b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 🚨 기록 함수 넷은 `deletion_log.replaying` 이 on 이면 아무것도 남기지 않는다. 재적용 도구
#    (scripts/deletion_log.py apply)가 원래 이력을 id · 시각 그대로 옮긴 뒤 동작을 다시 하는 동안만
#    켠다 — 다시 한 동작이 새 이력을 또 남기지 않게. 앱 코드는 이 값을 쓰지 않는다.
#    켤 때는 반드시 SET LOCAL 로 — 그냥 SET 이면 커넥션 풀에 돌아간 뒤에도 남아, 그 커넥션의 뒤
#    삭제가 조용히 기록되지 않는다.
#    위치 기록 사본을 지우는 erase_insert_copy() 는 이 값과 상관없이 돈다 (파기 약속).
# 지울 때마다 그 표의 기본 키를 카탈로그에서 읽는다 — 트리거를 걸 때 칸을 정하지 않으니, 나중에
# 기본 키가 바뀌어도 다시 걸 필요가 없다. 지우는 일은 드물고 표가 작아 비용은 작다.
# 🚨 기본 키가 없는 표에 걸면 그 표의 DELETE 가 실패한다 — 빈 구분 값을 조용히 남기는 것보다 낫다.
# 이력 표는 public. 으로 적는다 — search_path 를 비운 접속(psql 정비 등)에서도 DELETE 가
# 깨지지 않게. 행 전체(임베딩 · 원문 포함)를 jsonb 로 바꾸는 건 한 번만 한다 — 기본 키 칸마다
# 하면 큰 CASCADE 가 느리다.
FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION log_deletion() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_row jsonb := to_jsonb(OLD);
    key jsonb;
BEGIN
    IF current_setting('deletion_log.replaying', true) = 'on' THEN
        RETURN NULL;
    END IF;
    SELECT jsonb_object_agg(a.attname, old_row -> a.attname)
      INTO key
      FROM pg_index i
      JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
     WHERE i.indrelid = TG_RELID AND i.indisprimary;
    IF key IS NULL THEN
        RAISE EXCEPTION 'deletion_log: % 에 기본 키가 없다', TG_TABLE_NAME;
    END IF;
    INSERT INTO public.deletion_log (table_name, row_key) VALUES (TG_TABLE_NAME, key);
    RETURN NULL;
END
$$
"""

# 상태만 바꾸는 삭제 — 지운 행이 아니라 새 행(NEW)의 기본 키와, 트리거 인자로 받은 칸의 값만 남긴다.
STATUS_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION log_status_deletion() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    new_row jsonb := to_jsonb(NEW);
    key jsonb;
    changes jsonb;
BEGIN
    IF current_setting('deletion_log.replaying', true) = 'on' THEN
        RETURN NULL;
    END IF;
    SELECT jsonb_object_agg(a.attname, new_row -> a.attname)
      INTO key
      FROM pg_index i
      JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
     WHERE i.indrelid = TG_RELID AND i.indisprimary;
    IF key IS NULL THEN
        RAISE EXCEPTION 'deletion_log: % 에 기본 키가 없다', TG_TABLE_NAME;
    END IF;
    SELECT jsonb_object_agg(c, new_row -> c) INTO changes FROM unnest(TG_ARGV) AS c;
    INSERT INTO public.deletion_log (table_name, row_key, action, changes)
    VALUES (TG_TABLE_NAME, key, 'status', changes);
    RETURN NULL;
END
$$
"""

# 표: (삭제 상태 값, 남길 칸) — app/domains/deletion_log/models.py 의 STATUS_DELETES 와 같다
OBSERVATION_STATUS_DELETES = {
    "observation_food": ("deleted", ("status", "updated_at")),
    "observation_activity": ("deleted", ("status", "updated_at")),
    "observation_education": ("deleted", ("status", "updated_at")),
    "observation_health": ("deleted", ("status", "updated_at")),
    "observation_routine": ("deleted", ("status", "updated_at")),
}

# 알레르기 · 건강 기록 회수 — 커밋 때 돈다 (아래 CONSTRAINT TRIGGER ... INITIALLY DEFERRED).
# 고치기는 같은 트랜잭션에서 새 active 행을 넣으므로 커밋 때 보면 가를 수 있다.
SAFETY_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION log_safety_retraction() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('deletion_log.replaying', true) = 'on' THEN
        RETURN NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM public.health_safety s
                WHERE s.child_id = NEW.child_id AND s.kind = NEW.kind AND s.label = NEW.label
                  AND s.status = 'active') THEN
        RETURN NULL;
    END IF;
    INSERT INTO public.deletion_log (table_name, row_key, action, changes)
    SELECT 'health_safety', jsonb_build_object('id', s.id), 'status',
           jsonb_build_object('status', 'retracted', 'updated_by', NEW.updated_by,
                              'updated_at', NEW.updated_at)
      FROM public.health_safety s
     WHERE s.child_id = NEW.child_id AND s.kind = NEW.kind AND s.label = NEW.label
       AND s.status <> 'none'
     ORDER BY s.id;
    RETURN NULL;
END
$$
"""

# 새 행 전체 — 아래 INSERT_TABLES 전용이다. 내용(글)이 있는 표에는 쓰지 않는다.
INSERT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION log_insertion() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('deletion_log.replaying', true) = 'on' THEN
        RETURN NULL;
    END IF;
    INSERT INTO public.deletion_log (table_name, row_key, action, changes)
    VALUES (TG_TABLE_NAME, jsonb_build_object('id', NEW.id), 'insert', to_jsonb(NEW));
    RETURN NULL;
END
$$
"""

# app/domains/deletion_log/models.py 의 REINSERTS 와 같다
INSERT_TABLES = ("consent", "consent_retention", "location_use_log")

# 지워지면 이력 속 사본도 지운다 — models.py 의 ERASE_ON_DELETE 와 같다
ERASE_TABLES = ("location_use_log",)
ERASE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION erase_insert_copy() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM public.deletion_log
     WHERE table_name = TG_TABLE_NAME AND action = 'insert' AND row_key ->> 'id' = OLD.id::text;
    RETURN NULL;
END
$$
"""

TABLES = (
    "auth_identity",
    "chat_turn",
    "child",
    "child_growth_log",
    "consent",
    "consent_retention",
    "correction",
    "daycare_meal",
    "delete_proposal",
    "diary_entry",
    "event",
    "event_item",
    "event_series",
    "health_safety",
    "invite",
    "location_use_log",
    "medication_dose",
    "medication_schedule",
    "notice",
    "notice_item",
    "notification_setting",
    "observation_activity",
    "observation_education",
    "observation_food",
    "observation_health",
    "observation_link_hold",
    "observation_routine",
    "parent",
    "parent_child",
    "profile_affinity",
    "push_device",
    "reminder",
    "report",
    "shared_photo",
    "suggestion",
    "suggestion_event",
    "suggestion_evidence",
    "tour_seen",
)


def upgrade() -> None:
    # #753 뒤의 배포(deploy.sh)는 api 를 내리지 않고 마이그레이션한다. 트리거 38개가 표마다 잠금을
    # 잡는데, 오래 걸리는 쓰기에 막혀 줄을 서면 그 뒤의 쓰기까지 함께 멈춘다 — 기다리지 말고 실패
    # 시킨다. 실패하면 마이그레이션 없이 옛 컨테이너가 계속 서비스하고, 배포를 다시 돌리면 된다.
    # (SET LOCAL 이라 같은 실행에서 뒤따르는 revision 에도 걸린다 — 잠금 대기만 5초로 묶는다.)
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "deletion_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_key", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), server_default=sa.text("'delete'"), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "(action = 'delete' AND changes IS NULL)"
            " OR (action IN ('status', 'insert') AND changes IS NOT NULL)",
            name="ck_deletion_log_action",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deletion_log_deleted_at", "deletion_log", ["deleted_at"])
    op.create_index(
        "ix_deletion_log_erase",
        "deletion_log",
        ["table_name", sa.text("(row_key ->> 'id')")],
        postgresql_where=sa.text("action = 'insert'"),
    )
    op.execute(FUNCTION_SQL)
    for table in TABLES:
        op.execute(
            f'CREATE TRIGGER deletion_log AFTER DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION log_deletion()"
        )
    op.execute(STATUS_FUNCTION_SQL)
    for table, (deleted, columns) in OBSERVATION_STATUS_DELETES.items():
        args = ", ".join(f"'{c}'" for c in columns)
        op.execute(
            f'CREATE TRIGGER deletion_log_status AFTER UPDATE OF status ON "{table}" '
            f"FOR EACH ROW WHEN (NEW.status = '{deleted}' "
            "AND OLD.status IS DISTINCT FROM NEW.status) "
            f"EXECUTE FUNCTION log_status_deletion({args})"
        )
    op.execute(SAFETY_FUNCTION_SQL)
    op.execute(
        'CREATE CONSTRAINT TRIGGER deletion_log_status AFTER UPDATE OF status ON "health_safety" '
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.status = 'retracted' AND OLD.status IS DISTINCT FROM NEW.status) "
        "EXECUTE FUNCTION log_safety_retraction()"
    )
    op.execute(INSERT_FUNCTION_SQL)
    for table in INSERT_TABLES:
        op.execute(
            f'CREATE TRIGGER deletion_log_insert AFTER INSERT ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION log_insertion()"
        )
    op.execute(ERASE_FUNCTION_SQL)
    for table in ERASE_TABLES:
        op.execute(
            f'CREATE TRIGGER deletion_log_erase AFTER DELETE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION erase_insert_copy()"
        )


def downgrade() -> None:
    # 🚨 운영에서 내리지 않는다 — 표와 함께 이력이 사라져, 그 뒤 백업을 복원하면 그동안 지운
    #    것이 돌아온다.
    for table in ERASE_TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS deletion_log_erase ON "{table}"')
    op.execute("DROP FUNCTION IF EXISTS erase_insert_copy()")
    for table in INSERT_TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS deletion_log_insert ON "{table}"')
    op.execute("DROP FUNCTION IF EXISTS log_insertion()")
    op.execute('DROP TRIGGER IF EXISTS deletion_log_status ON "health_safety"')
    op.execute("DROP FUNCTION IF EXISTS log_safety_retraction()")
    for table in OBSERVATION_STATUS_DELETES:
        op.execute(f'DROP TRIGGER IF EXISTS deletion_log_status ON "{table}"')
    op.execute("DROP FUNCTION IF EXISTS log_status_deletion()")
    for table in TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS deletion_log ON "{table}"')
    op.execute("DROP FUNCTION IF EXISTS log_deletion()")
    op.drop_index("ix_deletion_log_erase", table_name="deletion_log")
    op.drop_index("ix_deletion_log_deleted_at", table_name="deletion_log")
    op.drop_table("deletion_log")
