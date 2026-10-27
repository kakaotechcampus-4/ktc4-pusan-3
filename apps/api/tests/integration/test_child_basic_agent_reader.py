"""child → Health 응급 카드가 읽는 아이 별명 · 생일 어댑터를 실제 DB 로."""

from contextlib import asynccontextmanager
from datetime import date
from uuid import UUID

import pytest

from app.agents.health.store.ports import ChildBasic
from app.domains.child.agent_reader import DbChildBasicReader
from tests.integration.agents.health.test_medication_store_contract_db import new_child


def reader(session) -> DbChildBasicReader:
    @asynccontextmanager
    async def open_session():
        yield session  # 테스트 트랜잭션 안에서 읽는다 — 바깥 rollback 이 지운다

    return DbChildBasicReader(open_session)


async def test_별명과_생일을_읽는다(session):
    child = await new_child(session, "별이")

    basic = await reader(session).basic(child_id=child.id)

    assert basic == ChildBasic(nickname="별이", birth_date=date(2023, 1, 1))


async def test_없는_아이는_LookupError(session):
    with pytest.raises(LookupError):
        await reader(session).basic(child_id=UUID(int=404))
