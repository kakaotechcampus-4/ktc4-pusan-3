"""08 사진으로 적기 — 읽기는 아무것도 저장하지 않고, 저장은 commit 하나다.

모델은 부르지 않는다. 읽은 결과를 직접 심고 저장 · 승인 규칙만 본다.
🚨 공지 글은 지어낸 것이다 (CLAUDE.md §9 — 실제 기관 문서를 픽스처에 넣지 않는다).
"""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import entrypoint
from app.api.runs import photo_run
from app.core.config import settings
from app.core.notice_extract import ExtractedItem, ItemDetail, NoticeExtract
from app.domains.child.models import ParentChild
from app.domains.daycare_meal.models import DaycareMeal
from app.domains.notice.assemble import assemble
from app.domains.notice.models import Notice, NoticeInputFormat, NoticeItem
from app.domains.schedule.models import Event, EventCategory, EventCreatedBy, EventType
from app.integrations import photo_uploads
from app.providers.meal_plan.schema import (
    Meal,
    MealDay,
    MealPlanJSON,
    MealPlanSource,
    MealType,
    MenuItem,
    UnparsedCell,
)
from tests.integration.api.conftest import issue_bearer, link_child

KST = ZoneInfo("Asia/Seoul")
# 사진 run 의 읽은 결과는 표(run_hold, #877)라 `pending_db` 가 테스트 트랜잭션에 묶는다
pytestmark = pytest.mark.usefixtures("pending_db")

PHOTOS = "/api/v1/children/{cid}/photos"
COMMIT = "/api/v1/photo-runs/{rid}/commit"
SOURCE = "숲 체험 안내\n일시: 11월 6일(금) 9:30~12:00\n참가 동의서는 11월 3일(화) 14시까지 회신"


def _extract() -> NoticeExtract:
    base = {
        "target_scope": "all",
        "details": [],
    }
    return NoticeExtract(
        doc_kind="newsletter",
        audience_scope="institution",
        title="숲 체험 안내",
        issued_text="2026. 10. 27.",
        is_correction=False,
        person_names=[],
        institution_names=[],
        items=[
            ExtractedItem(
                handling="calendar",
                label="행사",
                title="숲 체험",
                date_text="11월 6일(금) 9:30~12:00",
                due_text=None,
                supplies=["물통"],
                source_quote="일시: 11월 6일(금) 9:30~12:00",
                **{**base, "details": [ItemDetail(name="장소", value="숲체험장")]},
            ),
            ExtractedItem(
                handling="todo",
                label="회신",
                title="참가 동의서 회신",
                date_text=None,
                due_text="11월 3일(화) 14시",
                supplies=[],
                source_quote="참가 동의서는 11월 3일(화) 14시까지 회신",
                **base,
            ),
            ExtractedItem(
                handling="info",
                label="날씨",
                title="비가 오면 연기",
                date_text="다음 달쯤",
                due_text=None,
                supplies=[],
                source_quote="원문에 없는 문장",
                **base,
            ),
        ],
    )


def _reading(*, meal_plan: MealPlanJSON | None = None) -> entrypoint.PhotoReading:
    notice = assemble(
        _extract(),
        source_text=SOURCE,
        input_format=NoticeInputFormat.IMAGE,
        received_at=datetime(2026, 10, 27, 3, tzinfo=UTC),
        tz=KST,
    )
    return entrypoint.PhotoReading(notice=notice, meal_plan=meal_plan)


def _meal_plan() -> MealPlanJSON:
    def meal(kind: MealType) -> Meal:
        return Meal(meal_type=kind, items=[MenuItem(name="미역국", raw="미역국5")])

    return MealPlanJSON(
        source=MealPlanSource.IMAGE,
        year_month="2026-11",
        institution_name=None,
        days=[MealDay(day=2, meals=[meal(MealType.LUNCH), meal(MealType.DINNER)])],
        unparsed=[UnparsedCell(day=3, why="글자가 겹쳐요")],
        notes=None,
    )


@pytest.fixture
def key_set(monkeypatch):
    monkeypatch.setattr(settings, "MEAL_OCR_BASE_URL", "http://gateway.test")
    monkeypatch.setattr(settings, "MEAL_OCR_API_KEY", "test")
    monkeypatch.setattr(settings, "PHOTO_UPLOAD_ACCEPT", True)


async def _upload(db_client, headers, cid, monkeypatch, reading) -> tuple[str, dict]:
    monkeypatch.setattr(entrypoint, "read_photo", lambda data, *, received_at: reading)
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        files={"file": ("a.jpg", b"fake", "image/jpeg")},
        data={"lane": "document"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    from app.api.runs import registry

    channel = registry.get(run_id)
    await channel.task
    parsed = next(payload for name, payload in channel.events if name == "parsed")
    return run_id, parsed


async def _count(session: AsyncSession, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_스위치가_꺼져_있으면_받지_않는다(db_client, bearer, cid, key_set, monkeypatch):
    # 처리방침 · 동의가 나가기 전에는 주소를 직접 불러도 사진이 외부 AI 로 나가면 안 된다
    monkeypatch.setattr(settings, "PHOTO_UPLOAD_ACCEPT", False)

    def must_not_run(data, *, received_at):
        raise AssertionError("읽기가 돌면 안 된다")

    monkeypatch.setattr(entrypoint, "read_photo", must_not_run)
    headers, _ = bearer
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        files={"file": ("a.jpg", b"x", "image/jpeg")},
        data={"lane": "document"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 503


async def test_키_없이_올리면_400이다(db_client, bearer, cid, key_set):
    headers, _ = bearer
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        files={"file": ("a.jpg", b"x", "image/jpeg")},
        data={"lane": "document"},
        headers=headers,
    )
    assert response.status_code == 400


async def test_활동_사진은_아직_받지_않는다(db_client, bearer, cid, key_set):
    headers, _ = bearer
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        files={"file": ("a.jpg", b"x", "image/jpeg")},
        data={"lane": "activity"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422


async def test_읽기만_하면_아무것도_저장되지_않는다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    before = await _count(session, Notice)
    _, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())

    assert [e["kind"] for e in parsed["entries"]] == ["event", "todo", "info"]
    assert parsed["entries"][0]["date"] == "2026-11-06"
    assert parsed["entries"][0]["items"] == ["물통"]
    assert parsed["entries"][1]["date"] == "2026-11-03"
    assert await _count(session, Notice) == before


async def test_확인이_필요한_항목을_표시한다(db_client, bearer, cid, key_set, monkeypatch):
    headers, _ = bearer
    _, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    last = parsed["entries"][2]
    assert last["needs_review"] is True
    assert "근거 문장" in last["review_reason"]


async def test_확인한_것만_저장하고_일정은_만들지_않는다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, parent_id = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    events_before = await _count(session, Event)
    checked = [e for e in parsed["entries"] if not e["needs_review"]]

    response = await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": checked, "attach_to_calendar": False},
        headers=headers,
    )

    assert response.status_code == 201
    assert response.json()["calendar_date"] is None
    notice = await session.scalar(select(Notice).where(Notice.child_id == cid))
    assert notice.uploader_parent_id == parent_id
    assert notice.issued_on == date(2026, 10, 27)
    rows = (
        await session.scalars(select(NoticeItem).where(NoticeItem.notice_id == notice.id))
    ).all()
    assert sorted(row.title for row in rows) == ["숲 체험", "참가 동의서 회신"]
    assert await _count(session, Event) == events_before  # 🚨 게이트 ㉠ 을 지나지 않았다


async def test_보호자가_고친_값으로_저장한다(db_client, session, bearer, cid, key_set, monkeypatch):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    entry = {
        **parsed["entries"][0],
        "title": "가을 숲 체험",
        "date": "2026-11-07",
        "items": ["모자"],
    }

    await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": [entry], "attach_to_calendar": False},
        headers=headers,
    )

    row = await session.scalar(select(NoticeItem).where(NoticeItem.title == "가을 숲 체험"))
    assert row.supplies == ["모자"]
    assert row.starts_at.astimezone(KST).date() == date(2026, 11, 7)
    assert row.all_day is True


async def test_읽은_시각과_마감을_화면으로_보낸다(db_client, bearer, cid, key_set, monkeypatch):
    headers, _ = bearer
    _, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())

    event, todo, _ = parsed["entries"]
    assert event["starts_at"].startswith("2026-11-06T09:30:00")
    assert event["ends_at"].startswith("2026-11-06T12:00:00")
    assert event["details"] == [{"name": "장소", "value": "숲체험장"}]
    assert event["target_scope"] == "all"
    assert event["source_quote"] == "일시: 11월 6일(금) 9:30~12:00"
    assert todo["due_at"].startswith("2026-11-03T14:00:00")
    assert todo["starts_at"] is None


async def test_화면이_받은_값을_그대로_돌려보내도_저장된다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())

    response = await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": parsed["entries"][:2], "attach_to_calendar": False},
        headers=headers,
    )

    assert response.status_code == 201
    row = await session.scalar(select(NoticeItem).where(NoticeItem.title == "숲 체험"))
    assert row.details == [{"name": "장소", "value": "숲체험장"}]


async def test_날짜를_지우고_저장하면_날짜가_남지_않는다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    entry = {**parsed["entries"][0], "date": None}

    await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": [entry], "attach_to_calendar": False},
        headers=headers,
    )

    row = await session.scalar(select(NoticeItem).where(NoticeItem.title == "숲 체험"))
    assert row.starts_at is None
    assert row.ends_at is None
    assert row.all_day is False


async def _event(session: AsyncSession, cid: uuid.UUID) -> Event:
    event = Event(
        child_id=cid,
        title="숲 체험",
        event_type=EventType.EPISODIC,
        starts_at=datetime(2026, 11, 6, 0, 30, tzinfo=UTC),
        category=EventCategory.INSTITUTION,
        created_by=EventCreatedBy.CAREGIVER,
    )
    session.add(event)
    await session.flush()
    return event


async def test_일정으로_넣은_항목은_그_일정이_가리킨다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    event = await _event(session, cid)  # 항목 시트가 먼저 POST /events 로 넣은 일정
    entry = {**parsed["entries"][0], "event_id": str(event.id)}

    response = await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": [entry], "attach_to_calendar": False},
        headers=headers,
    )

    assert response.status_code == 201
    item = await session.scalar(select(NoticeItem).where(NoticeItem.title == "숲 체험"))
    await session.refresh(event)
    assert event.source_notice_item_id == item.id
    assert event.source_notice_id == item.notice_id


async def test_이을_수_없는_일정이_와도_공지는_저장된다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    entry = {**parsed["entries"][0], "event_id": str(uuid.uuid4())}  # 그 사이 지운 일정

    response = await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": [entry], "attach_to_calendar": False},
        headers=headers,
    )

    assert response.status_code == 201
    assert await _count(session, NoticeItem) == 1


async def test_한_일정을_두_항목에_이으면_422다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    event = await _event(session, cid)
    entries = [{**entry, "event_id": str(event.id)} for entry in parsed["entries"][:2]]

    response = await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": entries, "attach_to_calendar": False},
        headers=headers,
    )

    assert response.status_code == 422
    assert await _count(session, Notice) == 0


async def test_확인하지_않은_항목이_오면_422다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    before = await _count(session, Notice)

    response = await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": parsed["entries"], "attach_to_calendar": False},
        headers=headers,
    )

    assert response.status_code == 422
    assert await _count(session, Notice) == before


async def test_저장을_다시_보내도_두_번_넣지_않는다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    body = {"lane": "document", "entries": parsed["entries"][:1], "attach_to_calendar": True}

    first = await db_client.post(COMMIT.format(rid=run_id), json=body, headers=headers)
    second = await db_client.post(COMMIT.format(rid=run_id), json=body, headers=headers)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert first.json()["calendar_date"] == "2026-11-06"
    count = await session.scalar(
        select(func.count()).select_from(Notice).where(Notice.child_id == cid)
    )
    assert count == 1


async def test_같은_공지를_또_올려도_한_행이다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    for _ in range(2):
        run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
        await db_client.post(
            COMMIT.format(rid=run_id),
            json={
                "lane": "document",
                "entries": parsed["entries"][:1],
                "attach_to_calendar": False,
            },
            headers=headers,
        )

    notice_ids = (await session.scalars(select(Notice.id).where(Notice.child_id == cid))).all()
    assert len(notice_ids) == 1
    items = await session.scalar(
        select(func.count()).select_from(NoticeItem).where(NoticeItem.notice_id == notice_ids[0])
    )
    assert items == 1


async def test_식단표는_끼니를_daycare_meal_에_넣는다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(
        db_client, headers, cid, monkeypatch, _reading(meal_plan=_meal_plan())
    )
    meals = [e for e in parsed["entries"] if e["kind"] == "meal"]
    # 저녁은 daycare_meal 에 자리가 없고, 읽지 못한 칸은 확인거리로 올라온다
    assert [e["needs_review"] for e in meals] == [False, True]

    await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": [meals[0]], "attach_to_calendar": False},
        headers=headers,
    )

    row = await session.scalar(select(DaycareMeal).where(DaycareMeal.child_id == cid))
    assert row.serve_date == date(2026, 11, 2)
    assert row.menu_keys == ["미역국"]
    notice = await session.scalar(select(Notice.id).where(Notice.child_id == cid))
    assert row.source_notice_id == notice


async def test_식단표를_못_읽어도_공지_항목은_보여_준다(
    db_client, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    reading = entrypoint.PhotoReading(notice=_reading().notice, meal_plan=None, meal_failed=True)
    _, parsed = await _upload(db_client, headers, cid, monkeypatch, reading)
    assert [e["kind"] for e in parsed["entries"]] == ["event", "todo", "info", "meal"]
    assert parsed["entries"][-1]["needs_review"] is True


async def test_남의_run_은_없는_run_이다(db_client, session, bearer, cid, key_set, monkeypatch):
    headers, _ = bearer
    _, someone = await issue_bearer(session, token="test-token-someone")
    their_child = await link_child(session, parent_id=someone)
    run = photo_run.open_photo_run(run_id=uuid.uuid4().hex, parent_id=someone, child_id=their_child)
    run.reading = _reading()
    await photo_run.keep(run)
    response = await db_client.post(
        COMMIT.format(rid=run.run_id),
        json={"lane": "document", "entries": [], "attach_to_calendar": False},
        headers=headers,
    )
    assert response.status_code == 404


UPLOADS = "/api/v1/children/{cid}/photos/uploads"


@pytest.fixture
def s3_bucket(monkeypatch):
    """S3 를 가짜 저장소로 바꾼다. 열쇠가 내 것인지 보는 규칙(`owns`)은 진짜를 쓴다."""
    stored: dict[str, bytes] = {}
    monkeypatch.setattr(settings, "PHOTO_UPLOAD_BUCKET", "test-bucket")

    def take(key, *, max_bytes):
        if key not in stored:
            raise photo_uploads.MissingUpload
        data = stored.pop(key)  # 읽으면 지운다
        if len(data) > max_bytes:
            raise photo_uploads.UploadTooLarge
        return data

    def new_upload(parent_id, *, max_bytes):
        key = f"photos/{parent_id}/{uuid.uuid4().hex}"
        return photo_uploads.UploadTicket(url="https://s3.test/", fields={"key": key}, key=key)

    monkeypatch.setattr(photo_uploads, "take", take)
    monkeypatch.setattr(photo_uploads, "new_upload", new_upload)
    monkeypatch.setattr(photo_uploads, "discard", lambda key: stored.pop(key, None))
    return stored


async def test_버킷이_없으면_직접_올리라고_한다(db_client, bearer, cid, key_set):
    headers, _ = bearer
    response = await db_client.post(UPLOADS.format(cid=cid), headers=headers)
    assert response.status_code == 201
    assert response.json()["mode"] == "direct"


async def test_버킷이_있으면_S3_업로드_주소를_준다(db_client, bearer, cid, key_set, s3_bucket):
    headers, parent_id = bearer
    response = await db_client.post(UPLOADS.format(cid=cid), headers=headers)
    body = response.json()
    assert response.status_code == 201
    assert body["mode"] == "s3"
    assert photo_uploads.owns(body["key"], parent_id)
    assert body["expires_in"] == photo_uploads.UPLOAD_URL_TTL_SECONDS


async def test_S3_에_올린_사진은_열쇠로_읽고_지운다(
    db_client, bearer, cid, key_set, s3_bucket, monkeypatch
):
    headers, _ = bearer
    key = (await db_client.post(UPLOADS.format(cid=cid), headers=headers)).json()["key"]
    s3_bucket[key] = b"photo-bytes"  # 화면이 S3 에 올렸다
    received: list[bytes] = []

    def read(data, *, received_at):
        received.append(data)
        return _reading()

    monkeypatch.setattr(entrypoint, "read_photo", read)
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        data={"lane": "document", "upload_key": key},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    from app.api.runs import registry

    await registry.get(response.json()["run_id"]).task
    assert response.status_code == 202
    assert received == [b"photo-bytes"]
    assert s3_bucket == {}  # 원본이 남지 않는다


async def test_남의_열쇠로는_읽지_않는다(db_client, bearer, cid, key_set, s3_bucket):
    headers, _ = bearer
    stranger = f"photos/{uuid.uuid4()}/{uuid.uuid4().hex}"
    s3_bucket[stranger] = b"someone-else"
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        data={"lane": "document", "upload_key": stranger},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 400
    assert stranger in s3_bucket  # 건드리지도 않는다


async def test_버킷이_있으면_본문으로_온_사진은_받지_않는다(
    db_client, bearer, cid, key_set, s3_bucket
):
    headers, _ = bearer
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        files={"file": ("a.jpg", b"fake", "image/jpeg")},
        data={"lane": "document"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 400


async def test_S3_에_사진이_없으면_다시_고르라고_한다(db_client, bearer, cid, key_set, s3_bucket):
    headers, _ = bearer
    key = (await db_client.post(UPLOADS.format(cid=cid), headers=headers)).json()["key"]
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        data={"lane": "document", "upload_key": key},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "사진을 다시 골라 주세요"


async def test_저장할_것이_없는_사진은_빈_확인_화면_대신_실패로_끝난다(
    db_client, bearer, cid, key_set, monkeypatch
):
    # 영수증 · 게임 화면처럼 글자는 있는데 공지가 아닌 사진. 빈 확인 화면은 저장 버튼만 꺼져 있었다
    headers, _ = bearer
    reading = entrypoint.PhotoReading(notice=replace(_reading().notice, items=()), meal_plan=None)
    monkeypatch.setattr(entrypoint, "read_photo", lambda data, *, received_at: reading)
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        files={"file": ("a.jpg", b"x", "image/jpeg")},
        data={"lane": "document"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    from app.api.runs import registry

    channel = registry.get(response.json()["run_id"])
    await asyncio.wait_for(channel.task, 5)
    assert channel.ended_with == "failed"
    assert channel.events[-1][1]["reason"] == "not_document"
    assert "parsed" not in [name for name, _ in channel.events]


async def test_글자를_못_찾으면_실패로_끝난다(db_client, bearer, cid, key_set, monkeypatch):
    headers, _ = bearer

    def unreadable(data, *, received_at):
        raise entrypoint.UnreadablePhoto("글자를 찾지 못했어요")

    monkeypatch.setattr(entrypoint, "read_photo", unreadable)
    response = await db_client.post(
        PHOTOS.format(cid=cid),
        files={"file": ("a.jpg", b"x", "image/jpeg")},
        data={"lane": "document"},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    from app.api.runs import registry

    channel = registry.get(response.json()["run_id"])
    await asyncio.wait_for(channel.task, 5)
    assert channel.ended_with == "failed"
    assert channel.events[-1][1]["reason"] == "unparsable"


# ── 11-05 명성님 적대적 검증 (#726) ─────────────────────────────────────────


async def _commit(db_client, headers, run_id, entries):
    return await db_client.post(
        COMMIT.format(rid=run_id),
        json={"lane": "document", "entries": entries, "attach_to_calendar": False},
        headers=headers,
    )


async def test_같은_항목을_되풀이해_보내면_422다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    entry = parsed["entries"][0]

    # 500개면 개수 상한(400)에 먼저 걸린다. 상한 안에서 되풀이하는 경우를 본다
    response = await _commit(db_client, headers, run_id, [entry, entry])

    assert response.status_code == 422
    assert await _count(session, NoticeItem) == 0


async def test_항목이_너무_많으면_받지_않는다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    entries = [{**parsed["entries"][0], "id": f"pe_{i}"} for i in range(201)]

    response = await _commit(db_client, headers, run_id, entries)

    assert response.status_code == 400
    assert await _count(session, Notice) == 0


@pytest.mark.parametrize("day", ["0001-01-01", "1900-01-01", "9999-12-31"])
async def test_말이_안_되는_날짜는_받지_않는다(
    db_client, session, bearer, cid, key_set, monkeypatch, day
):
    # 0001-01-01 은 시간대를 붙이다 OverflowError 로 500 이 났다
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())

    response = await _commit(db_client, headers, run_id, [{**parsed["entries"][0], "date": day}])

    assert response.status_code == 400
    assert await _count(session, Notice) == 0


async def test_아이와_연결이_끊긴_보호자는_저장할_수_없다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, parent_id = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())
    await session.execute(
        delete(ParentChild).where(ParentChild.parent_id == parent_id, ParentChild.child_id == cid)
    )

    response = await _commit(db_client, headers, run_id, parsed["entries"][:1])

    assert response.status_code == 403
    assert await _count(session, Notice) == 0


async def test_같은_공지를_나눠_저장하면_뒤의_항목을_이어_붙인다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    first_run, first = await _upload(db_client, headers, cid, monkeypatch, _reading())
    assert (await _commit(db_client, headers, first_run, first["entries"][:1])).status_code == 201

    # 같은 사진을 다시 읽어 나머지를 저장한다. 앞에서 저장한 항목을 또 보내도 두 벌이 되지 않는다
    second_run, second = await _upload(db_client, headers, cid, monkeypatch, _reading())
    response = await _commit(db_client, headers, second_run, second["entries"][:2])

    assert response.status_code == 201
    titles = sorted((await session.scalars(select(NoticeItem.title))).all())
    assert titles == ["숲 체험", "참가 동의서 회신"]
    assert await _count(session, Notice) == 1


async def test_다시_읽은_공지의_항목도_일정과_잇는다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    first_run, first = await _upload(db_client, headers, cid, monkeypatch, _reading())
    await _commit(db_client, headers, first_run, first["entries"][:1])
    event = await _event(session, cid)

    second_run, second = await _upload(db_client, headers, cid, monkeypatch, _reading())
    entry = {**second["entries"][0], "event_id": str(event.id)}
    await _commit(db_client, headers, second_run, [entry])

    item = await session.scalar(select(NoticeItem).where(NoticeItem.title == "숲 체험"))
    await session.refresh(event)
    assert event.source_notice_item_id == item.id


@pytest.mark.parametrize(
    "change",
    [{"title": "   "}, {"items": [""]}, {"items": ["x" * 101]}],
    ids=["빈 제목", "빈 준비물", "너무 긴 준비물"],
)
async def test_빈_제목과_이상한_준비물은_받지_않는다(
    db_client, session, bearer, cid, key_set, monkeypatch, change
):
    headers, _ = bearer
    run_id, parsed = await _upload(db_client, headers, cid, monkeypatch, _reading())

    response = await _commit(db_client, headers, run_id, [{**parsed["entries"][0], **change}])

    assert response.status_code == 400
    assert await _count(session, Notice) == 0


async def test_메뉴를_다_지운_끼니는_조용히_버리지_않는다(
    db_client, session, bearer, cid, key_set, monkeypatch
):
    headers, _ = bearer
    run_id, parsed = await _upload(
        db_client, headers, cid, monkeypatch, _reading(meal_plan=_meal_plan())
    )
    meal = next(e for e in parsed["entries"] if e["kind"] == "meal" and not e["needs_review"])

    response = await _commit(db_client, headers, run_id, [{**meal, "title": " , ,"}])

    assert response.status_code == 422
    assert await _count(session, DaycareMeal) == 0


async def test_읽지_않고_돌려보내도_올라온_사진은_지운다(
    db_client, session, bearer, cid, key_set, s3_bucket
):
    # 활동 사진은 아직 받지 않는다(422). 그래도 S3 에 올라온 아이 사진이 하루 남으면 안 된다
    headers, _ = bearer
    key = (await db_client.post(UPLOADS.format(cid=cid), headers=headers)).json()["key"]
    s3_bucket[key] = b"child-photo"

    response = await db_client.post(
        PHOTOS.format(cid=cid),
        data={"lane": "activity", "upload_key": key},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    assert response.status_code == 422
    assert s3_bucket == {}


async def test_같은_요청을_다시_보내면_새로_올린_사진은_지우고_앞_결과를_준다(
    db_client, session, bearer, cid, key_set, s3_bucket, monkeypatch
):
    headers, _ = bearer
    monkeypatch.setattr(entrypoint, "read_photo", lambda data, *, received_at: _reading())
    idem = {**headers, "Idempotency-Key": str(uuid.uuid4())}
    first_key = (await db_client.post(UPLOADS.format(cid=cid), headers=headers)).json()["key"]
    s3_bucket[first_key] = b"photo"
    first = await db_client.post(
        PHOTOS.format(cid=cid), data={"lane": "document", "upload_key": first_key}, headers=idem
    )
    from app.api.runs import registry

    await registry.get(first.json()["run_id"]).task
    # 응답을 못 받은 화면이 다시 올리고 같은 키로 보낸다
    second_key = (await db_client.post(UPLOADS.format(cid=cid), headers=headers)).json()["key"]
    s3_bucket[second_key] = b"photo"
    second = await db_client.post(
        PHOTOS.format(cid=cid), data={"lane": "document", "upload_key": second_key}, headers=idem
    )

    assert second.json()["run_id"] == first.json()["run_id"]
    assert s3_bucket == {}
