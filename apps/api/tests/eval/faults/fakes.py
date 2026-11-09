"""스위트 F 가 같이 쓰는 가짜 (docs/eval/eval-v1.md §6).

새로 만든 것은 다른 테스트 모듈에서 import 할 수 없는 것뿐이다.
    SlowLLM      — test_tool_loop.py 의 SlowLLM 은 테스트 메서드 안에 있어 import 할 수 없다
    FakeSession  — test_runner.py 의 FakeSession 은 함수 안에 있고, test_runner 를 import 하는
                   곳도 아직 없다
    echo_client  — 제공처가 요청 일부를 400 메시지에 되돌려 주는 경우. 기존 가짜는 LLMClient 를
                   거치지 않아 llm_client.py 의 BadRequestError 변환을 못 탄다
    FailingStore — 실패 스위치가 있는 기억 저장소가 없다 (InMemoryStore 에는 없다)
나머지(FakeLLM · _tools · _call · _runner …)는 test_pipeline.py 에서, Activity 가짜는
test_activity_agent.py 에서 import 한다 — test_pipeline_cards.py 와
test_doc_seed_general_suggestion.py 가 이미 그렇게 한다. 아래 `food_ctx` · `food_ok` ·
`activity_ok` 는 그 가짜를 F3 · F4 가 같이 쓰게 묶은 것이다.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import openai
import pytest

from app.agents import entrypoint, pipeline
from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMClient, LLMResponse
from app.agents.food.store import InMemoryFoodDocs, InMemoryRecipeCatalog, MenuCatalogRow
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore, MemoryStore
from tests.unit.agents.activity.test_activity_agent import FakeLLM as ActivityLLM
from tests.unit.agents.activity.test_activity_agent import memory as activity_memory
from tests.unit.agents.activity.test_activity_agent import propose as activity_propose
from tests.unit.agents.food.support import MEAL_DOC
from tests.unit.agents.supervisor.test_pipeline import (
    CHILD,
    KST,
    NOW,
    FakeLLM,
    _call,
    _food_context,
    _tools,
)

# 원문 · 별명 카나리아. 로그 어디에도 나오면 안 된다 (루트 §2 · 불변식 I13). 지어낸 문장이다
NICK = "콩떡별이"
RAW = f"{NICK}가 어제 저녁에 브로콜리를 세 송이나 먹었어요"

_REQUEST = httpx.Request("POST", "https://llm.example.invalid/v1/chat/completions")


class SlowLLM:
    """첫 호출에서 멈춘다 — 모델이 답하지 않는 것처럼. 불렸는지는 `started` 로 본다."""

    def __init__(self, seconds: float = 10.0) -> None:
        self.seconds = seconds
        self.started = asyncio.Event()

    async def chat(self, **_kwargs: Any) -> LLMResponse:
        self.started.set()
        await asyncio.sleep(self.seconds)
        raise AssertionError("끊겼어야 하는 호출이 끝까지 갔다")


class _EchoCompletions:
    """`chat.completions.create` 자리. 받은 마지막 메시지를 오류 메시지에 그대로 담는다."""

    def __init__(self) -> None:
        self.echoed: list[str] = []

    async def create(self, **params: Any) -> Any:
        content = str(params["messages"][-1].get("content") or "")
        message = f"Error code: 400 - {{'error': {{'message': 'Invalid input: {content}'}}}}"
        self.echoed.append(message)
        raise openai.BadRequestError(
            message, response=httpx.Response(400, request=_REQUEST), body=None
        )


def echo_client(role: str) -> tuple[LLMClient, _EchoCompletions]:
    """제공처가 400 에 요청 원문을 되돌려 주는 LLMClient. 네트워크는 타지 않는다.

    역할 키를 init 인자로 넘긴다 — itenv 가 올린 .env 의 그 역할 effort 가 none 이 아니면
    tool 호출 전에 LLMConfigError 가 나서 400 경로를 못 탄다.
    """
    prefix = role.upper()
    settings = AgentSettings(
        _env_file=None,
        **{
            f"{prefix}_API_KEY": "test-key",
            f"{prefix}_BASE_URL": "https://llm.example.invalid/v1",
            f"{prefix}_MODEL": "fake-model",
            f"{prefix}_REASONING_EFFORT": "none",
        },
    )
    client = LLMClient(settings, role=role)  # type: ignore[arg-type]
    completions = _EchoCompletions()
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def bind_clients(
    monkeypatch: pytest.MonkeyPatch,
    *,
    supervisor: Any,
    memory: Any,
    domain: dict[str, Any] | None = None,
) -> None:
    """진입점 전체를 가짜 모델로 — `entrypoint._handle_input` 을 클라이언트를 묶은 pipeline 으로.

    import 시점에 이름이 박혀 pipeline 쪽을 바꿔 끼우면 안 먹는다 (test_entrypoint.py 와 같다).
    """
    real = pipeline.handle_input

    async def bound(raw_text: str, memory_context: Any, contexts: Any, **kwargs: Any) -> Any:
        return await real(
            raw_text,
            memory_context,
            contexts,
            supervisor_client=supervisor,
            memory_client=memory,
            domain_clients=domain,
            **kwargs,
        )

    monkeypatch.setattr(entrypoint, "_handle_input", bound)


class FakeSession:
    """러너가 여는 DB 세션 자리. commit 횟수만 센다 — 이 스위트의 run 은 DB 를 읽지 않는다."""

    opened: list["FakeSession"] = []

    def __init__(self) -> None:
        self.commits = 0

    async def __aenter__(self) -> "FakeSession":
        FakeSession.opened.append(self)
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


def fake_sessions(monkeypatch: pytest.MonkeyPatch) -> list[FakeSession]:
    """러너 · agent_ports 가 여는 세션을 가짜로 바꾸고, 열린 세션 목록을 돌려준다."""
    from app.api import agent_ports
    from app.api.runs import runner

    FakeSession.opened = []
    monkeypatch.setattr(runner, "background_session", FakeSession)
    monkeypatch.setattr(agent_ports, "background_session", FakeSession)
    # Curator 는 commit 뒤에만 뜬다. 이 스위트에서는 띄우지 않는다 (DB · 임베딩 키를 탄다)
    monkeypatch.setattr(runner, "curator_trigger", lambda: None)
    return FakeSession.opened


class FailingStore(InMemoryStore):
    """읽기가 터지는 기억 저장소 — DB 연결이 끊긴 것처럼. 쓰기는 그대로 된다."""

    async def query_observations(self, **_kwargs: Any) -> list[Any]:
        raise ConnectionError("기억 저장소 연결이 끊겼다")


def opener_of(store: MemoryStore) -> Callable[[], Any]:
    """도메인 Agent 기억 포트가 호출마다 여는 store 자리 (러너의 `domain_store_opener`)."""

    @asynccontextmanager
    async def opener() -> AsyncIterator[MemoryStore]:
        yield store

    return opener


# ── 진짜 Food · Activity 가 모델을 부르는 작은 세계 (F3 · F4) ─────────────────────────────
MENUS = ("두부조림", "시금치나물", "미역국")


def food_ctx(**ports: Any) -> Any:
    """레시피 3개 · 식단 문서 1개가 있는 Food context — 후보 풀이 차서 모델을 부른다.

    test_pipeline.py 의 식단 추천 저장 테스트
    (`test_식단_추천_결과는_저장_함수에_한_묶음으로_가고_카드가_그_id_를_받는다`)와 같은 세계다.
    `ports` 로 기억 포트 등을 바꿔 끼운다.
    """
    recipes = [
        MenuCatalogRow(
            menu_key=name,
            display_name=name,
            source="mafra_recipe",
            resolved=True,
            synced_at=NOW,
            ingredients=("가짜재료",),
            source_id=name,
        )
        for name in MENUS
    ]
    base = _food_context()
    return replace(
        base,
        ports=replace(
            base.ports,
            recipes=InMemoryRecipeCatalog(recipes),
            docs=InMemoryFoodDocs([MEAL_DOC]),
            **ports,
        ),
    )


def food_ok() -> FakeLLM:
    """세 메뉴를 문서 근거로 고르는 Food 모델 — 일반 추천 3개."""
    doc = {"kind": "food_doc", "id": str(MEAL_DOC.id), "note": "식단 자료를 참고함"}
    candidates = [
        {
            "menu_key": name,
            "meal_slot": "dinner",
            "reason": "어제와 겹치지 않아요",
            "evidence": [doc],
        }
        for name in MENUS
    ]
    return FakeLLM(_tools(_call("p", "propose_meal_candidates", {"candidates": candidates})))


def activity_ok() -> ActivityLLM:
    """기억을 조회한 뒤 실내 놀이 세 개를 내는 Activity 모델."""
    return ActivityLLM(
        activity_memory(), activity_propose("종이컵 탑 쌓기", "그림자놀이", "색종이 접기")
    )


def memory_ctx() -> AgentContext:
    """Memory 가 쓰는 run store — 늘 멀쩡하다."""
    return AgentContext(
        child_id=CHILD,
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )
