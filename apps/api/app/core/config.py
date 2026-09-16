from pathlib import Path

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    APP_ENV: str = "local"
    APP_NAME: str = "ktc4-pusan-3-api"

    DB_HOST: str
    DB_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    MEMORY_API_KEY: str | None = None
    MEMORY_BASE_URL: str | None = None
    MEMORY_MODEL: str | None = None
    MEMORY_REASONING_EFFORT: str | None = None

    SUPERVISOR_API_KEY: str | None = None
    SUPERVISOR_BASE_URL: str | None = None
    SUPERVISOR_MODEL: str | None = None
    SUPERVISOR_REASONING_EFFORT: str | None = None

    FOOD_API_KEY: str | None = None
    FOOD_BASE_URL: str | None = None
    FOOD_MODEL: str | None = None
    FOOD_REASONING_EFFORT: str | None = None

    # 브라우저가 다른 오리진에서 이 API 를 부를 수 있는 목록. 쉼표로 구분한다.
    # 비어 있으면 CORS 를 켜지 않는다 — 같은 오리진 배포에서는 필요 없다.
    CORS_ALLOW_ORIGINS: str = ""

    # --- 카카오 OAuth (#34) — 명세 docs/api/auth-kakao-v1.md §11 ---
    #
    # 🚨 카카오 값은 전부 Optional 이다. 키가 없어도 서버는 떠야 한다.
    #    ① GET /auth/{provider}/status 가 ready: false 를 내려 프론트가 버튼을
    #       비활성화하는 구조다 (§3-1 · 테스트 A-20). 부팅을 막으면 그 화면 자체가 없다.
    #    ② 카카오 키를 못 받은 팀원도 나머지 API 를 띄워 쓸 수 있어야 한다.
    KAKAO_REST_API_KEY: str | None = None
    KAKAO_CLIENT_SECRET: str | None = None
    KAKAO_CALLBACK_URL: str | None = None

    # 초. NF-06 의 20초는 Agent 파이프라인 예산이고 로그인은 그 예산 밖이다 (§8-1).
    KAKAO_API_TIMEOUT: float = 3.0

    # 로그인을 마친 뒤 302 로 돌려보낼 곳. client 열거값이 web / app 을 고른다 (§2-3).
    AUTH_RETURN_URL_WEB: str | None = None
    AUTH_RETURN_URL_APP: str | None = None

    # 수명 (초) — 명세 §5. 측정으로 정한 값이 아니다. M-02 뒤에 조정한다 (§10 2번).
    SESSION_TTL: int = 43200
    HANDOFF_TTL: int = 120
    SIGNUP_TICKET_TTL: int = 600
    OAUTH_STATE_TTL: int = 600

    @field_validator("CORS_ALLOW_ORIGINS")
    @classmethod
    def _reject_wildcard_origin(cls, raw: str) -> str:
        """🚨 * 를 허용 오리진으로 쓰지 않는다.

        이 API 는 Bearer 토큰으로 아이 정보를 내려준다. 아무 사이트나 부를 수 있으면
        XSS 한 번에 남의 페이지에서 우리 API 를 호출하는 통로가 열린다. 오타로 들어가는
        것을 막으려고 부팅에서 끊는다 — 런타임에 조용히 넓어지는 것이 제일 나쁘다.
        """
        if "*" in raw:
            raise ValueError("CORS_ALLOW_ORIGINS 에 * 를 쓰지 않는다. 오리진을 명시할 것")
        return raw

    @property
    def cors_allow_origins(self) -> list[str]:
        """허용 오리진 목록. 빈 칸과 빈 항목은 버린다."""
        return [origin.strip() for origin in self.CORS_ALLOW_ORIGINS.split(",") if origin.strip()]

    @property
    def kakao_missing_keys(self) -> list[str]:
        """카카오 로그인을 시작할 수 없게 만드는 빈 설정의 이름들.

        🚨 이 목록은 개발 환경 응답에만 싣는다. 프로덕션에서 무인증 엔드포인트가
           "어떤 설정이 비었는지" 를 알려주면 정찰에 쓰인다 (§3-1).

        REVIEW(#34): 명세 §3-1 은 ready 의 조건을 client_id · client_secret ·
          콜백 URL 셋으로 못박고 있어 그대로 따랐다. 다만 AUTH_RETURN_URL_WEB 이
          비면 성공도 실패도 돌려보낼 곳이 없어 로그인이 끝까지 못 간다.
          ready 에 포함할지는 프론트와 함께 정할 일이라 여기서 넓히지 않았다.
        """
        return [
            name
            for name in ("KAKAO_REST_API_KEY", "KAKAO_CLIENT_SECRET", "KAKAO_CALLBACK_URL")
            if not getattr(self, name)
        ]

    @property
    def kakao_ready(self) -> bool:
        """이 서버 설정으로 카카오 로그인을 시작할 수 있는가 (§3-1 의 ready)."""
        return not self.kakao_missing_keys

    # extra는 기본 forbid
    model_config = {"env_file": str(_ENV_FILE), "env_file_encoding": "utf-8"}


def _load() -> Settings:
    """설정을 읽는다. 실패시 키 이름과 사유만 남기고 값은 찍지 않는다."""
    try:
        return Settings()
    except ValidationError as error:
        detail = ", ".join(
            f"{'.'.join(str(part) for part in item['loc'])}={item['type']}"
            for item in error.errors()
        )
        raise RuntimeError(f".env 설정 오류 — {detail}") from None


settings = _load()
