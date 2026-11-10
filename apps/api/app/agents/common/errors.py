"""run 이 실패하는 원인의 한 계통

예외 종류가 로그 · SSE `failed.reason` · 화면 문구를 정한다. pipeline 은 `failure_reason(exc)` 로
이유를 한 곳에서 정하고, 문구는 `common/failure.py` 의 카탈로그가 이유마다 하나를 낸다.

클래스 정의는 여기 있고, 원래 모듈(`llm_client` · `ports` · `guardrail` …)은 같은 이름을 다시
내보낸다 — `domains/*/agent_reader.py` 의 import 가 그대로 산다.

🚨 이 파일은 표준 라이브러리만 import 한다. `domains/safety/agent_reader.py` 가 `common/ports.py` 를
   거쳐 읽는 파일이라 여기서 다른 agents 모듈을 부르면 순환이 된다.

계통 밖에 둔 것: `core/weather_raw.UpstreamUnavailable` · `core/book_raw.UpstreamUnavailable`.
core · integrations 는 agents 를 import 하지 못한다. 전부 Agent 안에서 닫히고, 새더라도
`failure_reason` 이 `internal_error` 로 정한다.
"""

from typing import ClassVar, Literal, get_args

# SSE `failed.reason` 이 갖는 값. 값을 더하면 `failure.readout.yaml` 에도 같은 키를 더한다
FailReason = Literal[
    "timeout",  # 러너 60초 · 도메인 20초 마감 · LLM 응답 시간 초과
    "llm_unavailable",  # LLM 연결 실패 · 그 밖의 LLM 오류
    "llm_rate_limited",
    "llm_misconfigured",  # 인증 · 설정 · 요청 거절. 다시 보내도 안 풀린다
    "guard_unavailable",  # 위험 물질 검사를 하지 못함
    "lookup_unavailable",  # 조회 실패가 Agent 밖으로 샌 경우
    "unparsable",  # 말을 정리하지 못함 · Agent 결과 없음
    "internal_error",  # 그 밖
]
FAIL_REASONS: tuple[str, ...] = get_args(FailReason)


class AgentFailure(Exception):
    """계통의 뿌리. 클래스마다 `reason` 을 갖는다."""

    reason: ClassVar[FailReason] = "internal_error"


# --- LLM ---


class LLMError(AgentFailure):
    """Agent 레이어가 다루는 LLM 호출 실패의 공통 타입."""

    reason: ClassVar[FailReason] = "llm_unavailable"


class LLMConfigError(LLMError):
    """역할의 API_KEY / BASE_URL / MODEL / REASONING_EFFORT 가 비었거나 잘못됨.
    메시지에는 실제로 읽은 .env 키를 적는다 — 대체됐으면 MEMORY_* 이름이 나온다."""

    reason: ClassVar[FailReason] = "llm_misconfigured"


class LLMAuthError(LLMError):
    """인증 실패. API_KEY 를 확인한다."""

    reason: ClassVar[FailReason] = "llm_misconfigured"


class LLMBadRequestError(LLMError):
    """요청이 거절됨. 지원하지 않는 파라미터거나 tool schema 가 잘못됨."""

    reason: ClassVar[FailReason] = "llm_misconfigured"


class LLMRateLimitError(LLMError):
    """rate limit 초과. 호출 측에서 backoff 한다."""

    reason: ClassVar[FailReason] = "llm_rate_limited"


class LLMUnavailableError(LLMError):
    """네트워크 연결 실패. 응답 시간 초과는 `LLMTimeoutError` 다."""


class LLMTimeoutError(LLMError):
    """LLM 응답 시간 초과 (openai `APITimeoutError`).

    🚨 내장 `TimeoutError` 를 상속하지 않는다. 러너의 `except TimeoutError` 가 60초 안전망으로
       오인하고, pipeline 의 도메인 단계도 20초 마감으로 센다.
    """

    reason: ClassVar[FailReason] = "timeout"


# --- 조회 ---


class LookupUnavailable(AgentFailure):
    """Agent 쪽 조회 실패의 공통 부모. 빈 결과(0행)와 다르다 — 빈 목록으로 대신하지 않는다."""

    reason: ClassVar[FailReason] = "lookup_unavailable"


class SafetyLookupError(LookupUnavailable):
    """health_safety 를 읽지 못함. 0행(건강정보 동의 안 함 · 등록한 것 없음)과 다르다.

    빈 목록으로 대신 돌려주지 않는다 (루트 CLAUDE.md §2). 닫는 범위는 Agent 마다 다르다 —
    Food 는 식단 추천을 닫고 급식 조회는 "알레르기 확인을 못 했어요" 를 붙인다. Activity 는
    통째로 닫는다(D7). Growth 는 교육 활동만 닫는다(`blocked.safety`).
    """


class MenuSourceError(LookupUnavailable):
    """외부 영양성분·레시피 API 호출 실패.

    결과가 0건인 것(정말 없음)과 다르다. 호출부는 이 예외를 카탈로그에 없는 것
    (캐시 미스)처럼 뭉개지 않는다.
    """


class SafetyRulesUnavailable(LookupUnavailable):
    """안전 사전을 읽지 못했다. 빈 사전으로 통과시키지 않는다 — 교육을 닫는다."""


# --- 안전 검사 ---


class GuardrailUnavailableError(AgentFailure):
    """검사를 하지 못함. 걸린 것이 없다(빈 `GuardResult`)와 다르다."""

    reason: ClassVar[FailReason] = "guard_unavailable"


def failure_reason(exc: BaseException) -> FailReason:
    """예외에서 `failed.reason` 을 정한다. 계통 밖의 예외는 `internal_error`."""
    if isinstance(exc, AgentFailure):
        return exc.reason
    return "internal_error"
