"""삭제 이력 (#758) — 지운 행의 구분 값과 지운 시각만 남긴다.

백업을 복원할 때 이 이력을 다시 적용해, 백업 시각 뒤에 지운 정보가 되살아나지 않게 한다
(처리방침 5장 ③ · `docs/safety/retention-v1.md`). 행을 쓰는 것은 앱이 아니라 DB 트리거다 —
`NOT_LOGGED` 밖의 모든 표에 `AFTER DELETE` 트리거가 걸려 있다 (alembic de2051c89680). 그래서
보호자가 지운 것, CASCADE 로 함께 지워진 것, 보관 기간이 끝나 정리 작업이 지운 것이 모두 남는다.

**행을 지우지 않고 상태만 바꾸는 "삭제" 도 남긴다** (`STATUS_DELETES`). 기록(관찰)을 지우면
`status='deleted'`, 알레르기 · 건강 기록을 지우면 `status='retracted'` 가 될 뿐 행은 남는다.
그 전 백업을 복원하면 살아 있는 상태로 돌아오므로, 그 상태로 바뀌는 순간도 이력에 남기고
(`action='status'`) 재적용할 때 같은 상태로 되돌린다. `changes` 에는 상태와 시각 · 고친 사람 칸만 —
원문 · 임베딩은 넣지 않는다.
  · 알레르기 · 건강 기록은 고치기도 "옛 행 회수 + 새 행" 이다. 그래서 커밋 때 판정한다 — 같은 항목의
    active 행이 남아 있으면 고치기라 남기지 않는다(재적용이 옛 버전을 회수하면 복원 뒤 알레르기가
    0건이 된다). 진짜 삭제면 그 항목의 모든 버전 id 를 남긴다 — 고친 뒤 지운 경우 백업 속 옛 버전도
    회수되게.

**복원 뒤에도 유지해야 하는 동의 · 보관 기록은 생기는 것도 남긴다** (`REINSERTS`). 백업 뒤에
생긴 행은 복원하면 사라지는데, 다른 기록과 달리 이 셋은 보관을 약속한 기록이다. 행이 생길 때 행
전체를 `action='insert'` 로 남기고 재적용이 다시 넣는다 — 셋 다 id · 범위 · 약관 버전 · 시각뿐이고
글이 없다.
  · `consent` — 동의와 철회. 철회는 지우기가 아니라 withdrawn 행을 넣는 것이라, 그 행이 사라지면
    동의한 상태로 돌아간다. 동의 행도 처리의 근거라 남긴다.
  · `consent_retention` — 아이 삭제 · 탈퇴 때 동의를 복사한 증빙(1년, 서비스가 정한 기간).
    재적용이 원본은 다시 지우는데 복사본은 백업에 없으면 증빙이 사라진다.
  · `location_use_log` — 위치 이용 확인자료(6개월, 위치정보법 제16조 ②).
그 밖의 "생긴 것" 은 남기지 않는다 — 백업 뒤에 쓴 기록은 복원하면 잃는다(#758 에서 받아들인 한계).
  🚨 위치 이용 확인자료는 지워지면 그 사본도 바로 지운다 (`ERASE_ON_DELETE`). 위치 동의를 철회하거나
     탈퇴하면 확인자료를 지체 없이 파기해야 한다(위치정보법 제24조 ④ · 위치 약관 제10조 ①) — 사본이
     이력에 35일 남으면 그 약속이 깨진다. 재적용은 그대로 맞다: 백업 뒤에 생겼다 지워진 행은 넣을
     사본이 없고, 백업 속 행은 delete 이력이 지운다.

🚨 **새 표를 만들면 그 마이그레이션에서 트리거를 같이 건다.** 표를 만든 뒤 한 줄:

    op.execute('CREATE TRIGGER deletion_log AFTER DELETE ON "새_표" '
               "FOR EACH ROW EXECUTE FUNCTION log_deletion()")

   downgrade 에는 표를 지우기 전에 `DROP TRIGGER IF EXISTS deletion_log ON "새_표"`.
   걸지 않을 표라면 아래 `NOT_LOGGED` 에 이유와 함께 적는다. 둘 다 안 하면
   tests/integration/test_deletion_log.py 가 실패한다.

🚨 내용 · 이름은 남기지 않는다. `row_key` 는 기본 키 칸 이름과 값뿐이다 — 기본 키가 여러 칸인
   표(`suggestion_evidence` · `tour_seen`)가 있어 jsonb 로 둔다.
🚨 35일 뒤 지운다 (`workers/cleanup.py`) — 가장 오래된 백업(28일)보다 길어야 그 백업을 복원할 때
   필요한 이력이 남아 있다.
🚨 앱 코드는 이 표에 쓰지 않는다. 트리거 밖에서 읽고 쓰는 곳은 복원할 때의 재적용 도구뿐이다.
🚨 이력을 비켜 가는 길이 셋 있다 — `TRUNCATE`, `session_replication_role = replica`, 그리고
   `deletion_log.replaying = on`(재적용 도구 전용 — 원래 이력을 옮긴 뒤 다시 하는 동작이 이력을 또
   남기지 않게). 셋 다 이력이 남지 않는다. 앱 코드는 쓰지 않는다. 손으로 정비할 때도 로그 대상
   표에는 쓰지 않는다.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class DeletionLog(Base):
    """지운 행 1개 = 1행. 트리거가 쓴다."""

    __tablename__ = "deletion_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    row_key: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # now() 가 아니라 clock_timestamp() — now() 는 트랜잭션 시작 시각이라, 긴 트랜잭션 끝에서
    # 지운 행이 실제보다 이른 시각으로 남는다.
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    # delete — 행이 지워졌다 / status — 상태만 삭제 상태로 바뀌었다 (changes 에 그 값)
    # insert — 철회처럼 행이 생긴 것을 되살려야 한다 (changes 에 그 행 전체)
    action: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'delete'"))
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_deletion_log_deleted_at", "deleted_at"),
        # 지워진 위치 이용 확인자료의 사본을 찾는다 (ERASE_ON_DELETE)
        Index(
            "ix_deletion_log_erase",
            "table_name",
            text("(row_key ->> 'id')"),
            postgresql_where=text("action = 'insert'"),
        ),
        CheckConstraint(
            "(action = 'delete' AND changes IS NULL)"
            " OR (action IN ('status', 'insert') AND changes IS NOT NULL)",
            name="ck_deletion_log_action",
        ),
    )


# 트리거를 걸지 않는 표와 그 이유. 여기 없는 표는 전부 건다 (머리말).
NOT_LOGGED: dict[str, str] = {
    "alembic_version": "마이그레이션 버전 — 데이터가 아니다",
    "deletion_log": "이력 자신",
    "policy_version": "약관 원문 — 공개 문서이고 지우지 않는다",
    "menu_catalog": "공개 메뉴 목록 — 보호자 정보가 아니다",
    "recipe_catalog": "공개 레시피 목록 — 보호자 정보가 아니다",
    "place": "공개 장소 · 행사 목록 — 보호자 정보가 아니다",
    # 로그인 세 표는 복원할 때 통째로 비운다 — 전원이 다시 로그인한다 (#758 복원 절차).
    # 로그아웃한 세션이 백업에서 되살아나도 그때 사라지고, session_access 는 refresh 마다
    # 생기고 지워져 이력을 걸면 행만 쌓인다.
    "auth_handoff": "로그인 1회용 코드 — 복원할 때 비운다",
    "session": "로그인 세션 — 복원할 때 비운다",
    "session_access": "access 토큰 — 복원할 때 비운다",
    # 지우는 길이 90일 정리 하나뿐이고(탈퇴는 parent_id SET NULL), 복원 절차가 정리 배치를 다시
    # 돌려 같은 행을 지운다. LLM 호출마다 1행이라 걸면 이력만 쌓인다 — 다른 길로 지우게 되면 건다.
    "llm_usage": "LLM 사용량 — 90일 정리로만 지우고, 복원 뒤 정리 배치가 다시 지운다",
    # 백업에서 내용을 빼므로(deploy/scripts/backup.sh) 복원해도 되살아날 행이 없다. 15분마다 생기고
    # 지워져 이력을 걸면 행만 쌓인다 (#877).
    "run_hold": "되묻기 등 15분~24시간 보관함 — 백업에서 빼서 복원해도 되살아나지 않는다",
    # 한도를 세는 사건 — 지우는 길이 만료 삭제(이틀)뿐이고 백업에서 빼서 되살아날 행이 없다 (#877)
    "rate_event": "하루 입력 · 초대 시도 카운터 — 이틀 뒤 지우고 백업에서 뺀다",
    # run 채널의 표 미러 — 닫힌 뒤 5분 · 끝나지 않으면 10분 뒤 지우고 백업에서 뺀다 (#877)
    "run_channel": "run 진행 소식의 표 미러 — 몇 분 뒤 지우고 백업에서 뺀다",
    "run_event": "run 진행 소식의 표 미러 — 몇 분 뒤 지우고 백업에서 뺀다",
}

# 상태만 바꾸는 "삭제" — 표: (삭제 상태 값, 이력에 남길 칸). 이 표들에는 `deletion_log_status`
# 트리거가 더 걸려 있다 (health_safety 는 커밋 때 도는 log_safety_retraction, 나머지는
# log_status_deletion). 새로 이런 "삭제" 를 만들면 여기와 마이그레이션에 같이 더한다 — 자동으로는
# 알아낼 수 없다. 🚨 남길 칸에 원문 · 임베딩을 넣지 않는다. 임베딩은 재적용할 때 비운다.
STATUS_DELETES: dict[str, tuple[str, tuple[str, ...]]] = {
    "observation_food": ("deleted", ("status", "updated_at")),
    "observation_activity": ("deleted", ("status", "updated_at")),
    "observation_education": ("deleted", ("status", "updated_at")),
    "observation_health": ("deleted", ("status", "updated_at")),
    "observation_routine": ("deleted", ("status", "updated_at")),
    "health_safety": ("retracted", ("status", "updated_by", "updated_at")),
}

# 생긴 행을 되살려야 하는 표 — 복원 뒤에도 유지해야 하는 동의 · 보관 기록 (머리말).
# `deletion_log_insert` 트리거가 행이 생길 때마다 행 전체를 남긴다.
# 🚨 글(내용)이 있는 표는 넣지 않는다.
REINSERTS: frozenset[str] = frozenset({"consent", "consent_retention", "location_use_log"})

# REINSERTS 중 행이 지워지면 그 사본도 바로 지우는 표 — 지체 없이 파기해야 하는 기록 (머리말).
# `deletion_log_erase` 트리거가 건다.
ERASE_ON_DELETE: frozenset[str] = frozenset({"location_use_log"})
