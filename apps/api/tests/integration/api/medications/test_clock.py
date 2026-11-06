"""복약 API 의 시계 — 한국 오늘 계산과 자정 경계 (#577).

체크 · 수정은 지금과 오늘을 시계 한 번으로 만든다. 두 시계를 따로 읽으면 자정을 넘는 순간 어긋난다.
"""

from datetime import UTC, date, datetime

from app.api.v1.routers import medications as medications_router
from app.domains.medication.repository import list_doses

from .helpers import (
    TODAY,
    dose_of,
    patch,
    put_check,
    revise_body,
    seed_course,
)

REAL_KST_TODAY = medications_router.kst_today  # 아래 autouse 픽스처가 바꿔 끼우기 전의 함수


def test_kst_오늘은_UTC_날짜가_아니라_한국_날짜다(monkeypatch):
    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            # UTC 로는 10/26 16:00, 한국 시간으로는 10/27 01:00 — 날짜가 갈리는 시각이다
            return datetime(2026, 10, 26, 16, 0, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(medications_router, "datetime", FakeDateTime)

    assert REAL_KST_TODAY() == date(2026, 10, 27)


async def test_수정은_요청_도중_자정을_넘어도_이미_지난_날의_회차를_지우지_않는다(
    db_client, session, bearer, consented, monkeypatch
):
    course = await seed_course(session, consented, starts=-1, ends=1)  # 오늘 = 10/27
    # 요청이 자정을 넘는 순간 — 두 시계가 다른 날을 가리킨다: kst_today() 는 10/27,
    # utc_now() 는 한국 시간 10/28 00:00:00.5
    monkeypatch.setattr(medications_router, "kst_today", lambda: TODAY)
    monkeypatch.setattr(
        medications_router, "utc_now", lambda: datetime(2026, 10, 27, 15, 0, 0, 500000, tzinfo=UTC)
    )

    response = await patch(db_client, bearer[0], consented, course.id, revise_body())

    assert response.status_code == 200
    kept = [
        d.scheduled_time.strftime("%H:%M")
        for d in await list_doses(session, schedule_ids=[course.id])
        if d.dose_date == TODAY
    ]
    assert kept == ["08:30", "12:30", "19:30"]  # 10/27 은 이미 지났다 — 하나도 지우지 않는다


async def test_체크는_요청_도중_자정을_넘으면_새_날의_회차를_오늘로_본다(
    db_client, session, bearer, consented, monkeypatch
):
    # 두 시계가 다른 날을 가리키는 순간 — kst_today() 는 10/27,
    # utc_now() 는 한국 시간 10/28 00:00:00.5. 시계를 한 번만 읽으므로 "오늘" 은 10/28 이고
    # 10/28 의 회차는 체크할 수 있다
    _, dose = await dose_of(session, consented, days=1)  # TODAY(10/27) 의 다음 날 = 10/28
    monkeypatch.setattr(medications_router, "kst_today", lambda: TODAY)
    monkeypatch.setattr(
        medications_router, "utc_now", lambda: datetime(2026, 10, 27, 15, 0, 0, 500000, tzinfo=UTC)
    )

    response = await put_check(db_client, bearer[0], consented, dose.id, {"checked": True})

    assert response.status_code == 200
