"""앱 안 신고 · 문의 — `POST /reports` (#487).

Play 정책(AI 생성 콘텐츠 · 사용자 제작 콘텐츠)이 앱을 나가지 않고 신고하는 길을 요구한다.

🚨 보호자가 쓴 글은 DB 에만 둔다. 로그 · Discord 알림에는 분류와 신고 번호만 — 글에 아이 정보가
   섞일 수 있다 (루트 CLAUDE.md §2).
🚨 로그인한 보호자만 보낼 수 있다 (protected_router).
"""

import logging
import uuid

from sqlalchemy import select

from app.domains.identity.models import Parent
from app.domains.report.models import Report, ReportCategory

PATH = "/api/v1/reports"
LOGGER = "app.api.v1.routers.reports"
MESSAGE = "추천 답변에 이상한 말이 있었어요"


async def test_신고를_저장하고_알림에는_분류와_번호만_싣는다(db_client, bearer, session, caplog):
    headers, parent_id = bearer
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        response = await db_client.post(
            PATH, json={"category": "ai_content", "message": f"  {MESSAGE}  "}, headers=headers
        )

    assert response.status_code == 201, response.text
    report_id = uuid.UUID(response.json()["id"])

    row = (await session.execute(select(Report).where(Report.id == report_id))).scalar_one()
    assert row.reporter_parent_id == parent_id
    assert row.category is ReportCategory.AI_CONTENT
    assert row.message == MESSAGE  # 앞뒤 공백은 뗀다
    assert row.handled_at is None

    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1
    line = records[0].getMessage()
    assert "ai_content" in line
    assert str(report_id) in line
    # 글은 로그에도, Discord 로 가는 값에도 없다
    assert MESSAGE not in line
    assert records[0].alert_heading == "신고 접수"
    assert records[0].alert_detail == f"ai_content · `{report_id}`"
    assert MESSAGE not in records[0].alert_detail
    assert str(parent_id) not in records[0].alert_detail


async def test_로그인하지_않으면_401(db_client):
    response = await db_client.post(PATH, json={"category": "bug", "message": MESSAGE})
    assert response.status_code == 401


async def test_모르는_분류는_400(db_client, bearer):
    headers, _ = bearer
    response = await db_client.post(
        PATH, json={"category": "spam", "message": MESSAGE}, headers=headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"


async def test_공백뿐인_글은_400(db_client, bearer):
    headers, _ = bearer
    response = await db_client.post(
        PATH, json={"category": "other", "message": "   "}, headers=headers
    )
    assert response.status_code == 400


async def test_1000자를_넘는_글은_400(db_client, bearer):
    headers, _ = bearer
    response = await db_client.post(
        PATH, json={"category": "other", "message": "가" * 1001}, headers=headers
    )
    assert response.status_code == 400


async def test_모르는_칸은_받지_않는다(db_client, bearer):
    """아이 · 기록 id 같은 칸을 몰래 늘리지 못하게 막는다 — 칸이 늘면 새로 모으는 정보가 된다."""
    headers, _ = bearer
    response = await db_client.post(
        PATH,
        json={"category": "shared_record", "message": MESSAGE, "child_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert response.status_code == 400


async def test_탈퇴로_보호자_행이_지워지면_보낸_신고도_지워진다(db_client, bearer, session):
    """처리방침 5장 — 탈퇴하면 개인 기록을 지운다. 신고는 FK CASCADE 로 함께 사라진다."""
    headers, parent_id = bearer
    response = await db_client.post(
        PATH, json={"category": "bug", "message": MESSAGE}, headers=headers
    )
    report_id = uuid.UUID(response.json()["id"])

    parent = (await session.execute(select(Parent).where(Parent.id == parent_id))).scalar_one()
    await session.delete(parent)
    await session.flush()
    session.expunge_all()

    gone = (
        await session.execute(select(Report).where(Report.id == report_id))
    ).scalar_one_or_none()
    assert gone is None
