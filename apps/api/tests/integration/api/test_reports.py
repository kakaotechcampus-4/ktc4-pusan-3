"""앱 안 신고 · 문의 — `POST /reports` (#487).

Play 정책(AI 생성 콘텐츠 · 사용자 제작 콘텐츠)이 앱을 나가지 않고 신고하는 길을 요구한다.

🚨 보호자가 쓴 글은 DB 에만 둔다. 로그 · Discord 알림에는 분류와 신고 번호만 — 글에 아이 정보가
   섞일 수 있다 (루트 CLAUDE.md §2).
🚨 로그인한 보호자만 보낼 수 있다 (protected_router).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.api.v1.routers import reports as reports_router
from app.api.v1.routers.reports import REPORT_DAILY_LIMIT
from app.domains.identity.withdraw import withdraw_parent
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
    assert records[0].alert_source == "report"  # Discord 에서 report-alert 로 — 장애 알림과 갈린다
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
    assert "가" * 50 not in response.text  # 400 응답이 보낸 글을 되돌려주지 않는다


async def test_정확히_1000자는_받는다(db_client, bearer):
    headers, _ = bearer
    response = await db_client.post(
        PATH, json={"category": "other", "message": "가" * 1000}, headers=headers
    )
    assert response.status_code == 201, response.text


async def test_NUL_글자는_400_줄바꿈은_그대로(db_client, bearer, session):
    """NUL 은 Postgres text 가 받지 못해 500 · 가짜 DB 장애 알림이 된다. 앞에서 400 으로 막는다."""
    headers, _ = bearer
    bad = await db_client.post(
        PATH, json={"category": "other", "message": "a\u0000b"}, headers=headers
    )
    assert bad.status_code == 400

    ok = await db_client.post(
        PATH, json={"category": "other", "message": "첫 줄\n둘째 줄"}, headers=headers
    )
    assert ok.status_code == 201, ok.text
    row = (
        await session.execute(select(Report).where(Report.id == uuid.UUID(ok.json()["id"])))
    ).scalar_one()
    assert row.message == "첫 줄\n둘째 줄"


async def test_하루_상한을_넘으면_429(db_client, bearer):
    """한 사람이 연타해 Discord 의 분당 한도를 채우면 진짜 장애 알림이 묻힌다."""
    headers, _ = bearer
    for _ in range(REPORT_DAILY_LIMIT):
        sent = await db_client.post(
            PATH, json={"category": "bug", "message": MESSAGE}, headers=headers
        )
        assert sent.status_code == 201, sent.text

    over = await db_client.post(PATH, json={"category": "bug", "message": MESSAGE}, headers=headers)
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "too_many_reports"
    # 지난 24시간을 센다 — "오늘은" 이라 하면 밤에 다 쓴 보호자가 다음 날 아침에도 막힐 때 틀린다
    assert over.json()["error"]["message"].startswith("지금은")


async def test_모르는_칸은_받지_않는다(db_client, bearer):
    """아이 · 기록 id 같은 칸을 몰래 늘리지 못하게 막는다 — 칸이 늘면 새로 모으는 정보가 된다."""
    headers, _ = bearer
    response = await db_client.post(
        PATH,
        json={"category": "shared_record", "message": MESSAGE, "child_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert response.status_code == 400


async def test_탈퇴하면_보낸_신고도_지워진다(db_client, bearer, session):
    """처리방침 5장 — 탈퇴하면 개인 기록을 지운다.

    실제 탈퇴 함수로 지워서, 탈퇴 방식이 바뀌어도 이 테스트가 잡는다.
    """
    headers, parent_id = bearer
    response = await db_client.post(
        PATH, json={"category": "bug", "message": MESSAGE}, headers=headers
    )
    report_id = uuid.UUID(response.json()["id"])

    await withdraw_parent(session, parent_id=parent_id, now=datetime.now(UTC))
    await session.flush()
    session.expunge_all()

    gone = (
        await session.execute(select(Report).where(Report.id == report_id))
    ).scalar_one_or_none()
    assert gone is None


async def test_인증_뒤에_탈퇴됐으면_401이고_아무것도_남기지_않는다(
    db_client, bearer, session, caplog, monkeypatch
):
    """인증과 잠금 사이에 탈퇴가 커밋되면 FK 위반 500 이 아니라 401 (#487 리뷰).

    HTTP 만으로는 만들 수 없다 — 탈퇴하면 인증 세션도 지워져 인증에서 먼저 401 이다. 그래서 잠금
    결과만 바꿔 끼운다. 잠금 자체가 False 를 내는지는 test_report_concurrency.py 가 본다.
    """
    headers, parent_id = bearer

    async def gone(*_args, **_kwargs):
        return False

    monkeypatch.setattr(reports_router, "lock_reporter", gone)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        response = await db_client.post(
            PATH, json={"category": "bug", "message": MESSAGE}, headers=headers
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    rows = await session.execute(select(Report).where(Report.reporter_parent_id == parent_id))
    assert rows.scalars().all() == []
    assert [r for r in caplog.records if r.name == LOGGER] == []  # 알림도 가지 않는다
