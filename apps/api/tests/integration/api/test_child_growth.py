"""키 · 몸무게 기록 — `/children/{cid}/growth` 의 GET · POST · PATCH · DELETE (C 묶음).

화면(`apps/web/src/lib/api/types.ts` 의 GrowthLog · CreateGrowthLogRequest ·
UpdateGrowthLogRequest)과
목 서버(`apps/web/src/mocks/handlers/profile.ts`)가 이미 기대하는 모양을 서버가 지킨다.

여기서 지키는 것.
    ① 기록이지 지표가 아니다 — 증감 · 백분위 · 또래 비교 칸이 응답에 없다 (루트 CLAUDE.md §2)
    ② 둘 다 빈 기록은 없다 — 만들 때도, 고친 결과도
    ③ 고치기는 보낸 칸만 바꾸고, 키 · 몸무게의 null 은 "안 잰 것으로 되돌리기" 다
    ④ "1주 전" 같은 문구는 서버가 만든다 — 기록 탭과 같은 규칙(rules/observed_label.py)
    ⑤ 다른 아이의 기록 id 는 없는 것과 같다 (404)

등장하는 사람은 전부 테스트용 가짜다. 보호자 1 = 아이 A 를 등록한 보호자,
보호자 3 = 아이 A 와 상관없는 보호자(아이 B 의 보호자). 키 · 몸무게도 가짜 값이다.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.api.v1.routers import children as children_router
from app.domains.child.models import ChildGrowthLog
from app.domains.child.repository import create_growth_log

from .conftest import issue_bearer, link_child

TODAY = date(2026, 10, 5)
"""문구("1주 전")가 날짜에 따라 바뀌어서 "오늘" 을 고정한다. 픽스처 아이 생일은 2023-01-01."""


def growth_url(child_id, log_id=None) -> str:
    base = f"/api/v1/children/{child_id}/growth"
    return base if log_id is None else f"{base}/{log_id}"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(children_router, "today_kst", lambda: TODAY)


async def add_log(session, child_id, parent_id, measured_on, height=None, weight=None):
    return await create_growth_log(
        session,
        child_id=child_id,
        created_by=parent_id,
        measured_on=measured_on,
        height_cm=None if height is None else Decimal(str(height)),
        weight_kg=None if weight is None else Decimal(str(weight)),
    )


# ── GET ────────────────────────────────────────────────────────────


async def test_목록은_잰_날_최신순이고_서버_문구가_붙는다(db_client, session, bearer, cid):
    headers, parent_1 = bearer
    older = await add_log(session, cid, parent_1, date(2026, 9, 28), height=103.0)
    newer = await add_log(session, cid, parent_1, date(2026, 10, 5), height=104.2, weight=17.1)

    response = await db_client.get(growth_url(cid), headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(newer.id),
                "measured_on": "2026-10-05",
                "height_cm": 104.2,
                "weight_kg": 17.1,
                "measured_label": "오늘",
                "note": None,
            },
            {
                "id": str(older.id),
                "measured_on": "2026-09-28",
                "height_cm": 103.0,
                "weight_kg": None,
                "measured_label": "1주 전",
                "note": None,
            },
        ],
        "next_cursor": None,
    }


async def test_다른_아이의_기록은_목록에_없다(db_client, session, bearer, cid):
    headers, _ = bearer
    _, parent_3 = await issue_bearer(session, token="test-token-stranger")
    child_b = await link_child(session, parent_id=parent_3)
    await add_log(session, child_b, parent_3, date(2026, 10, 1), weight=12.0)

    response = await db_client.get(growth_url(cid), headers=headers)

    assert response.json()["items"] == []


async def test_보호자3이_남의_아이_기록을_보면_403(db_client, session, cid):
    headers, _ = await issue_bearer(session, token="test-token-stranger")

    response = await db_client.get(growth_url(cid), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "child_access_denied"


# ── POST ───────────────────────────────────────────────────────────


async def test_기록을_더하면_201_과_서버_문구(db_client, session, bearer, cid):
    headers, parent_1 = bearer

    response = await db_client.post(
        growth_url(cid), json={"measured_on": "2026-10-01", "weight_kg": 15.4}, headers=headers
    )

    assert response.status_code == 201
    log = response.json()["log"]
    assert (log["measured_on"], log["height_cm"], log["weight_kg"]) == ("2026-10-01", None, 15.4)
    assert log["measured_label"] == "4일 전"
    row = await session.scalar(select(ChildGrowthLog).where(ChildGrowthLog.id == log["id"]))
    assert (row.child_id, row.created_by) == (cid, parent_1)


@pytest.mark.parametrize(
    "body",
    [
        {"measured_on": "2026-10-01"},  # 잰 것이 없다
        {"measured_on": "2026-10-01", "height_cm": None, "weight_kg": None},
        {"height_cm": 99.1},  # 잰 날이 없다
        {"measured_on": "2026-10-06", "height_cm": 99.1},  # 오늘(한국 날짜)보다 뒤
        {"measured_on": "2022-12-31", "height_cm": 99.1},  # 생일(2023-01-01)보다 앞
        {"measured_on": "2026-10-01", "height_cm": 0},
        {"measured_on": "2026-10-01", "weight_kg": 300.1},
        # 소수 한 자리로 반올림하면 0 이 된다 — DB 의 "0 보다 커야" 에 닿기 전에 400
        {"measured_on": "2026-10-01", "height_cm": 0.04},
    ],
)
async def test_더할_수_없는_기록은_400(db_client, session, bearer, cid, body):
    headers, _ = bearer

    response = await db_client.post(growth_url(cid), json=body, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    rows = await session.scalars(select(ChildGrowthLog).where(ChildGrowthLog.child_id == cid))
    assert rows.all() == []


async def test_소수_둘째_자리는_반올림해_저장하고_저장된_값을_돌려준다(
    db_client, session, bearer, cid
):
    # DB 칸이 소수 한 자리(docs/agents/data_model.md 09-22)라,
    # 서버가 먼저 반올림하고 그 값을 돌려준다
    headers, _ = bearer

    response = await db_client.post(
        growth_url(cid),
        json={"measured_on": "2026-10-01", "height_cm": 98.55, "weight_kg": 300},
        headers=headers,
    )

    assert response.status_code == 201
    log = response.json()["log"]
    assert (log["height_cm"], log["weight_kg"]) == (98.6, 300.0)
    await session.rollback()
    row = await session.scalar(select(ChildGrowthLog).where(ChildGrowthLog.id == log["id"]))
    assert (row.height_cm, row.weight_kg) == (Decimal("98.6"), Decimal("300.0"))


# ── PATCH ──────────────────────────────────────────────────────────


async def test_키를_null_로_보내면_키만_비운다(db_client, session, bearer, cid):
    headers, parent_1 = bearer
    log = await add_log(session, cid, parent_1, date(2026, 10, 1), height=99.1, weight=15.4)

    response = await db_client.patch(
        growth_url(cid, log.id), json={"height_cm": None}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()["log"]
    assert (body["height_cm"], body["weight_kg"], body["measured_on"]) == (
        None,
        15.4,
        "2026-10-01",
    )


async def test_날짜를_고치면_문구도_다시_만든다(db_client, session, bearer, cid):
    headers, parent_1 = bearer
    log = await add_log(session, cid, parent_1, date(2026, 10, 1), weight=15.4)

    response = await db_client.patch(
        growth_url(cid, log.id), json={"measured_on": "2026-09-20"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["log"]["measured_label"] == "2주 전"


@pytest.mark.parametrize(
    "body",
    [
        {"weight_kg": None},  # 고친 결과가 둘 다 빈다
        {"measured_on": None},  # 잰 날은 비울 수 없다
        {"measured_on": "2026-10-06"},  # 오늘보다 뒤
        {"height_cm": 0},
    ],
)
async def test_고칠_수_없는_내용은_400_이고_그대로다(db_client, session, bearer, cid, body):
    headers, parent_1 = bearer
    log = await add_log(session, cid, parent_1, date(2026, 10, 1), weight=15.4)

    response = await db_client.patch(growth_url(cid, log.id), json=body, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    await session.refresh(log)
    assert (log.measured_on, log.height_cm, log.weight_kg) == (
        date(2026, 10, 1),
        None,
        Decimal("15.40"),
    )


async def test_바뀐_칸이_없으면_그대로_200(db_client, session, bearer, cid):
    # 고치기 시트는 아무것도 안 바꾸고 저장하면 {} 를 보낸다 (growth-sheet.tsx) — 목과 같게 지금 줄
    headers, parent_1 = bearer
    log = await add_log(session, cid, parent_1, date(2026, 10, 1), weight=15.4)

    response = await db_client.patch(growth_url(cid, log.id), json={}, headers=headers)

    assert response.status_code == 200
    assert response.json()["log"]["weight_kg"] == 15.4


async def test_다른_아이의_기록_id_로_고치면_404(db_client, session, bearer, cid):
    headers, _ = bearer
    _, parent_3 = await issue_bearer(session, token="test-token-stranger")
    child_b = await link_child(session, parent_id=parent_3)
    log_b = await add_log(session, child_b, parent_3, date(2026, 10, 1), weight=12.0)

    response = await db_client.patch(
        growth_url(cid, log_b.id), json={"weight_kg": 13.0}, headers=headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    await session.refresh(log_b)
    assert log_b.weight_kg == Decimal("12.00")


# ── DELETE ─────────────────────────────────────────────────────────


async def test_지우면_204_이고_다시_지우면_404(db_client, session, bearer, cid):
    headers, parent_1 = bearer
    log = await add_log(session, cid, parent_1, date(2026, 10, 1), weight=15.4)

    first = await db_client.delete(growth_url(cid, log.id), headers=headers)
    second = await db_client.delete(growth_url(cid, log.id), headers=headers)

    assert first.status_code == 204
    assert second.status_code == 404
    listed = await db_client.get(growth_url(cid), headers=headers)
    assert listed.json()["items"] == []


async def test_다른_아이의_기록은_지울_수_없다(db_client, session, bearer, cid):
    headers, _ = bearer
    _, parent_3 = await issue_bearer(session, token="test-token-stranger")
    child_b = await link_child(session, parent_id=parent_3)
    log_b = await add_log(session, child_b, parent_3, date(2026, 10, 1), weight=12.0)

    response = await db_client.delete(growth_url(cid, log_b.id), headers=headers)

    assert response.status_code == 404
    still = await session.scalar(select(ChildGrowthLog.id).where(ChildGrowthLog.id == log_b.id))
    assert still == log_b.id
