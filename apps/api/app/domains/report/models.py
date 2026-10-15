import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, Timestamps, UUIDPk
from app.infra.db.types import enum_col_py


class ReportCategory(enum.StrEnum):
    """보호자가 고르는 신고 분류 (#487).

    Play 정책 두 개가 앱 안 신고를 요구한다 — AI 생성 콘텐츠 정책(AI 답 · 추천)과 사용자 제작
    콘텐츠 정책(공동 기록 · 다른 보호자). 둘을 한 창구에서 받고 분류로 가른다.
    """

    AI_CONTENT = "ai_content"
    SHARED_RECORD = "shared_record"
    BUG = "bug"
    OTHER = "other"


class Report(Base, UUIDPk, Timestamps):
    """앱 안에서 보낸 신고 · 문의 1건 (#487).

    처리방침 1장의 "권리 행사 요구와 문의의 처리 — 요구한 내용" 에 해당한다. 그래서 새 수집
    항목이 아니다. 보관은 "처리를 마친 날부터 1년" — `handled_at` 이 그 기준이다.

    🚨 `message` 는 보호자가 자유롭게 쓴 글이라 로그 · 알림에 싣지 않는다 (루트 CLAUDE.md §2).
       운영자는 Discord 알림의 신고 id 로 이 행을 찾아 읽는다.
    reporter_parent_id 는 ON DELETE CASCADE — 탈퇴하면 보낸 신고도 함께 지운다. 글에 아이 정보가
    섞일 수 있어 "탈퇴하면 지운다" 원칙을 따랐다. 대가로, 처리하기 전에 탈퇴하면 무엇이 신고됐는지
    남지 않는다(처리방침의 "처리 후 1년" 은 처리 증빙이 목적). 첫 배포는 이대로 두고 PM 이
    정한다(#487).
    """

    __tablename__ = "report"
    __table_args__ = (Index("ix_report_reporter_parent_id", "reporter_parent_id"),)

    reporter_parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[ReportCategory] = mapped_column(
        enum_col_py(ReportCategory, name="report_category"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
