"""카카오 OAuth 로그인 — 명세: docs/api/auth-kakao-v1.md §3

Spring 대응: @RestController @RequestMapping("/auth") 인 AuthController.
    다른 점은 라우터를 **둘로 나눈 것**이다.

왜 둘로 나누나 — 경로가 고정된 /auth/logout 과 경로에 값이 들어가는
/auth/{provider} 를 한 라우터에 두면 등록 순서에 로그인이 걸린다
(app/api/v1/router.py 의 주석). 나눠 두면 그 순서가 붙어 있는 두 줄로 드러나고,
주석보다 깨지기 어렵다.

구현: #34 (이도헌) · 리뷰: 김명성
"""

from fastapi import APIRouter, Response

from app.api.deps.auth import CurrentParent
from app.api.errors import ApiError
from app.api.v1.schemas.common import ErrorEnvelope
from app.core.constants import AUTH_PREFIX

fixed_router = APIRouter(prefix=AUTH_PREFIX, tags=["auth"])
"""경로가 고정된 엔드포인트. provider 값을 받지 않는다."""

provider_router = APIRouter(prefix=f"{AUTH_PREFIX}/{{provider}}", tags=["auth"])
"""경로에 provider 가 들어가는 엔드포인트.

🚨 provider 는 AuthProvider enum(app/domains/identity/models.py)으로 받는다.
   enum 밖 값이면 422 validation_failed 가 된다 (명세 §8-1).
   f-string 안의 {{provider}} 는 중괄호 이스케이프다 — 실제 경로는 /auth/{provider}.
"""


@fixed_router.post(
    "/logout",
    status_code=204,
    # 🚨 204 는 본문이 없어야 한다. response_class 를 두지 않으면 FastAPI 가
    #    JSON 으로 null 을 실으려 해서 Content-Length 가 어긋난다.
    #    Spring 의 @ResponseStatus(NO_CONTENT) + void 는 이 처리가 자동이다.
    response_class=Response,
    responses={401: {"model": ErrorEnvelope}},
)
async def logout(auth: CurrentParent) -> Response:
    """세션 행 1건 삭제 — 명세 §3-6 · 테스트 A-15.

    Spring 대응: @PostMapping("/logout") @ResponseStatus(NO_CONTENT)

    계약서 28개 목록에 없던 엔드포인트다. 없으면 클라이언트가 토큰을 버리는 흉내만
    내고 서버에서는 만료까지 유효하다.

    지금은 골격이라 여기까지 오지 않는다 — get_current_parent 가 먼저
    401(헤더 없음) 또는 501(미구현)을 던진다.
    """
    # TODO(#34): session_id 로 session 행 1건 DELETE → 204.
    #   🚨 같은 parent 의 다른 세션을 지우지 않는다 (명세 §5-3).
    raise ApiError(501, "not_implemented", "아직 구현되지 않았어요")


# 아래 5개는 #34 에서 채운다. 전부 **무인증**이고 계약서 §01 에 예외로 명시해야 한다.
#   GET  ""          → 302 카카오로             §3-2  (bind 형식 검증 · state 쿠키)
#   GET  "/callback" → 302 복귀 URL             §3-3  (state timing-safe 대조)
#   GET  "/status"   → {ready, start_url}       §3-1
#   POST ""          → 세션 | consent_required  §3-4  (DELETE … RETURNING 소비)
#   POST "/signup"   → 세션                     §3-5  (한 트랜잭션)
#
# 🚨 302 로 답하는 둘은 공통 에러 봉투를 쓸 수 없다 (명세 §8-2). 실패도 리다이렉트로
#    나가므로, 예외를 api/errors.py 핸들러까지 올리지 말고 라우터 안에서
#    복귀 URL + "?error=<코드>" 로 직접 만들어 돌려준다. 문구는 싣지 않는다 —
#    서버 메시지를 URL 에 실으면 공격자가 프론트 화면에 임의 문구를 띄우는 통로가 된다.
