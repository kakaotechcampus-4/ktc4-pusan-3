"""화면 오류 보고 본문 — `POST /client-errors` (멘토 #267 2번, #166).

🚨 네 칸 전부 모양이 정해져 있다. 자유 글은 받지 않는다 — 화면 코드가 실수로(또는 누가 일부러)
   보호자의 원문을 실어 보내도 서버 로그에 못 들어온다 (루트 CLAUDE.md §2). 길이도 짧게 — 로그
   한 줄이 커지지 않게.
🚨 프론트 apps/web/src/lib/report-render-error.ts 가 보내는 모양과 같아야 한다.
"""

from pydantic import BaseModel, ConfigDict, Field


class ClientErrorReport(BaseModel):
    """화면에서 난 예외 하나. 값은 서버 로그에만 남고 DB 에는 넣지 않는다."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        pattern=r"^[A-Za-z0-9_$.-]{1,80}$",
        description="예외의 생성자 이름 (TypeError 등). 메시지는 받지 않는다 — 원문이 섞인다",
    )
    digest: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Next 가 서버 렌더 오류에 붙이는 해시. 클라이언트 렌더 오류에는 없다",
    )
    path: str = Field(
        pattern=r"^/[A-Za-z0-9/_.-]{0,199}$",
        description="오류가 난 화면의 경로. 쿼리 문자열은 받지 않는다 — 인가 코드가 실릴 수 있다",
    )
    platform: str = Field(
        pattern=r"^[a-z0-9 ._-]{1,40}$",
        description='기기 요약 — "android 14 app" 처럼 운영체제 · 주요 버전 · 앱/브라우저. '
        "브라우저의 식별 문자열(User-Agent) 전체는 아니다",
    )
