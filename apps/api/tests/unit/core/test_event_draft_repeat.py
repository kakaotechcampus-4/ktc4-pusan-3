"""반복 일정 초안 payload 계약 (#738).

반복이 아닌 초안의 모양은 그대로다 — 항상 null 인 키는 싣지 않는 규칙이라 `repeat` 도 반복
초안에만 있다. 묶음 고치기는 `op="update_series"` 로 따로 간다.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.event_draft import (
    CreateEventDraft,
    CreateRepeatEventDraft,
    DraftItemPayload,
    EventBody,
    RepeatPayload,
    SeriesBefore,
    SeriesEventBody,
    UpdateSeriesDraft,
)

KST = ZoneInfo("Asia/Seoul")
REPEAT = RepeatPayload(weekdays=[1, 3], until=date(2026, 12, 20), label="12월 20일까지 매주 월, 수")


def _body() -> EventBody:
    return EventBody(
        title="등원",
        starts_at=datetime(2026, 11, 9, 8, 30, tzinfo=KST),
        ends_at=None,
        all_day=False,
        event_type="core",
        category="institution",
    )


def _series_body(**changes) -> SeriesEventBody:
    values = {
        "title": "등원",
        "starts_time": "08:30",
        "ends_time": None,
        "all_day": False,
        "event_type": "core",
        "category": "institution",
        **changes,
    }
    return SeriesEventBody(**values)


def test_repeat_draft_carries_repeat():
    payload = CreateRepeatEventDraft(
        draft_id="d1",
        op="create",
        event=_body(),
        items=[DraftItemPayload(item_id=None, item_name="물통")],
        repeat=REPEAT,
    ).model_dump(mode="json")

    assert payload["repeat"] == {
        "weekdays": [1, 3],
        "until": "2026-12-20",
        "label": "12월 20일까지 매주 월, 수",
    }
    assert set(payload) == {"draft_id", "op", "event", "items", "repeat"}


def test_plain_create_draft_has_no_repeat_key():
    plain = CreateEventDraft(draft_id="d1", op="create", event=_body(), items=[])
    assert "repeat" not in plain.model_dump(mode="json")
    with pytest.raises(ValidationError):
        CreateEventDraft.model_validate({**plain.model_dump(mode="json"), "repeat": None})


def test_repeat_draft_requires_repeat():
    with pytest.raises(ValidationError):
        CreateRepeatEventDraft(draft_id="d1", op="create", event=_body(), items=[])


def test_update_series_roundtrip():
    draft = UpdateSeriesDraft(
        draft_id="d2",
        op="update_series",
        series_id="series-1",
        event=_series_body(starts_time="09:00"),
        repeat=REPEAT,
        before=SeriesBefore(**_series_body().model_dump(), repeat=REPEAT, items=["물통"]),
        items=["물통", "실내화"],
    )
    payload = draft.model_dump(mode="json")

    assert payload["op"] == "update_series"
    assert payload["event"]["starts_time"] == "09:00"
    assert payload["before"]["items"] == ["물통"]
    assert UpdateSeriesDraft.model_validate(payload).model_dump(mode="json") == payload


def test_update_series_has_no_event_id():
    payload = UpdateSeriesDraft(
        draft_id="d2",
        op="update_series",
        series_id="series-1",
        event=_series_body(),
        repeat=REPEAT,
        before=None,
        items=[],
    ).model_dump(mode="json")

    assert "event_id" not in payload
    with pytest.raises(ValidationError):
        UpdateSeriesDraft.model_validate({**payload, "event_id": "event-1"})


@pytest.mark.parametrize("clock", ["8:30", "08:3", "0830", "오전 8시", "08:30:00"])
def test_series_time_must_be_clock_text(clock):
    with pytest.raises(ValidationError):
        _series_body(starts_time=clock)


def test_all_day_series_body_has_no_times():
    body = _series_body(all_day=True, starts_time=None, ends_time=None)
    assert body.model_dump(mode="json")["starts_time"] is None


def test_repeat_label_is_required():
    with pytest.raises(ValidationError):
        RepeatPayload.model_validate({"weekdays": [1], "until": "2026-12-20"})
