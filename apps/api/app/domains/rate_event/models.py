"""한도를 세는 사건 한 건 — 하루 입력 · 초대 코드 실패 (#877 3단계 · docs/ops/stateless-v1.md).

워커가 여럿이어도 한도가 하나로 세지게, 프로세스 메모리 카운터를 이 표로 옮겼다. 사건마다 한 행이고
`key` 가 통을 가른다 — `input:<보호자>:<날짜>` · `invite:parent:<보호자>` · `invite:ip:<버킷>`.

- 🚨 UNLOGGED 다. DB 가 비정상 종료되면 비워진다 — 메모리 카운터가 재시작에 비워지던 것과 같다.
- 보호자 FK 를 걸지 않는다. IP 통은 보호자가 없고, 한도는 탈퇴 뒤에도 그 1시간 · 하루 동안 지켜야
  한다(탈퇴 · 재가입으로 한도를 비켜 가지 않게). 이틀 지나면 `hold_purge` 가 지운다.
- 백업에서 내용을 빼고(`deploy/scripts/backup.sh`), 삭제 이력도 걸지 않는다(`NOT_LOGGED`).
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class RateEvent(Base):
    __tablename__ = "rate_event"
    __table_args__ = (
        Index("ix_rate_event_key_at", "key", "at"),
        Index("ix_rate_event_at", "at"),
        {"prefixes": ["UNLOGGED"]},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
