"""Agent 에 넘기는 `child_health` 동의의 현재 값 (#372 PR 2).

마지막 action 이 곧 현재 상태다 (consent 는 append-only). 한 번도 동의한 적이 없는 것(None)은
동의 안 함이고, 조회가 실패한 것은 그 값이 아니라 예외다 — 실패를 False 로 바꾸면 안전 필터가
있어야 할 자리에서 "건강정보 없음" 으로 읽힌다.
"""

import uuid

import pytest

from app.domains.consent.models import ConsentAction
from app.domains.consent.repository import child_health_granted

CHILD = uuid.UUID(int=1)


class _Session:
    """`session.scalar(stmt)` 만 쓴다. 마지막 action 을 돌려주거나, 실패한다."""

    def __init__(self, latest: ConsentAction | None = None, *, error: Exception | None = None):
        self._latest = latest
        self._error = error
        self.statements: list[object] = []

    async def scalar(self, stmt):
        self.statements.append(stmt)
        if self._error is not None:
            raise self._error
        return self._latest


@pytest.mark.parametrize(
    ("latest", "expected"),
    [
        (ConsentAction.GRANTED, True),
        (ConsentAction.WITHDRAWN, False),
        (None, False),  # 한 번도 동의한 적이 없다
    ],
)
async def test_마지막_action_이_현재_동의다(latest, expected):
    assert await child_health_granted(_Session(latest), child_id=CHILD) is expected


async def test_조회가_실패하면_False_로_바꾸지_않고_예외를_올린다():
    with pytest.raises(ConnectionError):
        await child_health_granted(_Session(error=ConnectionError("DB 끊김")), child_id=CHILD)


async def test_child_health_scope_를_이_아이로_한_번만_읽는다():
    session = _Session(ConsentAction.GRANTED)

    await child_health_granted(session, child_id=CHILD)

    assert len(session.statements) == 1
    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": False}))
    assert "consent.scope" in sql and "consent.child_id" in sql
    params = session.statements[0].compile().params
    assert "child_health" in {getattr(v, "value", v) for v in params.values()}
    assert CHILD in params.values()
