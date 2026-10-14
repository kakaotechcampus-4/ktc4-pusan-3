"""입력 접수가 `child_health` 동의를 한 번 읽어 러너로 넘긴다 (#372 PR 2).

🚨 동의를 읽는 데 실패하면 run 을 시작하지 않는다. "동의 안 함" 으로 바꿔 넘기면 Agent 가
   `health_safety` 를 읽지 않고 알레르기 필터 없이 추천을 연다.
DB 없이 도는 테스트다 — 의존성(인증 · 아이 접근 · 세션)과 동의 조회 · 러너를 바꿔 끼운다.
"""

import asyncio
import uuid
from datetime import date

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.agents.entrypoint import PendingMemoryContext
from app.agents.memory.schemas.task import WorkType
from app.api import idempotency, quota
from app.api.deps.auth import AuthContext, get_current_parent
from app.api.deps.child import ChildAccess, get_accessible_child
from app.api.errors import register_error_handlers
from app.api.runs import pending_reply, registry, runner
from app.api.v1.routers import children
from app.infra.db.session import get_session

PARENT = uuid.UUID(int=10)
CHILD = uuid.UUID(int=20)
BIRTH = date(2024, 3, 5)
URL = f"/api/v1/children/{CHILD}/inputs"
BODY = {"text": "계란말이 또 찾아요", "source": "home_input"}


class DbDown(Exception):
    """동의 조회 실패를 흉내 낸다."""


@pytest.fixture(autouse=True)
def _clean_process_memory():
    for module in (registry, idempotency, quota, pending_reply):
        module.clear()
    yield
    for module in (registry, idempotency, quota, pending_reply):
        module.clear()


@pytest.fixture
def agent_calls(monkeypatch) -> list[dict]:
    """러너를 가짜로 바꿔 끼우고, 창구가 `agent_job` 에 무엇을 넘겼는지 기록한다."""
    calls: list[dict] = []

    def fake_agent_job(**kwargs):
        calls.append(kwargs)
        return runner.fake_job

    monkeypatch.setattr(runner, "agent_job", fake_agent_job)
    monkeypatch.setattr(runner, "start", lambda channel, job, *, raw_text: None)
    return calls


class _Reads(list):
    """동의 조회를 부른 아이 id 들. `value` 가 예외면 그것을 올리고, 아니면 그 값을 돌려준다."""

    value: bool | Exception = True


@pytest.fixture
def consent_reads(monkeypatch) -> _Reads:
    """동의 조회를 가짜로 바꿔 끼운다. 기본은 granted 이고, 읽을 때마다 아이 id 를 적는다."""
    reads = _Reads()

    async def fake_read(session, *, child_id):
        reads.append(child_id)
        if isinstance(reads.value, Exception):
            raise reads.value
        return reads.value

    monkeypatch.setattr(children, "child_health_granted", fake_read)
    return reads


@pytest.fixture
async def client():
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(children.router, prefix="/api/v1")

    async def fake_session():
        yield object()

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[get_current_parent] = lambda: AuthContext(
        parent_id=PARENT, session_id=uuid.UUID(int=30)
    )
    app.dependency_overrides[get_accessible_child] = lambda: ChildAccess(
        child_id=CHILD, birth_date=BIRTH
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        yield http_client


def _key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


@pytest.mark.parametrize("granted", [True, False])
async def test_읽은_동의_값을_러너에_그대로_넘긴다(client, agent_calls, consent_reads, granted):
    consent_reads.value = granted

    response = await client.post(URL, json=BODY, headers=_key())

    assert response.status_code == 202
    assert len(agent_calls) == 1
    assert agent_calls[0]["child_health_granted"] is granted
    assert agent_calls[0]["birth_date"] == BIRTH  # 생일은 지금처럼 같이 간다


async def test_입력_한_번에_동의를_한_번만_읽는다(client, agent_calls, consent_reads):
    await client.post(URL, json=BODY, headers=_key())

    assert consent_reads == [CHILD]


async def test_같은_키_재생은_동의를_다시_읽지_않는다(client, agent_calls, consent_reads):
    headers = _key()
    first = await client.post(URL, json=BODY, headers=headers)
    second = await client.post(URL, json=BODY, headers=headers)

    assert second.json()["run_id"] == first.json()["run_id"]
    assert len(consent_reads) == 1
    assert len(agent_calls) == 1


async def test_동의_조회가_실패하면_run_을_시작하지_않는다(
    client, agent_calls, consent_reads, monkeypatch
):
    """실패는 "동의 안 함" 으로 바뀌지 않는다 — 예외로 끝나고 러너를 만들지 않는다."""
    consent_reads.value = DbDown("동의 조회 실패")
    headers = _key()
    opened: list[object] = []
    real_open_run = registry.open_run

    def spy_open_run(**kwargs):
        opened.append(kwargs)
        return real_open_run(**kwargs)

    monkeypatch.setattr(registry, "open_run", spy_open_run)

    with pytest.raises(DbDown):
        await client.post(URL, json=BODY, headers=headers)

    assert agent_calls == []  # agent_job 도 부르지 않았다
    assert opened == []  # 채널도 열지 않았다
    # 같은 키로 다시 보내면 새 접수다 — 실패한 요청이 키를 먹지 않았다
    consent_reads.value = True
    retry = await client.post(URL, json=BODY, headers=headers)
    assert retry.status_code == 202
    assert len(agent_calls) == 1


async def test_동의_조회가_실패해도_한도와_되묻기_맥락은_그대로다(
    client, agent_calls, consent_reads
):
    """읽기는 한도를 세고 맥락을 꺼내기 전에 한다.

    실패한 요청이 하루 한도와 한 번뿐인 맥락을 먹지 않는다.
    """
    pending_reply.put(
        run_id="prev-run",
        parent_id=PARENT,
        child_id=CHILD,
        context=PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE),
    )
    consent_reads.value = DbDown("동의 조회 실패")

    with pytest.raises(DbDown):
        await client.post(URL, json={**BODY, "reply_to": "prev-run"}, headers=_key())

    assert pending_reply.get(run_id="prev-run", parent_id=PARENT, child_id=CHILD) is not None
    from app.core.config import settings

    # 한도가 줄지 않았다 — 한도까지 남은 횟수를 그대로 쓸 수 있다
    consent_reads.value = True
    for _ in range(settings.INPUT_DAILY_LIMIT):
        response = await client.post(URL, json=BODY, headers=_key())
        assert response.status_code == 202


async def _answer_same_question_twice_at_once(client, monkeypatch):
    """같은 되묻기에 두 답을 거의 동시에 보낸다.

    동의 읽기가 두 요청이 모두 들어올 때까지 기다리게 해서 끼어들기를 재현한다.
    """
    pending_reply.put(
        run_id="prev-run",
        parent_id=PARENT,
        child_id=CHILD,
        context=PendingMemoryContext("요즘 기침해", "언제부터였어요?", WorkType.OBSERVE),
    )
    entered = 0
    both_in = asyncio.Event()

    async def read_after_both_arrive(session, *, child_id):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_in.set()
        await asyncio.wait_for(both_in.wait(), timeout=2)
        return True

    monkeypatch.setattr(children, "child_health_granted", read_after_both_arrive)

    reply = {**BODY, "reply_to": "prev-run"}
    return await asyncio.gather(
        client.post(URL, json=reply, headers=_key()),
        client.post(URL, json=reply, headers=_key()),
    )


async def test_같은_되묻기에_거의_동시에_두_번_답하면_하나만_받는다(
    client, agent_calls, monkeypatch
):
    """맥락 확인(get)과 지우기(consume) 사이에 await 가 있으면 두 답이 다 확인을 통과한다.

    동의 읽기가 get 과 consume 사이에 있으면 둘 다 202 가 되고, 같은 답으로 run 이 두 개 떠
    관찰이 두 행이 된다.
    """
    first, second = await _answer_same_question_twice_at_once(client, monkeypatch)

    assert sorted([first.status_code, second.status_code]) == [202, 400]
    rejected = first if first.status_code == 400 else second
    assert rejected.json()["error"]["code"] == "reply_context_unavailable"
    assert len(agent_calls) == 1


async def test_같은_되묻기에_늦게_온_답은_하루_한도를_쓰지_않는다(client, agent_calls, monkeypatch):
    """늦게 온 답은 한도를 세기 전에 400 으로 끝난다 (#439 리뷰).

    순서는 두고 consume() 이 "이미 없음" 을 돌려줄 때 400 을 내는 방법도 위 테스트는 통과한다.
    하지만 그때는 한도를 이미 센 뒤라, 늦게 온 답이 400 을 받으면서도 하루 한도 1회를 잃는다.
    동의 읽기를 get 앞으로 옮긴 이유가 이것이라, 남은 횟수로 직접 건다.
    """
    from app.core.config import settings

    first, second = await _answer_same_question_twice_at_once(client, monkeypatch)
    assert sorted([first.status_code, second.status_code]) == [202, 400]

    # 받아들인 답 하나만 셌다 — 일반 입력이 한도 - 1 번 더 들어가고, 그다음이 429 다
    for _ in range(settings.INPUT_DAILY_LIMIT - 1):
        response = await client.post(URL, json=BODY, headers=_key())
        assert response.status_code == 202
    over = await client.post(URL, json=BODY, headers=_key())
    assert over.status_code == 429
