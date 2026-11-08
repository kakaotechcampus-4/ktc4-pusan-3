"""notice · notice_item 저장 — 이상한 값이 와도 DB 가 깨지지 않고, 지울 때 연쇄가 맞는지.

명성님 질문 "실제 공지가 DB 에 안 깨지고 들어가는가" 의 회귀 테스트다. 말뭉치 시험에서 본 값과
모델이 낼 수 있는 값을 지어내 넣는다. 🚨 실제 공지 글은 넣지 않는다 (CLAUDE.md §9).
"""

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.notice_extract import ExtractedItem, ItemDetail, NoticeExtract
from app.domains.child.models import Child
from app.domains.identity.models import Parent
from app.domains.memory.observation.models import ConfidenceSource, ObservationFood
from app.domains.notice.assemble import NewNotice, assemble
from app.domains.notice.models import (
    Notice,
    NoticeAudience,
    NoticeDocKind,
    NoticeInputFormat,
    NoticeItem,
    NoticeRelation,
)
from app.domains.notice.repository import SavedNotice, link_related, save_notice
from app.domains.schedule.models import Event, EventCategory, EventCreatedBy, EventType
from app.domains.schedule.repository import link_notice_items

KST = ZoneInfo("Asia/Seoul")
RECEIVED = datetime(2026, 10, 27, 3, tzinfo=UTC)


async def _child(session) -> tuple[uuid.UUID, uuid.UUID]:
    parent = Parent()
    session.add(parent)
    await session.flush()
    child = Child(nickname="테스트아이", owner_parent_id=parent.id, birth_date=date(2023, 1, 1))
    session.add(child)
    await session.flush()
    return parent.id, child.id


def _extracted(**overrides) -> ExtractedItem:
    values = {
        "handling": "todo",
        "label": "회신",
        "title": "동의서 회신",
        "target_scope": "all",
        "date_text": None,
        "due_text": "11월 3일(화) 14시",
        "details": [],
        "supplies": [],
        "source_quote": "동의서 회신",
    }
    return ExtractedItem(**{**values, **overrides})


def _notice(*items: ExtractedItem, source: str = "동의서 회신 안내", **extract) -> NewNotice:
    values = {
        "doc_kind": "newsletter",
        "audience_scope": "institution",
        "title": "안내",
        "issued_text": None,
        "is_correction": False,
        "person_names": [],
        "institution_names": [],
        "items": list(items),
    }
    return assemble(
        NoticeExtract(**{**values, **extract}),
        source_text=source,
        input_format=NoticeInputFormat.TEXT,
        received_at=RECEIVED,
        tz=KST,
    )


HOSTILE = [
    "널\x00문자",
    "짝 없는 서로게이트 \ud83d 끝",
    "이모지 🎉👶",
    "보이지 않는 문자﻿​",
    "가" * 100_000,
    "'; DROP TABLE notice; --",
    "<script>alert(1)</script>",
    "역슬래시 \\ 와 따옴표 \" '",
    "\n\n\t  ",
]


@pytest.mark.parametrize("value", HOSTILE)
async def test_이상한_글자가_어느_칸에_와도_저장된다(session, value):
    parent_id, child_id = await _child(session)
    notice = _notice(
        _extracted(
            label=value,
            title=value,
            due_text=value,
            details=[ItemDetail(name=value, value=value), ItemDetail(name="장소", value=value)],
            supplies=[value, "물통"],
            source_quote=value,
        ),
        source=f"안내 {value}",
        title=value,
    )
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=notice
    )
    assert saved.created is True
    row = await session.scalar(select(NoticeItem).where(NoticeItem.notice_id == saved.notice_id))
    assert "\x00" not in row.title
    assert row.title.strip()  # 빈 칸이 NOT NULL 을 깨면 안 된다


async def test_details_를_목록으로_저장한다(session):
    parent_id, child_id = await _child(session)
    notice = _notice(_extracted(details=[ItemDetail(name="해님반", value="8,000원")]))
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=notice
    )
    row = await session.scalar(select(NoticeItem).where(NoticeItem.notice_id == saved.notice_id))
    assert row.details == [{"name": "해님반", "value": "8,000원"}]


async def test_details_가_없으면_빈_목록이다(session):
    parent_id, child_id = await _child(session)
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=_notice(_extracted())
    )
    row = await session.scalar(select(NoticeItem).where(NoticeItem.notice_id == saved.notice_id))
    assert row.details == []


async def test_어떤_판으로_읽었는지_남긴다(session):
    parent_id, child_id = await _child(session)
    notice = replace(_notice(), extractor_version="모델:2026-11-02")
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=notice
    )
    row = await session.get(Notice, saved.notice_id)
    assert row.extractor_version == "모델:2026-11-02"


async def test_항목이_없는_공지도_저장된다(session):
    parent_id, child_id = await _child(session)
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=_notice()
    )
    assert saved.created is True


async def test_아이를_지우면_공지와_항목이_같이_지워진다(session):
    parent_id, child_id = await _child(session)
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=_notice(_extracted())
    )
    await session.execute(delete(Child).where(Child.id == child_id))
    assert await session.get(Notice, saved.notice_id, populate_existing=True) is None
    item = await session.scalar(select(NoticeItem).where(NoticeItem.notice_id == saved.notice_id))
    assert item is None


async def test_올린_보호자를_지워도_공지는_남는다(session):
    parent_id, child_id = await _child(session)
    other = Parent()
    session.add(other)
    await session.flush()
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=other.id, notice=_notice()
    )
    await session.execute(delete(Parent).where(Parent.id == other.id))
    row = await session.get(Notice, saved.notice_id, populate_existing=True)
    assert row.uploader_parent_id is None


async def _event(session, child_id: uuid.UUID) -> Event:
    event = Event(
        child_id=child_id,
        title="숲 체험",
        event_type=EventType.EPISODIC,
        starts_at=RECEIVED,
        category=EventCategory.INSTITUTION,
        created_by=EventCreatedBy.CAREGIVER,
    )
    session.add(event)
    await session.flush()
    return event


async def _linked(session) -> tuple[uuid.UUID, Event, SavedNotice]:
    parent_id, child_id = await _child(session)
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=_notice(_extracted())
    )
    event = await _event(session, child_id)
    missed = await link_notice_items(
        session, child_id=child_id, notice_id=saved.notice_id, links={event.id: saved.item_ids[0]}
    )
    assert missed == set()
    return child_id, event, saved


async def test_일정은_자기를_만든_공지_항목을_가리킨다(session):
    _, event, saved = await _linked(session)
    await session.refresh(event)
    assert event.source_notice_item_id == saved.item_ids[0]
    assert event.source_notice_id == saved.notice_id


async def test_일정을_지워도_항목은_남는다(session):
    _, event, saved = await _linked(session)
    await session.execute(delete(Event).where(Event.id == event.id))
    assert await session.get(NoticeItem, saved.item_ids[0], populate_existing=True) is not None


async def test_공지를_지워도_일정은_남고_출처만_끊긴다(session):
    _, event, saved = await _linked(session)
    await session.execute(delete(Notice).where(Notice.id == saved.notice_id))
    await session.refresh(event)
    assert event.source_notice_item_id is None
    assert event.source_notice_id is None


async def test_한_항목에_일정을_둘_걸면_DB_가_막는다(session):
    child_id, _, saved = await _linked(session)
    second = await _event(session, child_id)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(Event)
                .where(Event.id == second.id)
                .values(source_notice_item_id=saved.item_ids[0])
            )


async def test_다른_아이의_일정과_이미_이어진_일정은_잇지_않는다(session):
    child_id, linked, saved = await _linked(session)
    _, other_child = await _child(session)
    stranger = await _event(session, other_child)
    missed = await link_notice_items(
        session,
        child_id=child_id,
        notice_id=saved.notice_id,
        links={stranger.id: saved.item_ids[0], linked.id: saved.item_ids[0]},
    )
    assert missed == {stranger.id, linked.id}
    await session.refresh(stranger)
    assert stranger.source_notice_item_id is None


async def test_공지_id_를_바꾸려_하면_막는다(session):
    _, _, saved = await _linked(session)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(Notice).where(Notice.id == saved.notice_id).values(id=uuid.uuid4())
            )


async def test_공지를_지워도_관찰은_남고_출처만_끊긴다(session):
    parent_id, child_id = await _child(session)
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=_notice()
    )
    obs = ObservationFood(
        child_id=child_id,
        raw_text="밥을 남겼어요",
        subject="밥",
        confidence_source=ConfidenceSource.INSTITUTION_NOTICE,
        observed_range=None,
        source_notice_id=saved.notice_id,
    )
    from sqlalchemy.dialects.postgresql import Range

    obs.observed_range = Range(date(2026, 10, 27), date(2026, 10, 28))
    session.add(obs)
    await session.flush()
    await session.execute(delete(Notice).where(Notice.id == saved.notice_id))
    await session.refresh(obs)
    assert obs.source_notice_id is None


async def test_없는_공지를_가리키는_관찰은_막는다(session):
    from sqlalchemy.dialects.postgresql import Range

    parent_id, child_id = await _child(session)
    session.add(
        ObservationFood(
            child_id=child_id,
            raw_text="밥",
            subject="밥",
            confidence_source=ConfidenceSource.INSTITUTION_NOTICE,
            observed_range=Range(date(2026, 10, 27), date(2026, 10, 28)),
            source_notice_id=uuid.uuid4(),
        )
    )
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.flush()


async def test_정정_공지는_정정으로_저장한다(session):
    parent_id, child_id = await _child(session)
    saved = await save_notice(
        session,
        child_id=child_id,
        uploader_parent_id=parent_id,
        notice=_notice(is_correction=True),
    )
    row = await session.get(Notice, saved.notice_id)
    assert row.relation is NoticeRelation.CORRECTION
    assert row.related_notice_id is None  # 어느 공지를 고치는지는 아직 모른다


async def test_앞_공지를_지워도_정정_공지는_남는다(session):
    parent_id, child_id = await _child(session)
    first = await save_notice(
        session,
        child_id=child_id,
        uploader_parent_id=parent_id,
        notice=_notice(source="처음 안내"),
    )
    second = await save_notice(
        session,
        child_id=child_id,
        uploader_parent_id=parent_id,
        notice=_notice(source="정정 안내"),
    )
    await link_related(
        session,
        notice_id=second.notice_id,
        related_notice_id=first.notice_id,
        relation=NoticeRelation.CORRECTION,
    )
    await session.execute(delete(Notice).where(Notice.id == first.notice_id))
    row = await session.get(Notice, second.notice_id, populate_existing=True)
    assert row.related_notice_id is None
    assert row.relation is NoticeRelation.CORRECTION


REREAD = "숲 체험 안내입니다. 11월 13일(금) 9:30 출발합니다. 체육복과 물통을 챙겨 주세요. " * 3


async def test_다시_읽어_글자가_조금_달라도_한_행이다(session):
    parent_id, child_id = await _child(session)
    first = await save_notice(
        session,
        child_id=child_id,
        uploader_parent_id=parent_id,
        notice=_notice(_extracted(), source=REREAD),
    )
    second = await save_notice(
        session,
        child_id=child_id,
        uploader_parent_id=parent_id,
        notice=_notice(_extracted(), source=REREAD.replace("물통", "물동")),
    )
    assert second.created is False
    assert second.notice_id == first.notice_id


async def test_날짜만_다른_공지는_따로_저장한다(session):
    parent_id, child_id = await _child(session)
    for day in ("13일", "20일"):
        saved = await save_notice(
            session,
            child_id=child_id,
            uploader_parent_id=parent_id,
            notice=_notice(source=REREAD.replace("13일", day)),
        )
        assert saved.created is True


async def test_정정_공지는_비슷해도_합치지_않는다(session):
    parent_id, child_id = await _child(session)
    await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=_notice(source=REREAD)
    )
    saved = await save_notice(
        session,
        child_id=child_id,
        uploader_parent_id=parent_id,
        notice=_notice(source=REREAD.replace("물통", "물동"), is_correction=True),
    )
    assert saved.created is True


async def test_다른_아이의_같은_공지는_각자_저장한다(session):
    parent_id, first = await _child(session)
    _, second = await _child(session)
    for child_id in (first, second):
        saved = await save_notice(
            session, child_id=child_id, uploader_parent_id=parent_id, notice=_notice()
        )
        assert saved.created is True


async def test_끝이_시작보다_빠른_행은_DB_가_막는다(session):
    parent_id, child_id = await _child(session)
    saved = await save_notice(
        session, child_id=child_id, uploader_parent_id=parent_id, notice=_notice(_extracted())
    )
    item = await session.scalar(select(NoticeItem).where(NoticeItem.notice_id == saved.notice_id))
    item.starts_at = datetime(2026, 11, 2, tzinfo=UTC)
    item.ends_at = datetime(2026, 11, 1, tzinfo=UTC)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.flush()


def test_문서_종류_값이_모델과_계약에서_같다():
    assert {k.value for k in NoticeDocKind} == set(
        NoticeExtract.model_fields["doc_kind"].annotation.__args__
    )
    assert {k.value for k in NoticeAudience} == set(
        NoticeExtract.model_fields["audience_scope"].annotation.__args__
    )
