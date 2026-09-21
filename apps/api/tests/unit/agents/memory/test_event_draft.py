"""일정 초안 모델과 run 단위 버퍼, 그리고 create_event 가 만드는 초안.

초안은 DB 에 없고 SSE 로만 나간다. 그래서 payload 모양과 합치는 규칙을 여기서 고정한다.

    - to_payload: datetime 은 isoformat, 없는 값은 null
    - 같은 event_id 는 초안 하나로 합쳐진다
    - 새 일정은 id 가 없어 호출마다 따로 쌓인다
    - all() 은 넣은 순서를 지킨다
    - drop 은 그 일정의 초안만 뺀다

create_event:

    - store 에 쓰지 않는다
    - 준비물을 items 로 같이 받아 초안 안에 순서대로 담는다
    - 결과에 id 대신 draft: true 와 요약이 실린다
    - 이름이 비어 있는 준비물은 스키마가 거부한다

update_event:

    - 저장된 행을 고치지 않는다
    - 말하지 않은 필드는 현재 값 그대로 초안에 실린다
    - 저장된 준비물은 item_id 와 함께 실린다
    - 같은 값으로 바꾸면 초안을 만들지 않는다
    - 같은 일정을 두 번 고치면 초안 하나에 쌓인다
"""

import json
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import DraftBook, DraftItem, EventDraft, EventSnapshot
from app.agents.memory.registry import execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)  # 2026-09-15 화요일


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=NOW,
        timezone=KST,
        store=InMemoryStore(now=NOW),
    )


def _draft(
    *,
    op: str = "create",
    event_id: str | None = None,
    title: str = "물놀이",
    ends_at: datetime | None = None,
    items: tuple[DraftItem, ...] = (),
    changed: tuple[str, ...] = (),
    before: EventSnapshot | None = None,
) -> EventDraft:
    return EventDraft(
        op=op,  # type: ignore[arg-type]
        event_id=event_id,
        title=title,
        starts_at=datetime(2026, 9, 25, 10, 0, tzinfo=KST),
        ends_at=ends_at,
        all_day=False,
        event_type="episodic",
        category="activity",
        items=items,
        changed=changed,
        before=before,
    )


def test_새_일정_초안은_준비물을_안에_싣고_id_는_비어_있다() -> None:
    draft = _draft(
        items=(
            DraftItem(item_id=None, item_name="수영복"),
            DraftItem(item_id=None, item_name="여벌옷"),
        )
    )

    payload = draft.to_payload()

    assert payload == {
        "draft_id": None,  # DraftBook 에 넣기 전이라 아직 없다
        "op": "create",
        "event_id": None,
        "event": {
            "title": "물놀이",
            "starts_at": "2026-09-25T10:00:00+09:00",
            "ends_at": None,
            "all_day": False,
            "event_type": "episodic",
            "category": "activity",
        },
        "before": None,  # 새 일정은 원본이 없다
        "items": [
            {"item_id": None, "item_name": "수영복", "is_prepared": False},
            {"item_id": None, "item_name": "여벌옷", "is_prepared": False},
        ],
    }
    assert "changed" not in payload  # 화면은 before 와 비교해 직접 구한다


def test_payload_는_그대로_json_으로_나간다() -> None:
    # SSE 로 실어 보내려면 datetime 이 남아 있으면 안 된다
    draft = _draft(ends_at=datetime(2026, 9, 25, 17, 0, tzinfo=KST))

    loaded = json.loads(json.dumps(draft.to_payload()))

    assert loaded["event"]["ends_at"] == "2026-09-25T17:00:00+09:00"


def test_수정_초안은_event_id_와_원본을_싣는다() -> None:
    before = EventSnapshot(
        title="운동회",
        starts_at=datetime(2026, 9, 25, 10, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        event_type="episodic",
        category="activity",
        items=(DraftItem(item_id="event_item-1", item_name="체육복"),),
    )
    draft = _draft(
        op="update",
        event_id="event-1",
        title="가을 운동회",
        items=(DraftItem(item_id="event_item-1", item_name="체육복", is_prepared=True),),
        changed=("title", "items"),
        before=before,
    )

    payload = draft.to_payload()

    assert payload["event_id"] == "event-1"
    assert payload["items"] == [
        {"item_id": "event_item-1", "item_name": "체육복", "is_prepared": True}
    ]
    # 화면이 "운동회 → 가을 운동회" 를 그릴 수 있어야 한다
    assert payload["before"]["title"] == "운동회"
    assert payload["before"]["items"] == [
        {"item_id": "event_item-1", "item_name": "체육복", "is_prepared": False}
    ]
    assert "changed" not in payload


def test_before_와_event_의_시각_표기가_같다() -> None:
    # 표기가 갈리면 화면이 안 바뀐 필드를 바뀌었다고 읽는다
    moment = datetime(2026, 9, 25, 10, 0, tzinfo=KST)
    before = EventSnapshot(
        title="운동회",
        starts_at=moment,
        ends_at=None,
        all_day=False,
        event_type="episodic",
        category="activity",
    )
    draft = _draft(op="update", event_id="event-1", before=before)

    payload = draft.to_payload()

    assert payload["before"]["starts_at"] == payload["event"]["starts_at"]
    assert payload["before"]["ends_at"] == payload["event"]["ends_at"] is None


def test_같은_일정에_두_번_쌓으면_초안은_하나다() -> None:
    # update_event 뒤에 create_event_item 이 같은 일정에 오는 경우
    book = DraftBook()

    book.put(_draft(op="update", event_id="event-1", title="운동회"))
    book.put(_draft(op="update", event_id="event-1", title="가을 운동회"))

    assert [draft.title for draft in book.all()] == ["가을 운동회"]


def test_새_일정은_id_가_없어_호출마다_따로_쌓인다() -> None:
    book = DraftBook()

    book.put(_draft(title="물놀이"))
    book.put(_draft(title="소풍"))

    assert [draft.title for draft in book.all()] == ["물놀이", "소풍"]


def test_초안_순서는_넣은_순서다() -> None:
    # 보호자 화면의 초안 순서가 발화 순서와 같아야 한다
    book = DraftBook()

    book.put(_draft(title="물놀이"))
    book.put(_draft(op="update", event_id="event-1", title="운동회"))
    book.put(_draft(title="소풍"))
    book.put(_draft(op="update", event_id="event-1", title="가을 운동회"))

    assert [draft.title for draft in book.all()] == ["물놀이", "가을 운동회", "소풍"]


def test_get_은_그_일정의_초안을_돌려준다() -> None:
    book = DraftBook()
    book.put(_draft(op="update", event_id="event-1"))

    assert book.get("event-1") is not None
    assert book.get("event-2") is None


def test_drop_은_그_일정의_초안만_뺀다() -> None:
    # 일정을 지우면 그 일정의 초안도 같이 빠져야 한다
    book = DraftBook()
    book.put(_draft(op="update", event_id="event-1"))
    book.put(_draft(op="update", event_id="event-2"))

    book.drop("event-1")
    book.drop("event-3")  # 없는 것을 빼도 실패하지 않는다

    assert [draft.event_id for draft in book.all()] == ["event-2"]


def test_비어_있는_버퍼는_빈_튜플을_돌려준다() -> None:
    assert DraftBook().all() == ()


# ── create_event ────────────────────────────────────────────────
async def _create_event(context: AgentContext, **args: Any) -> dict[str, Any]:
    result = await execute_tool(
        "create_event",
        {"title": "물놀이", "starts_on": "금요일", "starts_time": "오전 10시", **args},
        context,
    )
    assert result.success is True, result.error
    return result.data


async def test_create_event_는_store_에_쓰지_않는다(context: AgentContext) -> None:
    await _create_event(context, items=["수영복"])

    assert await context.store.query_events(child_id=context.child_id) == []
    assert await context.store.list_event_items(event_id="event-1") == []
    assert len(context.drafts.all()) == 1


async def test_준비물은_말한_순서대로_초안에_담긴다(context: AgentContext) -> None:
    await _create_event(context, items=["수영복", "여벌옷", "수건"])

    draft = context.drafts.all()[0]
    assert draft.op == "create"
    assert draft.event_id is None
    assert [(item.item_id, item.item_name, item.is_prepared) for item in draft.items] == [
        (None, "수영복", False),
        (None, "여벌옷", False),
        (None, "수건", False),
    ]


async def test_준비물_얘기가_없으면_초안의_items_는_비어_있다(context: AgentContext) -> None:
    await _create_event(context)

    assert context.drafts.all()[0].items == ()


async def test_결과는_id_대신_초안_요약을_싣는다(context: AgentContext) -> None:
    # id 를 주면 모델이 저장된 일정으로 보고 다음 tool 에 넘긴다
    data = await _create_event(context, items=["수영복"])

    assert "id" not in data
    assert data == {
        "draft": True,
        "title": "물놀이",
        "starts_at": "2026-09-18T10:00:00+09:00",
        "all_day": False,
        "items": ["수영복"],
    }


async def test_이름이_빈_준비물은_거부한다(context: AgentContext) -> None:
    result = await execute_tool(
        "create_event",
        {"title": "물놀이", "starts_on": "금요일", "starts_time": "오전 10시", "items": ["  "]},
        context,
    )

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.VALIDATION_ERROR
    assert context.drafts.all() == ()


async def test_준비물_이름의_앞뒤_공백은_지운다(context: AgentContext) -> None:
    await _create_event(context, items=[" 수영복 "])

    assert [item.item_name for item in context.drafts.all()[0].items] == ["수영복"]


async def test_일정_두_개를_부르면_초안도_두_장이다(context: AgentContext) -> None:
    # 새 일정끼리는 합칠 id 가 없다
    await _create_event(context, title="물놀이")
    await _create_event(context, title="소풍", starts_on="다음주 월요일")

    assert [draft.title for draft in context.drafts.all()] == ["물놀이", "소풍"]


# ── update_event ────────────────────────────────────────────────
SEEDED_START = datetime(2026, 9, 18, 9, 0, tzinfo=KST)


async def _seed_event(context: AgentContext, *items: str) -> str:
    """제출 API 가 저장해 둔 일정. store 를 직접 부른다."""
    row = await context.store.create_event(
        child_id=context.child_id,
        title="운동회",
        starts_at=SEEDED_START,
        ends_at=None,
        all_day=False,
        fields={"event_type": "episodic", "category": "institution"},
    )
    for name in items:
        await context.store.create_event_item(event_id=row.id, item_name=name)
    return row.id


async def _update(context: AgentContext, event_id: str, **args: Any) -> dict[str, Any]:
    result = await execute_tool("update_event", {"event_id": event_id, **args}, context)
    assert result.success is True, result.error
    return result.data


async def test_update_event_는_store_행을_고치지_않는다(context: AgentContext) -> None:
    event_id = await _seed_event(context)

    data = await _update(context, event_id, title="가을 운동회", starts_time="오후 3시")

    saved = await context.store.get_event(event_id=event_id)
    assert saved is not None
    assert saved.title == "운동회"
    assert saved.starts_at == SEEDED_START
    assert data["draft"] is True
    assert data["changed"] == ["title", "starts_at"]


async def test_말하지_않은_필드는_현재_값_그대로_초안에_실린다(context: AgentContext) -> None:
    event_id = await _seed_event(context)

    await _update(context, event_id, title="가을 운동회")

    draft = context.drafts.get(event_id)
    assert draft is not None
    assert draft.op == "update"
    assert draft.event_id == event_id
    assert draft.starts_at == SEEDED_START
    assert draft.ends_at is None
    assert draft.all_day is False
    assert draft.event_type == "episodic"
    assert draft.category == "institution"
    assert draft.changed == ("title",)


async def test_저장된_준비물은_item_id_와_함께_초안에_실린다(context: AgentContext) -> None:
    # 제출 API 가 무엇을 UPDATE 하고 무엇을 INSERT 할지 가르는 값이다
    event_id = await _seed_event(context, "체육복", "물통")

    await _update(context, event_id, title="가을 운동회")

    draft = context.drafts.get(event_id)
    assert draft is not None
    assert [(item.item_id, item.item_name) for item in draft.items] == [
        ("event_item-1", "체육복"),
        ("event_item-2", "물통"),
    ]
    assert "items" not in draft.changed  # 준비물은 건드리지 않았다


async def test_같은_값으로_바꾸면_초안을_만들지_않는다(context: AgentContext) -> None:
    event_id = await _seed_event(context)

    data = await _update(context, event_id, title="운동회", starts_time="오전 9시")

    assert data["draft"] is False
    assert data["changed"] == []
    assert context.drafts.all() == ()


async def test_같은_일정을_두_번_고치면_초안_하나에_쌓인다(context: AgentContext) -> None:
    event_id = await _seed_event(context)

    await _update(context, event_id, title="가을 운동회")
    data = await _update(context, event_id, starts_time="오후 3시")

    drafts = context.drafts.all()
    assert len(drafts) == 1
    assert drafts[0].title == "가을 운동회"  # 앞의 수정이 덮이지 않는다
    assert _local(drafts[0].starts_at) == "2026-09-18T15:00+09:00"
    assert data["changed"] == ["title", "starts_at"]


def _local(moment: datetime) -> str:
    return moment.astimezone(KST).isoformat(timespec="minutes")


async def test_없는_일정을_고치면_UNKNOWN_EVENT(context: AgentContext) -> None:
    result = await execute_tool("update_event", {"event_id": "event-404", "title": "소풍"}, context)

    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == ErrorCode.UNKNOWN_EVENT
    assert context.drafts.all() == ()


# ── 즉시 쓰기와 초안의 어긋남 (FE 리뷰) ──────────────────────────
async def _seed_checked(context: AgentContext) -> str:
    """운동회 15:00 + 체육복 미체크."""
    row = await context.store.create_event(
        child_id=context.child_id,
        title="운동회",
        starts_at=SEEDED_START,
        ends_at=None,
        all_day=False,
        fields={"event_type": "episodic", "category": "institution"},
    )
    await context.store.create_event_item(event_id=row.id, item_name="체육복")
    return row.id


@pytest.mark.parametrize("check_first", [False, True], ids=["수정_먼저", "체크_먼저"])
async def test_챙김_표시는_순서와_무관하게_초안에_비친다(
    context: AgentContext, check_first: bool
) -> None:
    # "운동회 5시로 옮기고, 체육복은 챙겼어" — 한 응답에 둘이 같이 온다.
    # 즉시 쓰기가 버퍼를 안 건드리면 수정이 먼저인 경우 초안이 옛 값을 들고 나가고,
    # items 가 최종 목록이라 제출하는 순간 방금 한 체크가 풀린다
    event_id = await _seed_checked(context)
    check = ("update_event_item", {"item_id": "event_item-1", "is_prepared": True})
    edit = ("update_event", {"event_id": event_id, "starts_time": "오후 5시"})

    for name, args in [check, edit] if check_first else [edit, check]:
        result = await execute_tool(name, args, context)
        assert result.success is True, result.error

    draft = context.drafts.get(event_id)
    assert draft is not None
    assert [item.is_prepared for item in draft.items] == [True]
    stored = await context.store.get_event_item(item_id="event_item-1")
    assert stored is not None and stored.is_prepared is True


async def test_챙김_표시는_before_에도_비친다(context: AgentContext) -> None:
    # 이미 DB 에 들어간 값이라 "수정 전 원본" 이 그쪽이다.
    # 안 맞추면 화면이 준비물을 바뀐 것으로 표시한다
    event_id = await _seed_checked(context)

    await execute_tool("update_event", {"event_id": event_id, "starts_time": "오후 5시"}, context)
    await execute_tool(
        "update_event_item", {"item_id": "event_item-1", "is_prepared": True}, context
    )

    draft = context.drafts.get(event_id)
    assert draft is not None and draft.before is not None
    assert [item.is_prepared for item in draft.before.items] == [True]
    assert draft.before.items == draft.items  # 준비물은 바뀐 게 없다


async def test_챙김_표시가_초안을_새로_만들지는_않는다(context: AgentContext) -> None:
    await _seed_checked(context)

    await execute_tool(
        "update_event_item", {"item_id": "event_item-1", "is_prepared": True}, context
    )

    assert context.drafts.all() == ()


# ── draft_id ────────────────────────────────────────────────────
async def test_초안마다_draft_id_가_붙는다(context: AgentContext) -> None:
    event_id = await _seed_event(context)

    await _create_event(context, title="물놀이")
    await _update(context, event_id, title="가을 운동회")

    ids = [draft.draft_id for draft in context.drafts.all()]
    assert ids == ["d1", "d2"]
    assert all(draft.to_payload()["draft_id"] for draft in context.drafts.all())


async def test_같은_일정을_두_번_고쳐도_draft_id_는_그대로다(context: AgentContext) -> None:
    # 화면이 초안을 가리키는 키라 중간에 바뀌면 안 된다
    event_id = await _seed_event(context)

    await _update(context, event_id, title="가을 운동회")
    first = context.drafts.get(event_id)
    await _update(context, event_id, starts_time="오후 3시")
    second = context.drafts.get(event_id)

    assert first is not None and second is not None
    assert first.draft_id == second.draft_id


async def test_수정_초안의_before_는_DB_원본이다(context: AgentContext) -> None:
    event_id = await _seed_event(context, "체육복")

    await _update(context, event_id, title="가을 운동회", starts_time="오후 3시")

    draft = context.drafts.get(event_id)
    assert draft is not None and draft.before is not None
    assert draft.before.title == "운동회"
    assert draft.before.starts_at == SEEDED_START
    assert [item.item_name for item in draft.before.items] == ["체육복"]
    assert draft.title == "가을 운동회"  # 초안 쪽은 바뀐 값이다
