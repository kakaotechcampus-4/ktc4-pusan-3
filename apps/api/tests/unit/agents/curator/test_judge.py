"""판정기 계약 · Jev 판정기 단위 테스트. 네트워크 없이 httpx.MockTransport 로 돈다.

- 판단 기준 문장이 실험 3 · 4 에서 검증한 문장과 글자까지 같다
- 보내는 값은 subject · 도메인 · 후보뿐이다
- 응답을 JudgeAnswer 로 옮긴다 (후보 목록 검증은 연결 단계의 몫이라 여기서 하지 않는다)
- 실패는 LLMError 계열로 온다 — 연결 단계가 이것만 잡으면 된다
- CURATOR_JUDGE_* 가 비면 만들 때 막는다
"""

import json
from typing import Any

import httpx
import pytest

from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMUnavailableError,
)
from app.agents.curator.embedding.jev import JevJudge
from app.agents.curator.embedding.judge import (
    NONE,
    PROMPT,
    UNCERTAIN,
    JudgeAnswer,
    JudgeQuotaError,
)
from tests.eval.agents.curator.jev_flow import PROMPT as EXPERIMENT_PROMPT

BASE = "https://judge.invalid/api/alpha"


def _settings(**overrides: str) -> AgentSettings:
    values = {
        "CURATOR_JUDGE_API_KEY": "test-key",
        "CURATOR_JUDGE_BASE_URL": BASE,
        "CURATOR_JUDGE_MODEL": "typesafe/jev-1.13",
        **overrides,
    }
    return AgentSettings(_env_file=None, **values)


def _ok(choice: str, **answer: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "typesafe/jev-1.13-20260917",
            "answers": {"identity": {"type": "choice", "choice": choice, **answer}},
            "usage": {"cost": 0.00002},
        },
    )


class Recorder:
    """받은 요청을 기록하고 정해진 응답을 돌려준다."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self.requests: list[httpx.Request] = []
        self._response = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def _judge(recorder: Recorder, **overrides: str) -> JevJudge:
    return JevJudge(_settings(**overrides), transport=httpx.MockTransport(recorder))


async def _ask(judge: JevJudge) -> JudgeAnswer:
    return await judge.judge(subject="생딸기", domain="food", candidates=["딸기", "블루베리"])


def test_판단_기준_문장이_실험에서_검증한_문장과_같다() -> None:
    """다르면 운영 문장이 실험 3 · 4 결과의 뒷받침을 잃는다. 바꿨다면 실험을 다시 돌린다."""
    assert PROMPT == EXPERIMENT_PROMPT


async def test_보내는_값은_subject_도메인_후보뿐이다() -> None:
    recorder = Recorder(_ok("딸기"))

    await _ask(_judge(recorder))

    request = recorder.requests[0]
    assert str(request.url) == f"{BASE}/decisions"
    assert request.headers["Authorization"] == "Bearer test-key"
    body = recorder.body
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {
        "subject": "생딸기",
        "domain": "food",
        "candidates": ["딸기", "블루베리"],
    }
    question = body["questions"]["identity"]
    assert question["type"] == "choice"
    assert question["instructions"] == PROMPT
    assert list(question["criteria"]) == ["딸기", "블루베리", NONE, UNCERTAIN]


async def test_응답을_그대로_옮긴다() -> None:
    answer = await _ask(_judge(Recorder(_ok("딸기", confidence=0.91))))

    assert answer == JudgeAnswer(choice="딸기", model="typesafe/jev-1.13-20260917", confidence=0.91)


async def test_후보에_없는_값도_그대로_돌려준다() -> None:
    """목록에 있는지 확인은 연결 단계가 한다 — 판정기가 걸러 버리면 원인을 알 수 없다."""
    answer = await _ask(_judge(Recorder(_ok("레고"))))

    assert answer.choice == "레고"


async def test_confidence_가_없어도_된다() -> None:
    answer = await _ask(_judge(Recorder(_ok(NONE))))

    assert (answer.choice, answer.confidence) == (NONE, None)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, LLMAuthError),
        (403, LLMAuthError),
        (402, JudgeQuotaError),  # LLMError 계열이면서 "이번 실행에서 멈춤" 대상
        (429, LLMRateLimitError),
        (400, LLMBadRequestError),
        (500, LLMUnavailableError),
        (503, LLMUnavailableError),
    ],
)
async def test_HTTP_오류는_LLMError_계열로_바꾼다(status: int, expected: type) -> None:
    with pytest.raises(expected):
        await _ask(_judge(Recorder(httpx.Response(status, json={"error": "x"}))))


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectTimeout("timeout"), httpx.ConnectError("refused")],
    ids=["시간초과", "연결실패"],
)
async def test_통신_실패는_LLMUnavailableError(error: Exception) -> None:
    with pytest.raises(LLMUnavailableError):
        await _ask(_judge(Recorder(error)))


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"answers": {}},
        {"answers": {"identity": {"type": "choice"}}},
        {"answers": {"identity": {"type": "boolean", "choice": "딸기"}}},
        {"answers": {"identity": {"type": "choice", "choice": 3}}},
        {"answers": {"identity": {"type": "choice", "choice": "딸기", "confidence": "high"}}},
    ],
    ids=["빈_응답", "답_없음", "choice_없음", "type_다름", "choice_숫자", "confidence_문자"],
)
async def test_응답_형식이_다르면_LLMError(body: dict[str, Any]) -> None:
    with pytest.raises(LLMError, match="형식"):
        await _ask(_judge(Recorder(httpx.Response(200, json=body))))


@pytest.mark.parametrize(
    "key", ["CURATOR_JUDGE_API_KEY", "CURATOR_JUDGE_BASE_URL", "CURATOR_JUDGE_MODEL"]
)
def test_설정이_비면_만들_때_막는다(key: str) -> None:
    with pytest.raises(LLMConfigError, match=key):
        JevJudge(_settings(**{key: " "}))


def test_다른_역할의_설정으로_대체하지_않는다() -> None:
    settings = AgentSettings(
        _env_file=None, MEMORY_API_KEY="k", MEMORY_BASE_URL=BASE, MEMORY_MODEL="chat-model"
    )
    with pytest.raises(LLMConfigError, match="CURATOR_JUDGE_API_KEY"):
        JevJudge(settings)
