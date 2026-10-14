"""생일 · 동의를 값으로 감싼 포트 (#372 PR 2).

API 가 run 마다 한 번 읽어 넘긴 값을 `ChildProfileReader` · `ConsentReader` 로 내보낸다.
다른 아이를 물으면 이 아이의 값을 주지 않고 거부한다 — 아이 축이 섞이면 남의 생일 · 동의로
게이트가 열린다.
"""

from datetime import date
from uuid import UUID

import pytest

from app.agents.common.child_values import ChildValues
from app.agents.common.ports import ChildProfileReader, ConsentReader

CHILD = UUID(int=1)
OTHER = UUID(int=2)
BORN = date(2024, 3, 5)


async def test_생일을_값으로_돌려준다():
    values = ChildValues(child_id=CHILD, birth=BORN, health_consent=True)
    assert await values.birth_date(child_id=CHILD) == BORN


@pytest.mark.parametrize("granted", [True, False])
async def test_동의를_값으로_돌려준다(granted):
    values = ChildValues(child_id=CHILD, birth=BORN, health_consent=granted)
    assert await values.child_health_granted(child_id=CHILD) is granted


async def test_다른_아이를_물으면_거부한다():
    values = ChildValues(child_id=CHILD, birth=BORN, health_consent=True)
    with pytest.raises(LookupError):
        await values.birth_date(child_id=OTHER)
    with pytest.raises(LookupError):
        await values.child_health_granted(child_id=OTHER)


def test_두_포트_Protocol_을_모두_채운다():
    values = ChildValues(child_id=CHILD, birth=BORN, health_consent=False)
    profile: ChildProfileReader = values
    consent: ConsentReader = values
    assert profile is consent is values
