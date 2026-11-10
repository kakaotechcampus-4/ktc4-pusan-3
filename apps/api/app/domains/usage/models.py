"""LLM 사용량 표 `llm_usage` — 호출 1번에 1행 (#710 · BM 산정).

원문 · 프롬프트 · 응답 · tool 인자는 칸이 없다 — 샐 수 없다 (루트 §2).
남기는 것은 id · 이름 · 숫자뿐이다. 계약과 규칙은 `docs/ops/llm-usage-v1.md`,
쓰는 쪽은 `core/llm_usage.py`(큐) · `api/llm_usage_writer.py`.

🚨 `parent_id` 는 **SET NULL** 이다. 보호자가 탈퇴하면 보호자와의 연결만 끊기고 숫자는 90일 남는다.
   월 1회 제공처 콘솔 합계와 맞춰 보려면 그달 탈퇴한 보호자가 쓴 토큰도 있어야 해서다
   (#710 PM 코멘트, 처리방침 "인공지능 요청 기록 3개월" 과도 맞다).
   그래서 집계가 보호자 수를 셀 때는 `parent_id IS NOT NULL` 을 건다.
🚨 `child_id` 는 두지 않는다. BM 단위는 보호자다 —
   넣으면 "아이와 함께 지운다"(retention-v1 원칙) 대상이 하나 더 생긴다.
🚨 `run_id` 는 보호자가 탈퇴해도 지우지 않는다.
   이어 볼 계정이 없어서 run 수 집계 · 디버깅에만 쓴다.

`source` 의 닫힌 값은 core 의 `Source` 에서 만든다.
마이그레이션은 글자를 하드코딩한다(과거 revision 이 움직이면 안 된다).
값을 늘리면 새 revision 으로 CHECK 를 넓힌다.
"""

import uuid
from datetime import datetime
from typing import get_args

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.llm_usage import Source
from app.infra.db.base import Base, UUIDPk

SOURCE_CHECK = "source IN ({})".format(", ".join(f"'{value}'" for value in get_args(Source)))


class LlmUsage(Base, UUIDPk):
    """LLM 호출 1번. writer 가 묶음으로 넣고 하루 한 번 90일이 지난 것을 지운다."""

    __tablename__ = "llm_usage"
    __table_args__ = (
        CheckConstraint(SOURCE_CHECK, name="ck_llm_usage_source"),
        # 보호자별 집계 + 탈퇴 때 SET NULL 이 이 보호자의 행을 찾는 길
        Index("ix_llm_usage_parent_id_occurred_at", "parent_id", "occurred_at"),
        # 90일 정리
        Index("ix_llm_usage_occurred_at", "occurred_at"),
        # run 하나 디버깅
        Index("ix_llm_usage_run_id", "run_id"),
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parent.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # supervisor · memory · food · activity · growth · health · curator_judge · embedding ·
    # meal_ocr · notice_ocr. Agent 가 늘 수 있어 CHECK 를 걸지 않는다
    role: Mapped[str] = mapped_column(Text, nullable=False)
    # base_url 의 host 만 — 같은 모델 이름도 게이트웨이마다 단가가 다르다
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    # 응답의 model, 없으면 설정값
    model: Mapped[str] = mapped_column(Text, nullable=False)
    # 캐시 포함 입력 전체. 임베딩은 total_tokens
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    # prompt_tokens 에 이미 포함된 값 — 더하면 두 번 센다
    cached_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # completion_tokens 에 이미 포함된 값 — 더하면 두 번 센다
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
