"""인증 엔드포인트의 응답 — 명세 docs/api/auth-kakao-v1.md §3

🚨 프론트 apps/web/src/lib/api/types.ts 의 AuthStatus 와 같은 모양이어야 한다.
   그쪽 start_url 은 nullable 이 아니고 lib/auth/oauth.ts 가 new URL(start_url) 을
   부른다. 설정이 비어도 문자열을 내리는 이유가 이것이다 (아래 참조).
"""

from pydantic import BaseModel


class AuthStatusResponse(BaseModel):
    """GET /auth/{provider}/status — 명세 §3-1.

    죽은 버튼을 만들지 않으려고 있는 응답이다. ready: false 면 프론트가 버튼을
    비활성화하고 "아직 연결 전" 이라고 말한다.
    """

    ready: bool
    """이 provider 로 로그인을 시작할 수 있는 서버 설정이 갖춰졌는가."""

    start_url: str
    """시작 엔드포인트의 절대 URL.

    서버가 내려주는 이유 — 프론트가 API_BASE_URL 로 조립하면 그 값과
    KAKAO_CALLBACK_URL 의 오리진이 어긋난 배포에서 state 쿠키가 조용히 깨진다.
    시작과 콜백이 다른 오리진이면 쿠키가 콜백에 실리지 않는다.
    """

    missing_keys: list[str] | None = None
    """🚨 개발 환경에서만 채운다 (§3-1).

    프로덕션에서 무인증 엔드포인트가 "어떤 설정이 비었는지" 를 알려주면 정찰에 쓰인다.
    None 일 때 키 자체가 빠지도록 라우터가 response_model_exclude_none 을 쓴다.
    """
