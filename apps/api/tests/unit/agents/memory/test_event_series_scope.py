"""반복 일정을 한 회차 또는 묶음 전체로 고치고 지우기 (#738).

대상이 반복 묶음의 회차이면 모델이 `scope` 로 범위를 정한다 — 날짜를 집어 말했으면 one, 날짜 없이
그 반복 일정 자체를 말했으면 series. 범위가 없으면 기본값으로 한 회차만 고치지 않고 되돌린다.
챙김 표시(`is_prepared`)는 늘 그날 회차 하나라 범위를 받지 않는다.

오늘은 2026-11-11(수) 09:00 이다. 묶음은 월 · 수 08:30, 11/9 · 11/11 · 11/16 · 11/18 이고
준비물은 "물통" 이다. 오늘 이후 회차는 11/11 · 11/16 · 11/18 인데, 11/11 08:30 은 이미 시작해서
묶음 고치기가 보는 회차는 11/16 · 11/18 이다 (시각을 따로 옮겨 두면 11/11 도 든다, #761).
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.datetime_rules import EventWhen
from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import DraftBook, EventDraft, RepeatRule, SeriesDraft, SeriesSnapshot
from app.agents.memory.registry import execute_tool
from app.agents.memory.result import ErrorCode
from app.agents.memory.store import InMemoryStore
from app.agents.memory.store.ports import EventItemRow, EventRow, SeriesRow
from app.core.event_draft import UpdateSeriesDraft

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=KST)
LABEL = "11월 18일까지 매주 월, 수"


@dataclass
class World:
    ctx: AgentContext
    store: InMemoryStore
    series: SeriesRow
    events: list[EventRow]  # 11/9 · 11/11 · 11/16 · 11/18
    plain: EventRow
    plain_item: EventItemRow


async def _build(
    *, weekdays: tuple[int, ...], until: date, item_names: tuple[str, ...] = ("물통",)
) -> World:
    store = InMemoryStore(now=NOW)
    series, events = await store.create_series(
        child_id=CHILD,
        title="등원",
        weekdays=weekdays,
        starts_on=date(2026, 11, 9),
        until=until,
        start_time=time(8, 30),
        end_time=None,
        tz=KST,
        fields={"event_type": "core", "category": "institution"},
        item_names=item_names,
    )
    plain = await store.create_event(
        child_id=CHILD,
        title="운동회",
        starts_at=datetime(2026, 11, 20, 10, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        fields={"event_type": "episodic", "category": "institution"},
    )
    plain_item = await store.create_event_item(event_id=plain.id, item_name="체육복")
    ctx = AgentContext(
        child_id=CHILD, source_writer=UUID(int=2), now=NOW, timezone=KST, store=store
    )
    return World(ctx, store, series, events, plain, plain_item)


@pytest.fixture
async def world() -> World:
    return await _build(weekdays=(1, 3), until=date(2026, 11, 18))


async def _call(world: World, name: str, **args: Any):
    return await execute_tool(name, args, world.ctx)


async def _items(world: World, event: EventRow) -> list[EventItemRow]:
    return await world.store.list_event_items(event_id=event.id)


def _series_draft(world: World) -> SeriesDraft:
    draft = world.ctx.drafts.get_series(world.series.id)
    assert draft is not None
    return draft


# ── query ────────────────────────────────────────────────────────


async def test_query_names_the_series_of_each_occurrence(world):
    result = await _call(world, "query_event")

    assert result.success is True, result.error
    by_id = {event["id"]: event for event in result.data["events"]}
    assert by_id[world.events[1].id]["series"] == {"id": world.series.id, "label": LABEL}
    assert "series" not in by_id[world.plain.id]


# ── update_event ─────────────────────────────────────────────────


async def test_update_of_an_occurrence_without_scope_asks_which(world):
    result = await _call(world, "update_event", event_id=world.events[1].id, starts_time="오전 9시")

    assert result.success is False
    assert result.error["code"] == ErrorCode.SCOPE_REQUIRED
    assert "scope" in result.error["message"]
    assert world.ctx.drafts.all() == ()


async def test_update_series_makes_one_series_draft(world):
    result = await _call(
        world, "update_event", event_id=world.events[1].id, scope="series", starts_time="오전 9시"
    )

    assert result.success is True, result.error
    assert result.data["draft"] is True and result.data["changed"] == ["starts_time"]
    [draft] = world.ctx.drafts.all()
    assert isinstance(draft, SeriesDraft)
    assert draft.series_id == world.series.id and draft.starts_time == time(9, 0)
    payload = draft.to_payload()
    assert payload["op"] == "update_series"
    assert payload["event"]["starts_time"] == "09:00"
    assert payload["before"]["starts_time"] == "08:30"
    assert payload["repeat"]["label"] == LABEL
    assert payload["items"] == ["물통"] and payload["before"]["items"] == ["물통"]
    assert UpdateSeriesDraft.model_validate(payload).model_dump(mode="json") == payload


async def test_update_series_does_not_touch_the_store(world):
    await _call(world, "update_event", event_id=world.events[1].id, scope="series", title="유치원")

    assert (await world.store.get_event(event_id=world.events[1].id)).title == "등원"


async def test_update_of_one_occurrence_is_an_ordinary_update_draft(world):
    result = await _call(
        world, "update_event", event_id=world.events[2].id, scope="one", starts_time="오전 9시"
    )

    assert result.success is True, result.error
    [draft] = world.ctx.drafts.all()
    assert isinstance(draft, EventDraft) and draft.op == "update"
    assert draft.event_id == world.events[2].id
    assert draft.starts_at == datetime(2026, 11, 16, 9, 0, tzinfo=KST)


async def test_update_series_twice_in_a_run_is_one_draft_with_the_first_before(world):
    event_id = world.events[2].id
    await _call(world, "update_event", event_id=event_id, scope="series", starts_time="오전 9시")
    result = await _call(world, "update_event", event_id=event_id, scope="series", title="유치원")

    assert result.data["changed"] == ["title", "starts_time"]
    [draft] = world.ctx.drafts.all()
    assert draft.title == "유치원" and draft.starts_time == time(9, 0)
    assert draft.before.title == "등원" and draft.before.starts_time == time(8, 30)


async def test_update_series_with_the_same_values_makes_no_draft(world):
    result = await _call(
        world,
        "update_event",
        event_id=world.events[1].id,
        scope="series",
        starts_time="오전 8시 30분",
    )

    assert result.success is True
    assert result.data["draft"] is False and result.data["changed"] == []
    assert world.ctx.drafts.all() == ()


async def test_update_series_changes_the_weekdays(world):
    result = await _call(
        world,
        "update_event",
        event_id=world.events[1].id,
        scope="series",
        repeat_weekdays=["tue", "thu"],
    )

    assert result.success is True, result.error
    assert result.data["changed"] == ["repeat"]
    draft = _series_draft(world)
    assert draft.repeat == RepeatRule(weekdays=(2, 4), until=date(2026, 11, 18))
    assert draft.to_payload()["repeat"]["label"] == "11월 18일까지 매주 화, 목"


async def test_update_series_extends_the_end_date(world):
    result = await _call(
        world,
        "update_event",
        event_id=world.events[1].id,
        scope="series",
        repeat_until="12월 2일",
    )

    assert result.success is True, result.error
    assert _series_draft(world).repeat.until == date(2026, 12, 2)


async def test_update_series_end_date_is_counted_from_the_original_start(world):
    # 시작 11/9 에서 90일은 2027-02-06 까지다
    result = await _call(
        world,
        "update_event",
        event_id=world.events[1].id,
        scope="series",
        repeat_until="2027년 2월 7일",
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE
    assert "90일" in result.error["message"]
    assert world.ctx.drafts.all() == ()


async def test_update_series_end_date_already_passed_asks(world):
    result = await _call(
        world,
        "update_event",
        event_id=world.events[1].id,
        scope="series",
        repeat_until="11월 10일",
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE


async def test_update_series_to_all_day_drops_the_times(world):
    await _call(
        world, "update_event", event_id=world.events[1].id, scope="series", starts_time="하루 종일"
    )

    draft = _series_draft(world)
    assert draft.all_day is True and draft.starts_time is None and draft.ends_time is None
    payload = draft.to_payload()
    assert payload["event"]["starts_time"] is None and payload["event"]["all_day"] is True


async def test_update_series_end_time_and_clearing_it(world):
    event_id = world.events[1].id
    await _call(world, "update_event", event_id=event_id, scope="series", ends_time="오전 9시")
    assert _series_draft(world).ends_time == time(9, 0)

    # 끝 시각을 지워 원본(끝 없음)과 같아지면 바뀐 게 없으니 초안을 뺀다
    result = await _call(
        world, "update_event", event_id=event_id, scope="series", clear=["ends_at"]
    )
    assert result.success is True, result.error
    assert result.data["draft"] is False and result.data["changed"] == []
    assert world.ctx.drafts.all() == ()


async def test_update_series_end_time_must_be_after_start(world):
    result = await _call(
        world, "update_event", event_id=world.events[1].id, scope="series", ends_time="오전 8시"
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS


@pytest.mark.parametrize("field", ["starts_on", "ends_on"])
async def test_update_series_takes_no_dates(world, field):
    result = await _call(
        world, "update_event", event_id=world.events[1].id, scope="series", **{field: "11월 20일"}
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "scope=one" in result.error["message"]


@pytest.mark.parametrize("scope", [None, "one"])
async def test_repeat_args_need_series_scope(world, scope):
    result = await _call(
        world,
        "update_event",
        event_id=world.events[1].id,
        repeat_weekdays=["tue"],
        **({} if scope is None else {"scope": scope}),
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS


async def test_series_scope_on_a_plain_event_is_rejected(world):
    result = await _call(
        world, "update_event", event_id=world.plain.id, scope="series", starts_time="오전 11시"
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert "반복" in result.error["message"]


async def test_plain_event_needs_no_scope(world):
    result = await _call(world, "update_event", event_id=world.plain.id, starts_time="오전 11시")

    assert result.success is True, result.error


async def test_update_series_with_no_future_occurrence_is_not_found(world):
    late = AgentContext(
        child_id=CHILD,
        source_writer=UUID(int=2),
        now=datetime(2026, 11, 20, 9, 0, tzinfo=KST),
        timezone=KST,
        store=world.store,
    )

    result = await execute_tool(
        "update_event",
        {"event_id": world.events[1].id, "scope": "series", "title": "유치원"},
        late,
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.TARGET_NOT_FOUND


# ── create_event_item ────────────────────────────────────────────


async def test_adding_an_item_to_an_occurrence_without_scope_asks_which(world):
    result = await _call(
        world, "create_event_item", event_id=world.events[1].id, item_name="실내화"
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.SCOPE_REQUIRED


async def test_adding_an_item_to_the_series_goes_on_the_series_draft(world):
    result = await _call(
        world, "create_event_item", event_id=world.events[1].id, item_name="실내화", scope="series"
    )

    assert result.success is True, result.error
    assert result.data["draft"] is True and result.data["added"] is True
    draft = _series_draft(world)
    assert draft.items == ("물통", "실내화") and draft.changed == ("items",)
    assert draft.to_payload()["items"] == ["물통", "실내화"]
    assert [i.item_name for i in await _items(world, world.events[1])] == ["물통"]


async def test_adding_an_existing_item_name_to_the_series_adds_nothing(world):
    result = await _call(
        world, "create_event_item", event_id=world.events[1].id, item_name="물통", scope="series"
    )

    assert result.success is True
    assert result.data["draft"] is False and result.data["added"] is False
    assert world.ctx.drafts.all() == ()


async def test_adding_an_item_to_one_occurrence_is_an_ordinary_update_draft(world):
    result = await _call(
        world, "create_event_item", event_id=world.events[2].id, item_name="실내화", scope="one"
    )

    assert result.success is True, result.error
    [draft] = world.ctx.drafts.all()
    assert isinstance(draft, EventDraft) and draft.event_id == world.events[2].id
    assert [i.item_name for i in draft.items] == ["물통", "실내화"]


# ── update_event_item ────────────────────────────────────────────


async def test_checking_an_item_needs_no_scope_and_marks_only_that_day(world):
    target = (await _items(world, world.events[2]))[0]

    result = await _call(world, "update_event_item", item_id=target.item_id, is_prepared=True)

    assert result.success is True, result.error
    assert (await _items(world, world.events[2]))[0].is_prepared is True
    for event in (world.events[0], world.events[1], world.events[3]):
        assert (await _items(world, event))[0].is_prepared is False


async def test_checking_an_item_with_series_scope_is_rejected(world):
    target = (await _items(world, world.events[2]))[0]

    result = await _call(
        world, "update_event_item", item_id=target.item_id, is_prepared=True, scope="series"
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert (await _items(world, world.events[2]))[0].is_prepared is False


async def test_renaming_an_occurrence_item_without_scope_asks_which(world):
    target = (await _items(world, world.events[2]))[0]

    result = await _call(world, "update_event_item", item_id=target.item_id, item_name="물병")

    assert result.success is False
    assert result.error["code"] == ErrorCode.SCOPE_REQUIRED


async def test_renaming_an_item_for_the_series_replaces_the_name_on_the_draft(world):
    target = (await _items(world, world.events[2]))[0]

    result = await _call(
        world, "update_event_item", item_id=target.item_id, item_name="물병", scope="series"
    )

    assert result.success is True, result.error
    assert result.data["draft"] is True
    assert _series_draft(world).items == ("물병",)
    assert (await _items(world, world.events[2]))[0].item_name == "물통"


async def test_renaming_an_item_for_one_occurrence_is_an_ordinary_update_draft(world):
    target = (await _items(world, world.events[2]))[0]

    result = await _call(
        world, "update_event_item", item_id=target.item_id, item_name="물병", scope="one"
    )

    assert result.success is True, result.error
    [draft] = world.ctx.drafts.all()
    assert isinstance(draft, EventDraft) and [i.item_name for i in draft.items] == ["물병"]


# ── delete_event_item ────────────────────────────────────────────


async def test_removing_an_occurrence_item_without_scope_asks_which(world):
    target = (await _items(world, world.events[2]))[0]

    result = await _call(world, "delete_event_item", item_id=target.item_id)

    assert result.success is False
    assert result.error["code"] == ErrorCode.SCOPE_REQUIRED
    assert len(await _items(world, world.events[2])) == 1


async def test_removing_an_item_for_the_series_edits_the_series_draft_only(world):
    target = (await _items(world, world.events[2]))[0]

    result = await _call(world, "delete_event_item", item_id=target.item_id, scope="series")

    assert result.success is True, result.error
    assert result.data["draft"] is True and result.data["scope"] == "series"
    draft = _series_draft(world)
    assert draft.items == () and draft.changed == ("items",)
    # 저장은 그대로다. 보호자가 확인하고 제출해야 오늘 이후 회차에서 빠진다
    for event in world.events:
        assert [i.item_name for i in await _items(world, event)] == ["물통"]


async def test_removing_a_renamed_item_for_the_series_removes_the_new_name(world):
    target = (await _items(world, world.events[2]))[0]
    await _call(
        world, "update_event_item", item_id=target.item_id, item_name="텀블러", scope="series"
    )

    result = await _call(world, "delete_event_item", item_id=target.item_id, scope="series")

    assert result.success is True, result.error
    assert _series_draft(world).items == ()


async def test_removing_an_item_the_series_draft_already_dropped_is_not_found(world):
    target = (await _items(world, world.events[2]))[0]
    await _call(world, "delete_event_item", item_id=target.item_id, scope="series")

    result = await _call(world, "delete_event_item", item_id=target.item_id, scope="series")

    assert result.success is False
    assert result.error["code"] == ErrorCode.TARGET_NOT_FOUND


async def test_adding_a_removed_item_back_leaves_no_change(world):
    target = (await _items(world, world.events[2]))[0]
    await _call(world, "delete_event_item", item_id=target.item_id, scope="series")

    result = await _call(
        world, "create_event_item", event_id=world.events[2].id, item_name="물통", scope="series"
    )

    assert result.success is True, result.error
    assert result.data["draft"] is False
    assert world.ctx.drafts.all() == ()


async def test_removing_an_item_for_one_occurrence_keeps_the_rest(world):
    target = (await _items(world, world.events[2]))[0]

    result = await _call(world, "delete_event_item", item_id=target.item_id, scope="one")

    assert result.success is True, result.error
    assert await _items(world, world.events[2]) == []
    assert len(await _items(world, world.events[3])) == 1


# ── 묶음의 기준값: 개별로 고친 회차가 묶음 전체로 번지지 않게 ───────────


async def _edit_one(world: World, event: EventRow, **changes: Any) -> None:
    """한 회차만 따로 고친 상태를 만든다 (이미 승인돼 저장된 개별 수정)."""
    when = None
    if "starts_at" in changes:
        when = EventWhen(starts_at=changes.pop("starts_at"), ends_at=None, all_day=False)
    await world.store.update_event(event_id=event.id, fields=changes, when=when)


async def test_series_draft_uses_the_common_values_not_the_first_occurrence(world):
    # 11/11 은 10:00 으로 따로 옮겼고 나머지는 08:30 이다. 첫 회차(11/11)에서 기준을 잡으면
    # "실내화 추가" 초안이 시각 10:00 을 들고 가서, 제출하면 08:30 회차가 전부 10:00 이 된다
    await _edit_one(world, world.events[1], starts_at=datetime(2026, 11, 11, 10, 0, tzinfo=KST))

    result = await _call(
        world, "create_event_item", event_id=world.events[2].id, item_name="실내화", scope="series"
    )

    assert result.success is True, result.error
    draft = _series_draft(world)
    assert draft.starts_time == time(8, 30) and draft.changed == ("items",)
    payload = draft.to_payload()
    assert payload["event"]["starts_time"] == payload["before"]["starts_time"] == "08:30"


async def test_series_draft_title_is_the_common_title(world):
    await _edit_one(world, world.events[1], title="등원 준비")

    await _call(
        world, "create_event_item", event_id=world.events[2].id, item_name="실내화", scope="series"
    )

    draft = _series_draft(world)
    assert draft.title == "등원" and draft.before.title == "등원"


# ── 묶음은 늘 core, 준비물 이름은 여러 번 바꿔도 따라간다 ───────────────


async def test_update_series_cannot_make_it_episodic(world):
    result = await _call(
        world, "update_event", event_id=world.events[1].id, scope="series", event_type="episodic"
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.INVALID_ARGS
    assert world.ctx.drafts.all() == ()


async def test_update_series_with_core_is_not_a_change(world):
    result = await _call(
        world, "update_event", event_id=world.events[1].id, scope="series", event_type="core"
    )

    assert result.success is True, result.error
    assert result.data["draft"] is False


async def _rename(world: World, event: EventRow, name: str):
    target = (await _items(world, event))[0]
    return await _call(
        world, "update_event_item", item_id=target.item_id, item_name=name, scope="series"
    )


async def test_renaming_the_same_item_twice_in_a_run_follows_the_draft(world):
    await _rename(world, world.events[2], "텀블러")

    result = await _rename(world, world.events[2], "보온병")

    assert result.success is True, result.error
    assert _series_draft(world).items == ("보온병",)


async def test_renaming_an_item_back_to_its_name_leaves_no_change(world):
    await _rename(world, world.events[2], "텀블러")

    result = await _rename(world, world.events[2], "물통")

    assert result.success is True, result.error
    assert result.data["draft"] is False
    assert world.ctx.drafts.all() == ()


# ── 초안 버퍼 ────────────────────────────────────────────────────


def _snapshot() -> SeriesSnapshot:
    return SeriesSnapshot(
        title="등원",
        starts_time=time(8, 30),
        ends_time=None,
        all_day=False,
        event_type="core",
        category="institution",
        repeat=RepeatRule(weekdays=(1, 3), until=date(2026, 11, 18)),
        items=("물통",),
    )


def _draft(**changes: Any) -> SeriesDraft:
    base = _snapshot()
    values = {
        "series_id": "series-1",
        "title": base.title,
        "starts_time": base.starts_time,
        "ends_time": base.ends_time,
        "all_day": base.all_day,
        "event_type": base.event_type,
        "category": base.category,
        "repeat": base.repeat,
        "items": base.items,
        "before": base,
        **changes,
    }
    return SeriesDraft(**values)


def test_series_draft_keeps_its_draft_id_when_put_again():
    book = DraftBook()
    first = book.put_series(_draft())

    second = book.put_series(_draft(title="유치원"))

    assert first.draft_id and second.draft_id == first.draft_id
    assert book.get_series("series-1").title == "유치원"
    assert len(book.all()) == 1


def test_draft_book_keeps_the_order_across_event_and_series_drafts():
    book = DraftBook()
    book.put_series(_draft())
    book.put(
        EventDraft(
            op="create",
            event_id=None,
            title="운동회",
            starts_at=datetime(2026, 11, 20, 10, 0, tzinfo=KST),
            ends_at=None,
            all_day=False,
            event_type="episodic",
            category="institution",
        )
    )

    kinds = [type(draft).__name__ for draft in book.all()]

    assert kinds == ["SeriesDraft", "EventDraft"]
    assert len({draft.draft_id for draft in book.all()}) == 2


def test_draft_book_drop_series():
    book = DraftBook()
    book.put_series(_draft())

    book.drop_series("series-1")

    assert book.get_series("series-1") is None and book.all() == ()


# ── PR #744 리뷰: 기준 값은 항목마다 따로 센다 ───────────────────────
# 6개 값을 한 표로 세면 제목 하나만 달라도 표가 쪼개져 소수 값이 기준이 된다. 시각(시작 · 끝 ·
# 종일)은 "종일인데 8:30" 같은 조합이 나오지 않게 한 묶음으로 센다.


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


async def _first_series_draft(world: World) -> SeriesDraft:
    """준비물만 하나 더해 묶음 초안을 연다. 기준 값(before)을 보려는 것이다."""
    result = await _call(
        world, "create_event_item", event_id=world.events[-1].id, item_name="실내화", scope="series"
    )
    assert result.success is True, result.error
    return _series_draft(world)


async def test_common_values_are_counted_per_field_when_titles_differ():
    # 오늘 이후 (등원,8:30) (등원 A,8:30) (등원 B,8:30) (등원,9:00) (등원,9:00)
    world = await _build(weekdays=(1,), until=date(2026, 12, 14))
    future = world.events[1:]  # 11/16 · 11/23 · 11/30 · 12/7 · 12/14
    await _edit_one(world, future[1], title="등원 A")
    await _edit_one(world, future[2], title="등원 B")
    await _edit_one(world, future[3], starts_at=_at(date(2026, 12, 7), 9))
    await _edit_one(world, future[4], starts_at=_at(date(2026, 12, 14), 9))

    draft = await _first_series_draft(world)

    assert draft.before.title == "등원"
    assert draft.before.starts_time == time(8, 30)
    assert draft.changed == ("items",)


async def test_common_values_follow_the_majority_of_each_field():
    # 오늘 이후 (등원,8:30) (등원,9:00) (등원2,9:00) — 제목은 등원, 시각은 9:00 이 흔하다
    world = await _build(weekdays=(1,), until=date(2026, 11, 30))
    future = world.events[1:]  # 11/16 · 11/23 · 11/30
    await _edit_one(world, future[1], starts_at=_at(date(2026, 11, 23), 9))
    await _edit_one(world, future[2], title="등원2", starts_at=_at(date(2026, 11, 30), 9))

    draft = await _first_series_draft(world)

    assert draft.before.title == "등원"
    assert draft.before.starts_time == time(9, 0)


# ── PR #761 리뷰: 시작이 지난 오늘 회차는 묶음 고치기의 대상이 아니다 ─────────
# 서버(`update_series`)는 시간 일정이면 시작이 지금 이후인 회차, 종일이면 오늘(그날 포함) 이후
# 회차만 고친다. 기준 값(before)과 "남은 회차가 있나" 판정이 같은 회차를 봐야 승인한 초안이 서버에서
# 0건 또는 422 가 되지 않는다. 지금은 11/11(수) 09:00 이고 08:30 회차는 이미 시작했다.


async def _only_today_world(*, all_day: bool = False) -> World:
    """11/11(수) 회차 하나뿐인 묶음."""
    world = await _build(weekdays=(3,), until=date(2026, 11, 11))
    if all_day:
        when = EventWhen(starts_at=_at(date(2026, 11, 11), 0), ends_at=None, all_day=True)
        await world.store.update_event(event_id=world.events[0].id, fields={}, when=when)
    return world


async def test_update_series_whose_last_occurrence_started_today_is_not_found():
    world = await _only_today_world()

    result = await _call(
        world, "update_event", event_id=world.events[0].id, scope="series", title="유치원"
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.TARGET_NOT_FOUND
    assert world.ctx.drafts.all() == ()


async def test_series_item_on_a_series_whose_last_occurrence_started_today_is_not_found():
    world = await _only_today_world()

    result = await _call(
        world, "create_event_item", event_id=world.events[0].id, item_name="실내화", scope="series"
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.TARGET_NOT_FOUND


async def test_an_all_day_occurrence_of_today_is_still_ahead():
    world = await _only_today_world(all_day=True)

    result = await _call(
        world, "update_event", event_id=world.events[0].id, scope="series", title="유치원"
    )

    assert result.success is True, result.error
    assert _series_draft(world).before.all_day is True


async def test_common_values_leave_out_the_started_occurrence_of_today():
    # 11/11(시작함) · 11/16 은 "등원 준비", 11/18 · 11/23 은 "등원". 11/11 을 세면 2:2 로 먼저 나온
    # "등원 준비" 가 뽑혀, 서버가 고치는 회차(11/16 · 11/18 · 11/23)의 흔한 값과 달라진다
    world = await _build(weekdays=(1, 3), until=date(2026, 11, 23))
    await _edit_one(world, world.events[1], title="등원 준비")
    await _edit_one(world, world.events[2], title="등원 준비")

    draft = await _first_series_draft(world)

    assert draft.before.title == "등원"


async def test_new_rule_left_with_only_the_started_occurrence_of_today_asks(world):
    result = await _call(
        world,
        "update_event",
        event_id=world.events[2].id,
        scope="series",
        repeat_weekdays=["wed"],
        repeat_until="11월 11일",
    )

    assert result.success is False
    assert result.error["code"] == ErrorCode.DATE_UNPARSEABLE
    assert world.ctx.drafts.all() == ()


async def test_new_rule_counts_today_when_the_new_start_is_still_ahead(world):
    # 같은 규칙이라도 시각을 10시로 같이 옮기면 오늘 회차는 아직 시작 전이다
    # (서버도 새 시각으로 본다)
    result = await _call(
        world,
        "update_event",
        event_id=world.events[2].id,
        scope="series",
        repeat_weekdays=["wed"],
        repeat_until="11월 11일",
        starts_time="오전 10시",
    )

    assert result.success is True, result.error
    assert _series_draft(world).repeat == RepeatRule(weekdays=(3,), until=date(2026, 11, 11))


# ── PR #744 리뷰: 준비물은 이름 집합으로 바뀜을 본다 ──────────────────

_THREE = ("물통", "도시락", "색연필")


async def _three_items_world() -> World:
    return await _build(weekdays=(1, 3), until=date(2026, 11, 18), item_names=_THREE)


async def _item_id(world: World, name: str) -> str:
    return next(i.item_id for i in await _items(world, world.events[2]) if i.item_name == name)


async def _remove(world: World, name: str) -> None:
    result = await _call(
        world, "delete_event_item", item_id=await _item_id(world, name), scope="series"
    )
    assert result.success is True, result.error


async def _add(world: World, name: str):
    return await _call(
        world, "create_event_item", event_id=world.events[2].id, item_name=name, scope="series"
    )


@pytest.mark.parametrize(
    ("removed", "added"),
    [
        (["물통"], ["물통"]),
        (["도시락"], ["도시락"]),
        (["색연필"], ["색연필"]),
        (list(_THREE), list(_THREE)),
        (list(_THREE), list(reversed(_THREE))),
    ],
)
async def test_removing_and_adding_back_items_in_any_order_is_no_change(removed, added):
    world = await _three_items_world()
    for name in removed:
        await _remove(world, name)
    for name in added:
        result = await _add(world, name)

    assert result.success is True, result.error
    assert result.data["draft"] is False
    assert world.ctx.drafts.all() == ()


async def test_a_real_item_change_is_still_a_draft():
    world = await _three_items_world()

    await _remove(world, "도시락")

    assert _series_draft(world).changed == ("items",)
    assert _series_draft(world).items == ("물통", "색연필")
