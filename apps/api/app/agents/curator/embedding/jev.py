"""Jev 판정기. Elice ML API 의 Decisions API(`POST /v1/decisions`)로 동일 대상 여부를 묻는다.

요청 모양은 실험 3 · 4 와 같다 (tests/eval/agents/curator/exp3_flow.py).
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
    PROMPT,
    JudgeAnswer,
    JudgeQuotaError,
    criteria,
    prompt_parts,
)
from app.agents.curator.embedding.ports import CuratorDomain
from app.core import llm_usage
from app.core.prompt_hash import fingerprint, short

logger = logging.getLogger(__name__)


class JevJudge:
    """IdentityJudge 구현. CURATOR_JUDGE_* 가 비면 만들 때 LLMConfigError."""

    def __init__(
        self,
        settings: AgentSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        prompt: str = PROMPT,
    ) -> None:
        """prompt 는 실험에서 다른 판단 기준 문장을 비교할 때만 바꾼다. 운영은 기본값(PROMPT)."""
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
        self._provider = llm_usage.provider_of(values["CURATOR_JUDGE_BASE_URL"])
        self._timeout = s.LLM_TIMEOUT_S
        self._transport = transport  # 테스트는 httpx.MockTransport 를 넣는다
        self._prompt = prompt

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
                    "instructions": self._prompt,
                    "criteria": criteria(options),
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
        # 🚨 모양 검사(_parse)보다 앞에서 기록한다 — 모양이 틀려 버려도 토큰은 이미 과금됐다.
        #    HTTP 오류는 위에서 끝난다: LLMClient 도 SDK 가 오류 응답을 먼저 예외로 바꾼다
        self._record_usage(response, started)
        answer, body = _parse(response)

        # subject · 후보 원문은 남기지 않는다. Elice 응답에는 금액(cost)이 없어 토큰 수를 남긴다
        # (금액은 Elice 콘솔에서 본다). 출력 토큰은 과금되지 않는다
        logger.info(
            "curator judge prompt_name=curator_judge prompt_sha256=%s model=%s candidates=%d "
            "confidence=%s input_tokens=%s latency_ms=%d",
            short(fingerprint(*prompt_parts(self._prompt))),
            answer.model,
            len(options),
            answer.confidence,
            (body.get("usage") or {}).get("input_tokens"),
            int((time.perf_counter() - started) * 1000),
        )
        return answer

    def _record_usage(self, response: httpx.Response, started: float) -> None:
        """응답 원문에서 사용량만 따로 읽어 기록한다. 읽을 수 없으면 조용히 건너뛴다."""
        try:
            body = response.json()
        except ValueError:  # JSON 이 아니면 읽을 사용량이 없다. _parse 가 LLMError 로 끝낸다
            return
        usage = body.get("usage") if isinstance(body, dict) else None
        input_tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
        if not isinstance(input_tokens, int):  # 셀 값이 없는 응답은 0 토큰 행을 만들지 않는다
            return
        served = body.get("model")
        llm_usage.record(
            role="curator_judge",
            provider=self._provider,
            model=served if isinstance(served, str) and served else self._model,
            prompt_tokens=input_tokens,
            completion_tokens=usage.get("output_tokens"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def _raise_for_status(status: int) -> None:
    # 응답 본문은 싣지 않는다. 요청을 되풀이해 담는 서버가 있다
    if status == 200:
        return
    if status in (401, 403):
        # Elice 는 크레딧을 넘기면 키를 지운다 — 잔액 부족이 402 가 아니라 401 로 올 수 있다
        raise LLMAuthError(
            f"판정 인증 실패 (HTTP {status}). CURATOR_JUDGE_API_KEY 와 Elice 크레딧을 확인한다."
        )
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
