"""Jev 판정기. OpenRouter Decisions API 로 동일 대상 여부를 묻는다.

요청 모양은 실험 3 · 4 와 같다 (tests/eval/agents/curator/jev_flow.py).
보내는 값은 subject · 도메인 · 후보 merge_key 뿐이다. 원문 · 아이 id 는 보내지 않는다.
재시도하지 않는다 — 실패하면 연결 단계가 보류하고 다음 실행에서 다시 시도한다.
"""

import logging
import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.agents.common.config import AgentSettings, get_agent_settings
from app.agents.common.llm_client import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMUnavailableError,
)
from app.agents.curator.embedding.judge import (
    NONE,
    PROMPT,
    UNCERTAIN,
    JudgeAnswer,
    JudgeQuotaError,
)
from app.agents.curator.embedding.ports import CuratorDomain

logger = logging.getLogger(__name__)


class JevJudge:
    """IdentityJudge 구현. CURATOR_JUDGE_* 가 비면 만들 때 LLMConfigError."""

    def __init__(
        self,
        settings: AgentSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        s = settings or get_agent_settings()
        values = {
            "CURATOR_JUDGE_API_KEY": s.CURATOR_JUDGE_API_KEY.strip(),
            "CURATOR_JUDGE_BASE_URL": s.CURATOR_JUDGE_BASE_URL.strip(),
            "CURATOR_JUDGE_MODEL": s.CURATOR_JUDGE_MODEL.strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise LLMConfigError(f"{' / '.join(missing)} 가 비어 있다. apps/api/.env 를 확인한다.")

        self._key = values["CURATOR_JUDGE_API_KEY"]
        self._url = values["CURATOR_JUDGE_BASE_URL"].rstrip("/") + "/decisions"
        self._model = values["CURATOR_JUDGE_MODEL"]
        self._timeout = s.LLM_TIMEOUT_S
        self._transport = transport  # 테스트는 httpx.MockTransport 를 넣는다

    @property
    def model(self) -> str:
        return self._model

    async def judge(
        self, *, subject: str, domain: CuratorDomain, candidates: Sequence[str]
    ) -> JudgeAnswer:
        options = list(candidates)
        payload: dict[str, Any] = {
            "model": self._model,
            "state": {"subject": subject, "domain": domain, "candidates": options},
            "questions": {
                "identity": {
                    "type": "choice",
                    "instructions": PROMPT,
                    "criteria": {
                        **{option: f"동일한 대상: {option}" for option in options},
                        NONE: "동일한 후보가 없다",
                        UNCERTAIN: "정보 부족 또는 모호하여 판단 불가",
                    },
                }
            },
        }

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._url, headers={"Authorization": f"Bearer {self._key}"}, json=payload
                )
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError("판정 요청 시간 초과.") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailableError("판정 서버 연결 실패.") from exc

        _raise_for_status(response.status_code)
        answer, body = _parse(response)

        # subject · 후보 원문은 남기지 않는다
        logger.info(
            "curator judge model=%s candidates=%d confidence=%s cost=%s latency_ms=%d",
            answer.model,
            len(options),
            answer.confidence,
            (body.get("usage") or {}).get("cost"),
            int((time.perf_counter() - started) * 1000),
        )
        return answer


def _raise_for_status(status: int) -> None:
    # 응답 본문은 싣지 않는다. 요청을 되풀이해 담는 서버가 있다
    if status == 200:
        return
    if status in (401, 403):
        raise LLMAuthError(f"판정 인증 실패 (HTTP {status}). CURATOR_JUDGE_API_KEY 를 확인한다.")
    if status == 402:
        raise JudgeQuotaError("판정 계정 잔액 부족 (HTTP 402).")
    if status == 429:
        raise LLMRateLimitError("판정 rate limit 초과 (HTTP 429).")
    if status >= 500:
        raise LLMUnavailableError(f"판정 서버 오류 (HTTP {status}).")
    if 400 <= status < 500:
        raise LLMBadRequestError(f"판정 요청 거절 (HTTP {status}).")
    raise LLMError(f"판정 응답 상태가 예상과 다르다 (HTTP {status}).")


def _parse(response: httpx.Response) -> tuple[JudgeAnswer, dict[str, Any]]:
    try:
        body = response.json()
        answer = body["answers"]["identity"]
        choice = answer["choice"]
        if answer.get("type") != "choice" or not isinstance(choice, str):
            raise ValueError("choice")
        confidence = answer.get("confidence")
        if confidence is not None and not isinstance(confidence, (int, float)):
            raise ValueError("confidence")
    except (ValueError, KeyError, TypeError) as exc:
        raise LLMError("판정 응답 형식이 예상과 다르다.") from exc
    return (
        JudgeAnswer(
            choice=choice,
            model=body.get("model"),
            confidence=float(confidence) if confidence is not None else None,
        ),
        body,
    )
