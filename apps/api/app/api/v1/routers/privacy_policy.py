"""개인정보 처리방침 정본 — 로그인 없이 연다 (#221).

    GET /privacy-policy            늘 최신. 스토어 등록 정보 · 가입 화면 · 설정에 거는 고정 주소
    GET /privacy-policy/{version}  시행일별 — 처리방침 15장 ③ "이전의 처리방침은 시행일별로"

처리방침은 동의를 받는 글이 아니다. 그래서 동의 목록(`GET /policies`)에 넣지 않고 주소를 따로 둔다 —
넣으면 가입 화면이 체크박스로 그려 "처리방침에 동의" 를 받는 셈이 된다 (#221).

🚨 무인증이다 (루트 CLAUDE.md §9). 처리방침은 누구나 언제든 볼 수 있게 게시해야 한다
   (개인정보 보호법 제30조 ② · 시행령 제31조 ②).
🚨 글은 DB 가 아니라 이 옆 폴더의 파일이다 (`app/domains/policy/privacy_policy/<시행일>.html`,
   만드는 법은 scripts/render_privacy_policy). 동의 증빙이 아니라서 무엇을 언제 게시했는지는 git 이
   남긴다. 서버가 뜰 때 한 번 읽어 두고 그대로 내준다 — 여기서 다시 만들지 않는다.
"""

from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import HTMLResponse

from app.api.errors import ApiError
from app.api.v1.routers.policies import PAGE_HEADERS

router = APIRouter(tags=["policies"])

PAGES_DIR = FilePath(__file__).resolve().parents[3] / "domains" / "policy" / "privacy_policy"

VERSION_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
"""시행일 — `2026-10-15`. 이 모양이 아니면 파일을 찾기 전에 422 다 (`..` 이 들어올 수 없다)."""

PAGES: dict[str, str] = {
    page.stem: page.read_text(encoding="utf-8") for page in sorted(PAGES_DIR.glob("*.html"))
}
"""시행일 → 정본 HTML. 시행일 순서(= 이름 순서)로 담긴다."""

# 파일이 없으면 아래 max() 가 "empty sequence" 로만 죽어 원인을 찾기 어렵다. 배포 이미지에서
# 이 폴더가 빠졌을 때 바로 보이게 폴더 이름을 대고 멈춘다 (#448 리뷰).
# assert 가 아닌 이유 — python -O 로 돌려도 남아야 한다.
if not PAGES:
    raise RuntimeError(f"개인정보 처리방침 정본(*.html)이 없다: {PAGES_DIR}")

LATEST = max(PAGES)
"""고정 주소가 내주는 버전. 날짜 이름이라 글자 순서가 곧 시행일 순서다.

🚨 시행일이 오지 않은 새 버전을 미리 머지하면 그날부터 고정 주소가 새 글을 내준다. 15장 ① 의
   "적용일 7일 전 알림" 은 버전 주소로 미리 보여 주고, 새 파일은 시행일에 맞춰 머지한다.
"""

LATEST_HEADERS = {**PAGE_HEADERS, "Cache-Control": "public, max-age=3600"}
"""고정 주소는 새 버전이 올라오면 내용이 바뀐다 — 하루가 아니라 한 시간만 캐시한다."""


@router.get("/privacy-policy", response_class=HTMLResponse)
async def read_latest_privacy_policy() -> HTMLResponse:
    """지금 시행 중인 개인정보 처리방침 정본 HTML."""
    return HTMLResponse(PAGES[LATEST], headers=LATEST_HEADERS)


@router.get(
    "/privacy-policy/{version}",
    response_class=HTMLResponse,
    responses={
        200: {"content": {"text/html": {}}, "description": "그 시행일의 정본 HTML 그대로"},
        404: {
            "description": "그 시행일의 처리방침이 없다",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}
            },
        },
    },
)
async def read_privacy_policy(
    version: Annotated[str, Path(pattern=VERSION_PATTERN)],
) -> HTMLResponse:
    """그 시행일의 개인정보 처리방침 정본 HTML. 지난 버전도 계속 내준다."""
    page = PAGES.get(version)
    if page is None:
        raise ApiError(404, "not_found", "개인정보 처리방침을 찾을 수 없어요")
    return HTMLResponse(page, headers=PAGE_HEADERS)
