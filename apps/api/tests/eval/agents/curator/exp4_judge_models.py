"""동일 대상 판정기. 모델만 바꿔 끼울 수 있게 모양을 하나로 둔다.

판정 한 번 = "subject 와 같은 대상인 후보를 하나 고른다. 없으면 none, 모호하면 uncertain".
이름이 같은 후보 · 후보 없음은 판정기를 부르기 전에 코드가 처리한다 (exp4_judges.py).
판정 규칙 문장은 두 판이 있다 (PROMPTS). 모델마다 같은 판을 넣어야 비교가 공정하다.
    v1  실험 3 의 exp3_flow.PROMPT 그대로
    v2  v1 에서 보류(uncertain) 조건 두 문장만 좁혔다. 나머지는 같다.
        실험 4 에서 Gemini 가 "다른 대상"에도 uncertain 을 많이 고른 것을 보고 만들었다 —
        선정용을 보고 고친 문장이라 최종 평가는 새 미사용 데이터로 한다.

    JevJudge   OpenRouter Decisions API (typesafe/jev-*) — 실험 3 과 같은 요청 모양
    ChatJudge  OpenAI 호환 chat completions (Elice mlapi 등) — JSON 스키마로 선택지를 강제한다

키는 환경변수로 받는다. apps/api/.env 에 모르는 키를 두면 서버 설정이 거부한다.
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from tests.eval.agents.curator.exp3_flow import PROMPT as PROMPT_V1

API_ENV = Path(__file__).resolve().parents[4] / ".env"  # apps/api/.env

NONE = "none"
UNCERTAIN = "uncertain"

_V1_HOLD = (
    "정보가 부족하거나 모호하면 uncertain을 선택하세요.",
    "정보가 부족하거나 모호하면 uncertain.",
)
_V2_HOLD = (
    "uncertain 은 subject 자체가 무엇을 가리키는지 알 수 없을 때(예: '그거', '저번 거')만 "
    "선택하세요. subject 와 후보가 무엇인지 알면 확신이 낮더라도 같은지 다른지로 답하세요.",
    "서로 다른 대상이면 uncertain 이 아니라 none.",
)


def _v2(v1: str) -> str:
    for old, new in zip(_V1_HOLD, _V2_HOLD, strict=True):
        if old not in v1:
            raise RuntimeError(f"v1 문장이 바뀌었다. v2 를 다시 만든다: {old}")
        v1 = v1.replace(old, new, 1)
    return v1


PROMPTS = {"v1": PROMPT_V1, "v2": _v2(PROMPT_V1)}
UNCERTAIN_CRITERIA = {
    "v1": "정보 부족 또는 모호하여 판단 불가",
    "v2": "subject 가 무엇을 가리키는지 알 수 없다",
}


@dataclass(frozen=True)
class Decision:
    choice: str | None  # 후보 · none · uncertain. 형식 오류면 None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # 응답이 금액을 줄 때만 (Jev)
    latency_ms: float = 0.0
    error: str | None = None  # HTTP 상태 · 형식 오류 이름. 원문은 싣지 않는다
    extra: dict[str, Any] = field(default_factory=dict)


class Judge(Protocol):
    name: str

    async def decide(
        self, client: httpx.AsyncClient, *, subject: str, domain: str, candidates: list[str]
    ) -> Decision: ...


def env_key(name: str) -> str:
    """환경변수 → apps/api/.env 순서로 찾는다. 값은 출력하지 않는다."""
    value = os.environ.get(name)
    if not value and API_ENV.exists():
        found = re.search(rf"^{name}=(.*)$", API_ENV.read_text(encoding="utf-8"), re.M)
        value = found[1] if found else None
    value = (value or "").strip().strip("\"'")
    if not value:
        raise SystemExit(f"{name} 가 비어 있다. 환경변수로 넘긴다.")
    return value


class JevJudge:
    """실험 3 의 exp3_flow.decide 와 같은 요청."""

    url = "https://openrouter.ai/api/alpha/decisions"

    def __init__(self, model: str = "typesafe/jev-1.13", *, prompt: str = "v1") -> None:
        self.name = f"{model} (prompt={prompt})"
        self._model = model
        self._prompt = prompt
        self._key = env_key("OPENROUTER_API_KEY")

    async def decide(
        self, client: httpx.AsyncClient, *, subject: str, domain: str, candidates: list[str]
    ) -> Decision:
        started = time.perf_counter()
        response = await client.post(
            self.url,
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "model": self._model,
                "state": {"subject": subject, "domain": domain, "candidates": candidates},
                "questions": {
                    "identity": {
                        "type": "choice",
                        "instructions": PROMPTS[self._prompt],
                        "criteria": {
                            **{s: f"동일한 대상: {s}" for s in candidates},
                            NONE: "동일한 후보가 없다",
                            UNCERTAIN: UNCERTAIN_CRITERIA[self._prompt],
                        },
                    }
                },
            },
        )
        ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            return Decision(None, latency_ms=ms, error=f"HTTP {response.status_code}")
        try:
            body = response.json()
            answer = body["answers"]["identity"]
            usage = body.get("usage", {})
            return Decision(
                answer["choice"],
                model=body.get("model"),
                # Decisions API 는 input_tokens / output_tokens 로 준다 (chat 과 이름이 다르다)
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=usage.get("cost"),
                latency_ms=ms,
                extra={"confidence": answer.get("confidence")},
            )
        except (KeyError, TypeError, ValueError) as exc:
            return Decision(None, latency_ms=ms, error=type(exc).__name__)


class ChatJudge:
    """OpenAI 호환 chat completions. 선택지를 JSON 스키마 enum 으로 강제한다."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        key_env: str,
        reasoning_effort: str | None = None,
        prompt: str = "v1",
    ) -> None:
        self.name = f"{model} (prompt={prompt}, reasoning={reasoning_effort or '기본'})"
        self._prompt = prompt
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._key = env_key(key_env)
        self._reasoning = reasoning_effort

    async def decide(
        self, client: httpx.AsyncClient, *, subject: str, domain: str, candidates: list[str]
    ) -> Decision:
        options = [*candidates, NONE, UNCERTAIN]
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": PROMPTS[self._prompt]
                    + ' 답은 {"choice": 선택지} JSON 하나로만 한다.',
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"subject": subject, "domain": domain, "candidates": candidates},
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "identity",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {"choice": {"type": "string", "enum": options}},
                        "required": ["choice"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        if self._reasoning:
            payload["reasoning_effort"] = self._reasoning

        started = time.perf_counter()
        response = await client.post(
            self._url, headers={"Authorization": f"Bearer {self._key}"}, json=payload
        )
        ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            return Decision(None, latency_ms=ms, error=f"HTTP {response.status_code}")
        try:
            body = response.json()
            choice = json.loads(body["choices"][0]["message"]["content"])["choice"]
            if choice not in options:
                return Decision(None, latency_ms=ms, error="choice_out_of_options")
            usage = body.get("usage", {})
            return Decision(
                choice,
                model=body.get("model"),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                latency_ms=ms,
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            return Decision(None, latency_ms=ms, error=type(exc).__name__)


ELICE_GEMINI_FLASH_LITE = "https://mlapi.run/3fc54e02-bf9b-483e-b0d8-571ff86f04af/v1"


def make_judge(name: str, *, prompt: str = "v1", reasoning_effort: str | None = None) -> Judge:
    if name == "jev":
        return JevJudge(prompt=prompt)
    if name == "gemini-flash-lite":
        # Elice 서버리스 키 하나로 여러 모델을 부른다 (임베딩 키와 같다)
        return ChatJudge(
            base_url=ELICE_GEMINI_FLASH_LITE,
            model="gemini-3.5-flash-lite",
            key_env="EMBEDDING_API_KEY",
            reasoning_effort=reasoning_effort,
            prompt=prompt,
        )
    raise SystemExit(f"알 수 없는 판정기: {name}")
