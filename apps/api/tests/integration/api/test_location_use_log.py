"""위치 이용 확인자료 — 위치정보법 제16조 ② · 위치 약관 제4조 ④ · 제10조 ① (#693).

여기서 지키는 것.
    ① 위치 동의를 통과한 좌표를 Agent 에 넘긴 입력마다 1건 남긴다 (커밋까지)
    ② 🚨 기록에는 좌표가 없다 — 처리방침이 위치 값을 남기지 않는다고 약속했다
    ③ 좌표를 넘기지 않은 입력(동의 없음 · 좌표 없음 · 같은 키 재생)은 남기지 않는다
    ④ 동의를 철회하면 그 보호자의 기록을 지운다
    ⑤ 6개월이 지나면 지운다. 탈퇴하면 보호자 행과 함께 지워진다

세션은 바깥 트랜잭션 안의 savepoint 로 열린다 (tests/conftest.py). 그래서 입력 뒤에 rollback 을
불러도 행이 남아 있으면 커밋된 것이다 — 커밋이 빠지면 약관의 "자동으로 기록" 이 조용히 거짓이 된다.
등장하는 사람은 전부 테스트용 가짜다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.api import idempotency, quota
from app.api.runs import pending_reply, registry, runner
from app.domains.consent.location_log import purge_expired_location_logs
from app.domains.consent.models import ConsentScope, LocationPurpose, LocationUseLog
from app.domains.consent.repository import grant_account_scope
from app.domains.identity.withdraw import withdraw_parent
from app.domains.policy.repository import find_active_versions

from .conftest import issue_bearer

INPUTS = "/api/v1/children/{cid}/inputs"
CONSENTS = "/api/v1/consents"
BODY = {"text": "오늘 어디 갈까요", "source": "home_input"}
LOCATION = {"lat": 35.18, "lon": 129.08}


@pytest.fixture(autouse=True)
def _clean_process_memory():
    registry.clear()
    idempotency.clear()
    quota.clear()
    pending_reply.clear()
    yield
    registry.clear()
    idempotency.clear()
    quota.clear()
    pending_reply.clear()


@pytest.fixture(autouse=True)
def agent_calls(monkeypatch) -> list[dict]:
    """진짜 Agent 대신 가짜 러너를 끼우고, 창구가 agent_job 에 넘긴 값을 기록한다."""
    calls: list[dict] = []

    def fake_agent_job(**kwargs):
        calls.append(kwargs)
        return runner.fake_job

    monkeypatch.setattr(runner, "agent_job", fake_agent_job)
    return calls


def with_key(headers: dict[str, str], key: str | None = None) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key or str(uuid.uuid4())}


async def location_version(session) -> tuple[uuid.UUID, str]:
    rows = await find_active_versions(session, now=datetime.now(UTC))
    row = next(row for row in rows if row.scope is ConsentScope.LOCATION)
    return row.id, row.version


async def grant_location(session, parent_id: uuid.UUID) -> None:
    version_id, _ = await location_version(session)
    await grant_account_scope(
        session, parent_id=parent_id, scope=ConsentScope.LOCATION, policy_version_id=version_id
    )
    await session.commit()


async def logs_of(session, parent_id: uuid.UUID) -> list[LocationUseLog]:
    rows = await session.scalars(
        select(LocationUseLog).where(LocationUseLog.parent_id == parent_id)
    )
    return list(rows.all())


def old_log(parent_id: uuid.UUID, used_at: datetime) -> LocationUseLog:
    return LocationUseLog(parent_id=parent_id, purpose=LocationPurpose.SUGGESTION, used_at=used_at)


# ── 남기기 ─────────────────────────────────────────────────────────


async def test_위치_동의를_통과한_입력은_기록이_1건_커밋된다(
    db_client, session, bearer, cid, agent_calls
):
    headers, parent_id = bearer
    await grant_location(session, parent_id)

    response = await db_client.post(
        INPUTS.format(cid=cid), json={**BODY, "location": LOCATION}, headers=with_key(headers)
    )
    await session.rollback()  # 커밋이 빠졌다면 여기서 행이 사라진다

    assert response.status_code == 202
    assert agent_calls[0]["location"] is not None
    logs = await logs_of(session, parent_id)
    assert [log.purpose for log in logs] == [LocationPurpose.SUGGESTION]
    assert abs(datetime.now(UTC) - logs[0].used_at) < timedelta(minutes=1)


async def test_기록_표에는_좌표를_담을_칸이_없다(db_client, session, bearer, cid):
    """🚨 칸을 더하려면 이 테스트가 먼저 멈춘다. 위치 값을 남기면 처리방침 · 약관이 거짓이 된다."""
    headers, parent_id = bearer
    await grant_location(session, parent_id)
    await db_client.post(
        INPUTS.format(cid=cid), json={**BODY, "location": LOCATION}, headers=with_key(headers)
    )

    row = (await session.execute(text("SELECT * FROM location_use_log"))).mappings().one()

    assert set(row) == {"id", "parent_id", "purpose", "used_at"}


async def test_위치_동의가_없으면_기록하지_않는다(db_client, session, bearer, cid, agent_calls):
    headers, parent_id = bearer

    await db_client.post(
        INPUTS.format(cid=cid), json={**BODY, "location": LOCATION}, headers=with_key(headers)
    )

    assert agent_calls[0]["location"] is None
    assert await logs_of(session, parent_id) == []


async def test_좌표가_없는_입력은_기록하지_않는다(db_client, session, bearer, cid):
    headers, parent_id = bearer
    await grant_location(session, parent_id)

    await db_client.post(INPUTS.format(cid=cid), json=BODY, headers=with_key(headers))

    assert await logs_of(session, parent_id) == []


async def test_같은_키로_다시_보내도_기록은_1건이다(db_client, session, bearer, cid):
    """재생은 Agent 를 다시 부르지 않는다. 쓰지 않은 위치를 썼다고 남기면 안 된다."""
    headers, parent_id = bearer
    await grant_location(session, parent_id)
    key = str(uuid.uuid4())
    url = INPUTS.format(cid=cid)
    payload = {**BODY, "location": LOCATION}

    await db_client.post(url, json=payload, headers=with_key(headers, key))
    await db_client.post(url, json=payload, headers=with_key(headers, key))

    assert len(await logs_of(session, parent_id)) == 1


# ── 철회 ───────────────────────────────────────────────────────────


async def test_위치_동의를_끄면_그_보호자의_기록만_지운다(db_client, session, bearer):
    headers, parent_id = bearer
    _, other_id = await issue_bearer(session, token="location-log-other")
    await grant_location(session, parent_id)
    session.add_all([old_log(parent_id, datetime.now(UTC)), old_log(other_id, datetime.now(UTC))])
    await session.commit()
    _, version = await location_version(session)

    response = await db_client.post(
        CONSENTS,
        json={"scope": "location", "action": "withdrawn", "policy_version": version},
        headers=headers,
    )
    await session.rollback()

    assert response.status_code == 201
    assert await logs_of(session, parent_id) == []
    assert len(await logs_of(session, other_id)) == 1


async def test_이미_끈_뒤에_다시_꺼도_남은_기록을_지운다(db_client, session, bearer):
    """접수와 철회가 겹쳐 철회 뒤에 들어온 기록은 끄기를 다시 누르면 지워진다."""
    headers, parent_id = bearer
    await grant_location(session, parent_id)
    _, version = await location_version(session)
    request = {"scope": "location", "action": "withdrawn", "policy_version": version}
    await db_client.post(CONSENTS, json=request, headers=headers)
    session.add(old_log(parent_id, datetime.now(UTC)))
    await session.commit()

    await db_client.post(CONSENTS, json=request, headers=headers)

    assert await logs_of(session, parent_id) == []


async def test_위치_동의를_켜는_것은_기록을_지우지_않는다(db_client, session, bearer):
    headers, parent_id = bearer
    session.add(old_log(parent_id, datetime.now(UTC)))
    await session.commit()
    _, version = await location_version(session)

    await db_client.post(
        CONSENTS,
        json={"scope": "location", "action": "granted", "policy_version": version},
        headers=headers,
    )

    assert len(await logs_of(session, parent_id)) == 1


# ── 6개월 · 탈퇴 ───────────────────────────────────────────────────


async def test_가장_긴_6개월이_끝나기_전에는_지우지_않는다(session, bearer):
    """7월 1일 ~ 1월 1일이 184일로 가장 길다. 이 안에서 지우면 "6개월 보관" 약속을 어긴다."""
    _, parent_id = bearer
    now = datetime(2026, 1, 1, tzinfo=UTC)
    kept = old_log(parent_id, datetime(2025, 7, 1, 0, 0, 1, tzinfo=UTC))
    expired = old_log(parent_id, datetime(2025, 7, 1, tzinfo=UTC))
    session.add_all([kept, expired])
    await session.flush()

    purged = await purge_expired_location_logs(session, now=now)

    assert purged == 1
    assert [log.id for log in await logs_of(session, parent_id)] == [kept.id]


async def test_탈퇴하면_기록이_보호자_행과_함께_지워진다(session, bearer):
    _, parent_id = bearer
    session.add(old_log(parent_id, datetime.now(UTC)))
    await session.flush()

    await withdraw_parent(session, parent_id=parent_id, now=datetime.now(UTC))

    assert await logs_of(session, parent_id) == []
