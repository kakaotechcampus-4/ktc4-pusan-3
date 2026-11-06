"""복약 서비스 · 저장소 테스트의 공통 픽스처 (#577).

두 픽스처 모두 테스트의 `session` 위에서 만들고 끝나면 롤백된다. 별도 세션으로 실제 commit 하는
동시성 테스트(`test_medication_concurrency.py`)는 자기 정리를 하는 `make_child` 를 쓴다.
"""

from datetime import date

import pytest

from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.identity.models import Parent


@pytest.fixture
async def child(session):
    """보호자 1명과 그 아이 1명. 별명 · 생일은 가짜 값이다.

    아이 등록(`create_child`)처럼 등록한 보호자의 연결 행도 만든다 — 코스 등록 · 알림 구독이
    연결을 확인한다(#749).
    """
    owner = Parent()
    session.add(owner)
    await session.flush()
    child = Child(owner_parent_id=owner.id, nickname="test", birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    session.add(
        ParentChild(parent_id=owner.id, child_id=child.id, relation=ParentChildRelation.OTHER)
    )
    await session.flush()
    return child


@pytest.fixture
async def other_child(session, child):
    """`child` 와 다른 보호자의 다른 아이 — 아이 사이가 섞이지 않는지 볼 때 쓴다.

    보호자는 아이 1명에만 연결된다(`uq_parent_child_parent_id`) — 그래서 보호자도 따로 둔다.
    """
    owner = Parent()
    session.add(owner)
    await session.flush()
    other = Child(owner_parent_id=owner.id, nickname="other", birth_date=date(2022, 1, 1))
    session.add(other)
    await session.flush()
    session.add(
        ParentChild(parent_id=owner.id, child_id=other.id, relation=ParentChildRelation.OTHER)
    )
    await session.flush()
    return other
