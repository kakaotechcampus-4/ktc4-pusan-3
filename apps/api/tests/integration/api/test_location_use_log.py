"""위치 이용 확인자료 — 위치정보법 제16조 ② · 위치 약관 제4조 ④ · 제10조 ① (#693).

여기서 지키는 것.
    ① run 이 좌표를 Agent 에 넘길 때마다 1건 남긴다 (커밋까지)
    ② 🚨 기록에는 좌표가 없다 — 처리방침이 위치 값을 남기지 않는다고 약속했다
    ③ 위치를 넘기지 않은 요청은 남기지 않는다 — run 이 뜨기 전에 끝난 요청, 그새 동의를 끈 보호자
    ④ 동의를 철회하면 그 보호자의 기록을 지운다
    ⑤ 6개월이 지나면 지운다. 탈퇴하면 보호자 행과 함께 지워진다

철회와 기록이 동시에 오는 경우는 tests/integration/test_location_use_concurrency.py 가 본다.
러너가 여는 세션은 테스트 세션으로 바꿔 끼운다 (test_run_saved.py 와 같다). 그 세션은 바깥
트랜잭션 안의 savepoint 라, run 뒤에 rollback 을 불러도 행이 남아 있으면 커밋된 것이다.
등장하는 사람은 전부 테스트용 가짜다.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from app.agents import entrypoint
from app.agents.entrypoint import Done
from app.api import agent_ports, idempotency, quota
from app.api.runs import registry, runner
from app.core.config import settings
from app.domains.consent.location_log import purge_expired_location_logs
from app.domains.consent.models import ConsentScope, LocationPurpose, LocationUseLog
from app.domains.consent.repository import grant_account_scope
from app.domains.identity.withdraw import withdraw_parent
from app.domains.policy.repository import find_active_versions

from .conftest import issue_bearer

INPUTS = "/api/v1/children/{cid}/inputs"
CONSENTS = "/api/v1/consents"
RAW_TEXT = "오늘 어디 갈까요"
COORDS = entrypoint.CoarseLocation(lat=35.18, lon=129.08)


@pytest.fixture(autouse=True)
def _clean_process_memory(pending_db):
    """채널 · Idempotency 는 프로세스 메모리라 비운다. 한도는 표(rate_event, #877)라 `pending_db` 가
    테스트 트랜잭션에 묶는다."""
    for module in (registry, idempotency):
        module.clear()
    yield
    for module in (registry, idempotency):
        module.clear()


@pytest.fixture(autouse=True)
def _same_session(monkeypatch, session):
    """러너 · 포트가 여는 세션을 테스트 세션으로."""

    @asynccontextmanager
    async def same():
        yield session

    monkeypatch.setattr(runner, "background_session", same)
    monkeypatch.setattr(agent_ports, "background_session", same)


@pytest.fixture
def handed(monkeypatch) -> list:
    """진짜 Agent 대신 가짜 진입점. Agent 에 넘어간 좌표를 적는다."""
    seen: list = []

    async def fake_handle_input(**kwargs):
        seen.append(kwargs["location"])
        kwargs["emit"](Done(kwargs["run_id"], 1))

    monkeypatch.setattr(entrypoint, "handle_input", fake_handle_input)
    return seen


async def run_with_coords(cid: uuid.UUID, parent_id: uuid.UUID) -> registry.RunChannel:
    """창구가 동의를 확인하고 좌표를 넘긴 run 하나를 끝까지 돌린다."""
    channel = registry.open_run(parent_id=parent_id)
    job = runner.agent_job(child_id=cid, parent_id=parent_id, raw_text=RAW_TEXT, location=COORDS)
    await asyncio.wait_for(runner.start(channel, job, raw_text=RAW_TEXT), timeout=5)
    return channel


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


async def test_run_이_좌표를_넘기면_기록이_1건_커밋된다(session, bearer, cid, handed):
    _, parent_id = bearer
    await grant_location(session, parent_id)

    await run_with_coords(cid, parent_id)
    await session.rollback()  # 커밋이 빠졌다면 여기서 행이 사라진다

    assert handed == [COORDS]
    logs = await logs_of(session, parent_id)
    assert [log.purpose for log in logs] == [LocationPurpose.SUGGESTION]
    assert abs(datetime.now(UTC) - logs[0].used_at) < timedelta(minutes=1)


async def test_그새_위치_동의가_꺼졌으면_좌표를_버리고_기록하지_않는다(
    db_client, session, bearer, cid, handed
):
    """창구가 동의를 읽은 뒤 run 이 뜨기 전에 철회된 경우. run 은 위치 없이 끝까지 돈다.

    한 번도 안 켠 보호자가 아니라 켰다가 끈 보호자다 — "켠 적이 있으면 허용" 으로 잘못 읽어도
    여기서 멈춘다.
    """
    headers, parent_id = bearer
    await grant_location(session, parent_id)
    _, version = await location_version(session)
    withdrawn = await db_client.post(
        CONSENTS,
        json={"scope": "location", "action": "withdrawn", "policy_version": version},
        headers=headers,
    )
    assert withdrawn.status_code == 201

    channel = await run_with_coords(cid, parent_id)

    assert handed == [None]
    assert [name for name, _ in channel.events][-1] == "done"
    assert await logs_of(session, parent_id) == []


async def test_기록_표에는_좌표를_담을_칸이_없다(session):
    """🚨 칸을 더하려면 이 테스트가 먼저 멈춘다. 위치 값을 남기면 처리방침 · 약관이 거짓이 된다."""
    columns = await session.scalars(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'location_use_log'"
        )
    )

    assert set(columns) == {"id", "parent_id", "purpose", "used_at"}


async def test_하루_한도에_걸린_입력은_기록하지_않는다(db_client, session, bearer, cid, handed):
    """run 이 뜨지 않은 요청은 위치를 쓰지 않았다. 한도를 넘긴 반복 요청이 표를 키우지도 않는다."""
    headers, parent_id = bearer
    await grant_location(session, parent_id)
    for _ in range(settings.INPUT_DAILY_LIMIT):
        await quota.consume(
            parent_id=parent_id, today=quota.today_kst(), limit=settings.INPUT_DAILY_LIMIT
        )

    response = await db_client.post(
        INPUTS.format(cid=cid),
        json={"text": RAW_TEXT, "source": "home_input", "location": {"lat": 35.18, "lon": 129.08}},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 429
    assert handed == []
    assert await logs_of(session, parent_id) == []


async def test_입력_한_번은_기록_한_번이다_같은_키를_다시_보내도(
    db_client, session, bearer, cid, handed
):
    """창구(POST /inputs)부터 러너까지 실제 길로 간다. 같은 Idempotency-Key 를 다시 보내면 처음
    응답을 재생할 뿐 run 이 다시 뜨지 않으니, 기록도 늘지 않는다."""
    headers, parent_id = bearer
    await grant_location(session, parent_id)
    url = INPUTS.format(cid=cid)
    body = {"text": RAW_TEXT, "source": "home_input", "location": {"lat": 35.18, "lon": 129.08}}
    keyed = {**headers, "Idempotency-Key": str(uuid.uuid4())}

    first = await db_client.post(url, json=body, headers=keyed)
    assert first.status_code == 202
    await asyncio.wait_for(registry.get(first.json()["run_id"]).task, timeout=5)
    again = await db_client.post(url, json=body, headers=keyed)
    await session.rollback()  # 커밋이 빠졌다면 여기서 행이 사라진다

    assert again.json()["run_id"] == first.json()["run_id"]
    assert len(handed) == 1
    assert handed[0] is not None
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
