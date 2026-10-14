"""아이 성별 칸 · 키·몸무게 기록 표가 DB 에서 지키는 것 (C 묶음).

    ① 성별을 정하지 않은 아이는 "밝히지 않을래요"(undisclosed) — 루트 CLAUDE.md §2 기본값
    ② 성별은 세 값만 받는다
    ③ 키·몸무게 기록은 둘 다 비면 거부한다 — 잰 것이 없는 기록이다
    ④ 키·몸무게는 0 보다 커야 한다

ORM 이 아니라 SQL 로 넣는다 — 화면 · 서버 코드를 거치지 않고 들어와도 DB 가 막는지 본다.
보호자 탈퇴 · 아이 삭제 때의 동작은 공동 기록 목록(test_writer_deletion.py 의 RECORDS)에서 본다.
별명 · 생일 · 키 · 몸무게는 전부 가짜 값이다 (루트 CLAUDE.md §9).
"""

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domains.identity.models import Parent


async def _child(session) -> UUID:
    parent = Parent()
    session.add(parent)
    await session.flush()
    return await session.scalar(
        text(
            "INSERT INTO child (nickname, owner_parent_id, birth_date) "
            "VALUES ('테스트아이', :owner, '2023-01-01') RETURNING id"
        ),
        {"owner": parent.id},
    )


async def test_성별을_정하지_않은_아이는_undisclosed(session):
    child_id = await _child(session)

    gender = await session.scalar(text("SELECT gender FROM child WHERE id = :id"), {"id": child_id})

    assert gender == "undisclosed"


async def test_성별은_세_값만_받는다(session):
    child_id = await _child(session)

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE child SET gender = 'unknown' WHERE id = :id"), {"id": child_id}
            )


async def test_한쪽만_잰_기록은_들어간다(session):
    child_id = await _child(session)

    row = (
        await session.execute(
            text(
                "INSERT INTO child_growth_log (child_id, measured_on, weight_kg) "
                "VALUES (:child, '2026-10-01', 15.4) RETURNING height_cm, weight_kg"
            ),
            {"child": child_id},
        )
    ).one()

    assert row.height_cm is None
    assert float(row.weight_kg) == 15.4


async def test_키도_몸무게도_없는_기록은_거부한다(session):
    child_id = await _child(session)

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO child_growth_log (child_id, measured_on) "
                    "VALUES (:child, '2026-10-01')"
                ),
                {"child": child_id},
            )


@pytest.mark.parametrize("column", ["height_cm", "weight_kg"])
async def test_키_몸무게는_0_보다_커야_한다(session, column):
    child_id = await _child(session)

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text(
                    f"INSERT INTO child_growth_log (child_id, measured_on, {column}) "
                    "VALUES (:child, '2026-10-01', 0)"
                ),
                {"child": child_id},
            )
