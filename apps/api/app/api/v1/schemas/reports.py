"""앱 안 신고 요청 · 응답 — `POST /reports` (#487).

🚨 분류와 글 두 칸만 받는다. 어느 아이 · 어느 기록인지는 받지 않는다 — 보호자가 글로 적고,
   운영자가 읽고 확인한다. 칸을 늘리면 그만큼 새로 모으는 정보가 된다.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domains.report.models import ReportCategory

ReportMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000, pattern=r"^[^\x00]+$"),
]
"""🚨 NUL(`\\u0000`)을 막는다. JSON 에서는 올바른 값이지만 Postgres text 칸이 받지 못해 500 이 되고,
   가짜 DB 장애 알림이 간다. 줄바꿈 · 탭은 그대로 받는다 (Nickname 패턴과 같은 이유 · #172)."""


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: ReportCategory = Field(
        description=(
            "ai_content — AI 답 · 추천이 부적절해요 / shared_record — 공동 기록이나 다른 보호자 "
            "문제 / bug — 앱 오류 / other — 기타"
        )
    )
    message: ReportMessage = Field(
        description="보호자가 쓴 내용. 앞뒤 공백을 떼고 1~1000자. 로그 · 알림에는 싣지 않는다"
    )


class ReportOut(BaseModel):
    id: uuid.UUID = Field(description="신고 번호. 운영자가 Discord 알림에서 이 번호로 찾는다")
    created_at: datetime = Field(description="서버가 받은 시각")
