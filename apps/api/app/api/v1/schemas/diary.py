"""내 일기 쓰기 요청 — #318 (09 캘린더).

응답은 하루 보기의 `my_diary` 와 같은 `MyDiaryOut` 이다 — 저장한 뒤 화면이 그 값을 그대로 쓴다.
"""

from pydantic import BaseModel, Field

DIARY_CONTENT_MAX = 1000
"""일기 화면(apps/web/src/lib/api/types.ts DIARY_CONTENT_MAX_LENGTH)과 같은 상한 (#549).
한쪽만 바꾸지 않는다."""


class DiaryRequest(BaseModel):
    content: str = Field(
        max_length=DIARY_CONTENT_MAX,
        pattern=r"^[^\x00]*$",
        description=(
            f"일기 본문. 1~{DIARY_CONTENT_MAX}자 — 넘으면 400. 공백뿐이면 422 — 지우기는 "
            "DELETE 로만 한다. 앞뒤 공백 · 줄바꿈은 그대로 저장하고, 그대로 센다"
        ),
    )
    """🚨 `strip_whitespace` 를 쓰지 않는다 — 신고(`ReportMessage`)와 달리 원문을 그대로 저장하므로
       센 길이와 저장되는 길이가 같아야 한다.
    🚨 NUL(`\\u0000`)을 막는다. Postgres text 칸이 받지 못해 500 이 난다 (신고와 같은 이유).
       `*` 인 이유 — 빈 문자열은 여기서 400 이 아니라 라우터의 공백 검사(422)로 간다."""
