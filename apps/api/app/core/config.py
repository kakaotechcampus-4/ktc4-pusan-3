from pathlib import Path

from pydantic import ValidationError, ValidationInfo, field_validator, model_validator

from app.core.agent_config import AgentLLMSettings

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(AgentLLMSettings):
    APP_ENV: str = "local"
    APP_NAME: str = "ktc4-pusan-3-api"

    DB_HOST: str
    DB_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    # 급식표 사진 OCR — Elice MLAPI (OpenAI 호환). 비어 있으면 사진 입력만 비활성, 서버는 뜬다
    MEAL_OCR_BASE_URL: str | None = None
    MEAL_OCR_API_KEY: str | None = None
    MEAL_OCR_MODEL: str = "gemini-3.1-pro-preview"

    # 브라우저가 다른 오리진에서 이 API 를 부를 수 있는 목록. 쉼표로 구분한다.
    # 비어 있으면 CORS 를 켜지 않는다 — 같은 오리진 배포에서는 필요 없다.
    CORS_ALLOW_ORIGINS: str = ""

    # --- 카카오 OAuth (#34) — 명세 docs/api/auth-kakao-v1.md §11 ---
    #
    # 🚨 개발 환경에서만 Optional 이다. prod 는 없으면 서버가 뜨지 않는다 (#97).
    #    ① local · dev — 키가 없어도 뜬다. GET /auth/{provider}/status 가 ready: false 를
    #       내려 프론트가 버튼을 비활성화하고(§3-1 · A-20), 키를 못 받은 팀원도 나머지
    #       API 를 띄워 쓸 수 있다.
    #    ② prod — 키가 없으면 아무도 로그인할 수 없는 서비스가 조용히 배포되고, 그 사실을
    #       사용자가 먼저 발견한다. 복귀 URL 과 같은 이유로 부팅에서 끊는다 (아래 검증).
    #       원래 ①의 근거는 개발 편의뿐이었다 — 운영에는 적용되지 않는다 (멘토 리뷰, #71).
    KAKAO_REST_API_KEY: str | None = None
    KAKAO_CLIENT_SECRET: str | None = None
    KAKAO_CALLBACK_URL: str | None = None

    # 초. NF-06 의 20초는 Agent 파이프라인 예산이고 로그인은 그 예산 밖이다 (§8-1).
    KAKAO_API_TIMEOUT: float = 3.0

    # 로그인을 마친 뒤 302 로 돌려보낼 곳. client 열거값이 web / app 을 고른다 (§2-3).
    #
    # 🚨 둘 다 필수다. 하나라도 비면 서버가 뜨지 않는다 (아래 검증, #45).
    #    카카오 키와 달리 "없어도 일단 뜨고 ready: false 로 알린다" 를 쓰지 않는 이유 —
    #    이 값이 없으면 인증이 아예 성립하지 않는다. 성공도 실패도 전부 여기로 돌아가고
    #    (§2-3 의 안전한 기본 복귀 대상), 없으면 302 를 만들 수조차 없다.
    #    빠진 채로 뜨면 사용자가 카카오 인증을 **마친 뒤에** 깨진다.
    #
    #    앱도 같이 막는다 (#58 리뷰, 김명성). 사용자가 앱 환경에 치우쳐 있어서, 앱 주소가
    #    빠진 배포를 런타임 거절로 알리면 그 사실을 앱 사용자만 겪는다.
    AUTH_RETURN_URL_WEB: str
    AUTH_RETURN_URL_APP: str

    # 수명 (초) — 명세 §5. 측정으로 정한 값이 아니다. M-02 뒤에 조정한다 (§10 2번).
    SESSION_TTL: int = 43200
    HANDOFF_TTL: int = 120
    SIGNUP_TICKET_TTL: int = 600
    OAUTH_STATE_TTL: int = 600

    # 보호자별 하루 한 줄 입력 횟수 (app/api/quota.py). 모델 크레딧 보호용 — 테스트용 숫자.
    INPUT_DAILY_LIMIT: int = 5

    @field_validator("AUTH_RETURN_URL_WEB", "AUTH_RETURN_URL_APP")
    @classmethod
    def _require_return_url(cls, raw: str, info: ValidationInfo) -> str:
        """빈 문자열도 없는 것으로 본다.

        `AUTH_RETURN_URL_WEB=` 처럼 키만 두고 값을 비워 두는 실수가 흔하다. 타입만
        필수로 걸면 그건 통과해 버려서, 결국 로그인 마지막 단계에서 깨진다.
        """
        if not raw.strip():
            raise ValueError(
                f"{info.field_name} 이 비어 있다. 로그인을 마친 사용자를 돌려보낼 곳이라 "
                "없으면 인증이 성립하지 않는다 (예: http://localhost:3000/auth/callback)"
            )
        return raw

    @model_validator(mode="after")
    def _require_kakao_keys_in_prod(self) -> "Settings":
        """🚨 운영에서는 카카오 키가 없으면 서버가 뜨지 않는다 (#97).

        local · dev 는 막지 않는다. 키를 못 받은 팀원이 나머지 API 를 띄워 쓰기 위해서다.

        dev 를 뺀 이유 — **아직 dev 환경이 없다** (배포 도메인 미정). 지금 정하면
        추측이고, 추측이 빗나갔을 때 손해가 한쪽으로 크다.

            막았는데 키 없이 써야 하는 곳이었다면
                → 팀이 가짜 키를 넣어 우회한다. 그 뒤로 이 검증은 아무도 안 믿는다.
            열었는데 운영 리허설 자리였다면
                → 키 누락을 prod 배포에서 알게 된다. 늦지만 사용자에게 가기 전이다.

        아래쪽이 덜 나쁘다. 그래서 열어 둔다.

        🚨 dev 서버가 생기면 다시 판단한다. 조건에 "dev" 를 더하면 된다.

        운영은 다르다. 키가 없으면 ready: false 가 내려가 로그인 버튼만 꺼진 채
        배포가 끝나고, 서비스가 성립하지 않는다는 사실을 사용자가 먼저 발견한다.

        빠진 키 이름을 메시지에 싣는다 — 부팅 로그는 배포자만 본다 (응답에 싣지
        않는 것은 kakao_missing_keys 의 주석 참고).
        """
        if self.APP_ENV == "prod" and self.kakao_missing_keys:
            raise ValueError(
                f"운영 환경인데 카카오 설정이 비어 있다: {', '.join(self.kakao_missing_keys)}. "
                "이대로 뜨면 로그인 버튼만 꺼진 채 배포된다"
            )
        return self

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

        여기는 **카카오 키만** 본다. 복귀 URL 은 provider 와 무관한 값이라
        app/api/v1/routers/auth.py 의 _missing_keys() 가 더한다 — ready 판정에
        포함하기로 #45 에서 정했다.
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
