"""한 줄 입력 접수 — 계약서 §06 `POST /children/{cid}/inputs`.

즉시 202 로 run_id 만 주고, 결과는 전부 `GET /runs/{rid}/events` 로 흐른다.
접수는 Agent 를 기다리지 않는다 — 채널을 열고 러너를 뒤에서 돌리고 바로 돌아온다.

Idempotency 는 계약서 §01 "헤더가 없으면 400" 에 더해 docs/api/idempotency-v1.md 의
"같은 키 · 같은 보호자 → 처음 응답 재생" 까지만 한다. 422·409·24시간 보관은 다음 이슈다.
프론트 mutation 은 retry: false 라 중복은 보호자가 버튼을 다시 누를 때만 생기는데,
그때 재생이 없으면 관찰이 두 번 저장되고 홈 숫자가 2배가 된다.

🚨 3단계에서는 아이 소유 확인(403)을 하지 않는다 — 9단계. cid 는 아무 UUID 나 받는다.
"""

import asyncio
import uuid

import pytest

from app.api import idempotency
from app.api.runs import registry, runner

from .conftest import issue_bearer

INPUTS = "/api/v1/children/{cid}/inputs"
EVENTS = "/api/v1/runs/{rid}/events"
BODY = {"text": "계란말이 또 찾아요", "source": "home_input"}


@pytest.fixture(autouse=True)
def _clean_process_memory(monkeypatch):
    """채널과 Idempotency 기억은 프로세스 메모리라 테스트 사이에 샌다. 가짜 러너의 지연도 없앤다."""
    registry.clear()
    idempotency.clear()
    monkeypatch.setattr(runner, "DEMO_STEP_DELAY", 0.0)
    yield
    registry.clear()
    idempotency.clear()


def with_key(headers: dict[str, str], key: str | None = None) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key or str(uuid.uuid4())}


async def test_inputs_requires_authentication(db_client):
    """🚨 무인증 예외는 auth 5개뿐이다 (루트 CLAUDE.md §9)."""
    response = await db_client.post(INPUTS.format(cid=uuid.uuid4()), json=BODY)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_inputs_rejects_missing_idempotency_key(db_client, bearer):
    """계약서 §01 — 되돌릴 수 없는 POST 는 헤더가 없으면 400."""
    headers, _ = bearer

    response = await db_client.post(INPUTS.format(cid=uuid.uuid4()), json=BODY, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


async def test_inputs_rejects_empty_text(db_client, bearer):
    """빈 줄을 Agent 에 보내면 모델 호출 한 번이 그냥 낭비다. 본문 오류라 400 validation_failed."""
    headers, _ = bearer

    response = await db_client.post(
        INPUTS.format(cid=uuid.uuid4()),
        json={"text": "   ", "source": "home_input"},
        headers=with_key(headers),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"


async def test_inputs_accepts_and_returns_run_id(db_client, bearer):
    """202 에 본문이 있어야 한다 — 프론트가 202 도 JSON 으로 파싱한다 (client.ts).

    빈 본문을 주면 화면에서 `res.run_id` 를 읽는 순간 터진다.
    """
    headers, _ = bearer

    response = await db_client.post(
        INPUTS.format(cid=uuid.uuid4()), json=BODY, headers=with_key(headers)
    )

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert registry.get(run_id) is not None  # 채널이 열려 있어야 GET 이 붙을 수 있다


async def test_same_key_replays_the_same_run_id(db_client, bearer):
    """같은 키로 다시 오면 새 run 을 만들지 않고 처음 응답을 그대로 준다.

    보호자가 버튼을 두 번 누른 경우다. 새 run 이 생기면 관찰이 두 번 저장된다.
    같은 아이(같은 경로)여야 한다 — 스코프에 path 가 들어 있어 다른 아이면 다른 요청이다.
    """
    headers, _ = bearer
    keyed = with_key(headers)
    cid = uuid.uuid4()

    first = await db_client.post(INPUTS.format(cid=cid), json=BODY, headers=keyed)
    second = await db_client.post(INPUTS.format(cid=cid), json=BODY, headers=keyed)

    assert second.status_code == 202
    assert second.json() == first.json()


async def test_failed_run_forgets_its_key(db_client, bearer, monkeypatch):
    """실패로 끝난 run 의 키는 지운다 — 같은 키로 "다시 시도" 하면 새 run 이 떠야 한다.

    프론트는 실패 뒤 재시도에 같은 키를 쓴다(home/page.tsx `retry()`). 키가 남아 있으면
    이미 닫힌 실패 run 이 재생돼 몇 번을 눌러도 같은 실패만 받는다.
    (idempotency-v1 §3-4 — 기억하는 것은 성공뿐이다)
    """

    async def boom(channel):
        raise RuntimeError("일부러 낸 실패")

    monkeypatch.setattr(runner, "fake_job", boom)
    headers, _ = bearer
    keyed = with_key(headers)
    cid = uuid.uuid4()

    first = await db_client.post(INPUTS.format(cid=cid), json=BODY, headers=keyed)
    await asyncio.wait_for(registry.get(first.json()["run_id"]).task, timeout=2)
    retried = await db_client.post(INPUTS.format(cid=cid), json=BODY, headers=keyed)

    assert retried.status_code == 202
    assert retried.json()["run_id"] != first.json()["run_id"]


async def test_different_key_starts_a_new_run(db_client, bearer):
    headers, _ = bearer

    first = await db_client.post(
        INPUTS.format(cid=uuid.uuid4()), json=BODY, headers=with_key(headers)
    )
    second = await db_client.post(
        INPUTS.format(cid=uuid.uuid4()), json=BODY, headers=with_key(headers)
    )

    assert first.json()["run_id"] != second.json()["run_id"]


async def test_key_is_scoped_per_parent(db_client, bearer, session):
    """키 스코프는 (parent_id, method, path, key) — 보호자 사이에 키가 섞이지 않는다.

    (idempotency-v1 §3-5) 다른 보호자가 우연히 같은 키를 보내도 남의 run_id 를 받아 가면 안 된다.
    """
    headers_a, _ = bearer
    headers_b, _ = await issue_bearer(session, token="test-token-other-parent")
    key = str(uuid.uuid4())
    cid = uuid.uuid4()

    a = await db_client.post(INPUTS.format(cid=cid), json=BODY, headers=with_key(headers_a, key))
    b = await db_client.post(INPUTS.format(cid=cid), json=BODY, headers=with_key(headers_b, key))

    assert a.json()["run_id"] != b.json()["run_id"]


async def test_other_parent_cannot_open_the_run(db_client, bearer, session, monkeypatch):
    """🚨 run 은 만든 보호자만 연다. 남의 run 은 없는 run 과 똑같이 404 — 있다는 것도 흘리지 않는다.

    `failed` 는 보호자가 적은 문장(raw_text)을 그대로 싣는다. 로그인만 했으면 남의 run_id 로
    그 문장을 받아 갈 수 있었다 (#140 리뷰 재현). 주소에 아이 id 가 없어 아이 소유 403(9단계)으로는
    못 막는다 — auth-kakao-v1 부록 A 1번 "간접 식별자는 소유를 역추적해 검사한다".
    """

    async def boom(channel):
        raise RuntimeError("일부러 낸 실패")

    monkeypatch.setattr(runner, "fake_job", boom)
    headers_a, _ = bearer
    headers_b, _ = await issue_bearer(session, token="test-token-other-parent")

    accepted = await db_client.post(
        INPUTS.format(cid=uuid.uuid4()), json=BODY, headers=with_key(headers_a)
    )
    run_id = accepted.json()["run_id"]
    await asyncio.wait_for(registry.get(run_id).task, timeout=2)

    response = await db_client.get(EVENTS.format(rid=run_id), headers=headers_b)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert BODY["text"] not in response.text


async def test_post_then_get_streams_steps_to_done(db_client, bearer):
    """서버 쪽 관통 — 접수하고 이어서 GET 하면 step → … → done 이 흐른다.

    🚨 GET 전에 러너 태스크를 기다린다. 채널이 닫혀야 응답이 끝나고, 안 닫히면 테스트가 멈춘다.
    """
    headers, _ = bearer

    accepted = await db_client.post(
        INPUTS.format(cid=uuid.uuid4()), json=BODY, headers=with_key(headers)
    )
    run_id = accepted.json()["run_id"]
    channel = registry.get(run_id)
    # 태스크를 채널에 매달지 않으면 GC 가 거둬 run 이 조용히 사라진다
    assert channel.task is not None
    await asyncio.wait_for(channel.task, timeout=2)

    response = await db_client.get(EVENTS.format(rid=run_id), headers=headers)

    assert response.status_code == 200
    frames = [f for f in response.text.split("\n\n") if f]
    assert frames[0].startswith("event: step\n")
    assert frames[-1].startswith("event: done\n")
    assert f'"run_id":"{run_id}"' in frames[-1]
