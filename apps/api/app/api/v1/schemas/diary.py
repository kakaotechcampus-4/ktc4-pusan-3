"""내 일기 쓰기 요청 — #318 (09 캘린더).

응답은 하루 보기의 `my_diary` 와 같은 `MyDiaryOut` 이다 — 저장한 뒤 화면이 그 값을 그대로 쓴다.
"""

from pydantic import BaseModel, Field


class DiaryRequest(BaseModel):
    content: str = Field(
        description=(
            "일기 본문. 공백뿐이면 422 — 지우기는 DELETE 로만 한다. "
            "앞뒤 공백 · 줄바꿈은 그대로 저장한다"
        )
    )
