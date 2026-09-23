"""Agent 이벤트 → 화면(SSE) 이벤트 번역 — app/api/runs/translate.py

pipeline 은 진행 상황을 파이썬 객체(`Step` · `Failed` …)로 내보내고, 화면은 정해진 이름
(`step` · `failed` …)의 글자만 알아듣는다. 그 사이를 잇는 표가 여기서 고정된다.
화면이 아는 이름은 프론트 `apps/web/src/lib/api/sse.ts` 를 기준으로 본다
(HTML 계약서는 9/21 부터 갱신 보류, SSE 는 스웨거로 그리기 어렵다).

DB 도 LLM 도 없이 객체만 넣어 본다.
"""

import uuid
from datetime import datetime
from typing import get_args
from zoneinfo import ZoneInfo

import pytest

from app.agents import entrypoint
from app.agents.entrypoint import (
    Done,
    EventDraft,
    EventDrafts,
    Failed,
    FoodRouted,
    Guidance,
    MemoryNote,
    Ref,
    Rerouted,
    Saved,
    Step,
    Unavailable,
    Unwritten,
)

# 초안을 만들 부품이라 진입점이 내보내지 않는다. app/api 코드가 아니라 픽스처를 만드는 테스트라
# 레이어 경계(api 는 진입점만) 밖이다 — 화면으로 가는 모양은 EventDraft.to_payload() 가 정한다.
from app.agents.memory.drafts import DraftItem, EventSnapshot
from app.api.runs import registry, sse, translate

KST = ZoneInfo("Asia/Seoul")
PARENT = uuid.UUID(int=1)

SENT = {Step, Failed, Done, Guidance, EventDrafts}
"""화면으로 보내는 것."""
HELD = {Saved, MemoryNote, Unavailable, FoodRouted, Unwritten, Rerouted}
"""보내지 않는 것. 이유는 translate.py 머리말."""


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _create_draft() -> EventDraft:
    return EventDraft(
        op="create",
        event_id=None,
        title="물놀이",
        starts_at=datetime(2026, 9, 25, 10, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        event_type="episodic",
        category="activity",
        items=(DraftItem(item_id=None, item_name="수영복"),),
    )


def _update_draft() -> EventDraft:
    """바뀌기 전 값(before)까지 딸린 쪽 — 시각이 두 벌이라 JSON 변환이 틀리면 여기서 먼저 터진다."""
    return EventDraft(
        op="update",
        event_id="event-1",
        title="운동회",
        starts_at=datetime(2026, 9, 25, 17, 0, tzinfo=KST),
        ends_at=None,
        all_day=False,
        event_type="episodic",
        category="activity",
        changed=("starts_at",),
        before=EventSnapshot(
            title="운동회",
            starts_at=datetime(2026, 9, 25, 15, 0, tzinfo=KST),
            ends_at=None,
            all_day=False,
            event_type="episodic",
            category="activity",
        ),
    )


def test_every_pipeline_event_is_either_sent_or_held():
    """pipeline 에 이벤트가 새로 생기면 여기서 실패한다 — 화면에 보낼지 정하라는 신호다.

    안 정하면 translate 가 조용히 버린다. 버리는 쪽이 안전한 기본값이지만, 화면에 가야 할 것
    (예: 나중의 `promoted` · `partial`)이 말없이 사라지는 것은 막아야 한다.
    """
    events = set(get_args(entrypoint.Event))

    undecided = sorted(event.__name__ for event in events - SENT - HELD)
    assert not undecided, f"보낼지 정해 주세요 → translate.py · 이 파일의 SENT/HELD: {undecided}"


def test_step_keeps_its_fields():
    assert translate.to_sse(Step(1, 3, "입력을 살펴보고 있어요")) == (
        "step",
        {"index": 1, "total": 3, "label": "입력을 살펴보고 있어요"},
    )


def test_failed_carries_raw_text_back():
    """raw_text 가 있어야 "적어주신 말은 입력창에 그대로 남겨뒀어요" 가 성립한다 (계약서 §06)."""
    assert translate.to_sse(Failed("llm_unavailable", "계란말이 또 찾아요")) == (
        "failed",
        {"reason": "llm_unavailable", "raw_text": "계란말이 또 찾아요"},
    )


def test_done_keeps_its_fields():
    assert translate.to_sse(Done("r1", 3)) == ("done", {"run_id": "r1", "model_calls": 3})


def test_guidance_goes_out_as_guidance():
    """#141 제안 — 알레르기를 말했는데 화면에 아무 반응이 없으면 안 된다 (§2)."""
    guidance = Guidance(
        code="safety_record",
        message="알레르기·건강 정보는 직접 입력해 주세요. 대신 등록해 드릴 수 없어요.",
        deeplink="settings/health-safety",
    )

    assert translate.to_sse(guidance) == (
        "guidance",
        {
            "code": "safety_record",
            "message": "알레르기·건강 정보는 직접 입력해 주세요. 대신 등록해 드릴 수 없어요.",
            "deeplink": "settings/health-safety",
        },
    )


def test_guidance_without_deeplink_sends_null():
    """키를 빼지 않고 null 로 둔다 — 화면이 "키가 없음" 과 "값이 없음" 을 따로 다루지 않게."""
    guidance = Guidance(code="out_of_scope", message="대신 해 드릴 수 없어요.")

    name, payload = translate.to_sse(guidance)

    assert name == "guidance"
    assert payload["deeplink"] is None


def test_event_drafts_use_the_agents_payload_shape():
    """초안 모양은 AI 파트(drafts.py `to_payload`)가 정한다. 여기서 모양을 다시 적지 않는다."""
    create, update = _create_draft(), _update_draft()

    assert translate.to_sse(EventDrafts((create, update))) == (
        "event_draft",
        {"drafts": [create.to_payload(), update.to_payload()]},
    )


@pytest.mark.parametrize(
    "event",
    [
        Step(1, 3, "입력을 살펴보고 있어요"),
        Failed("unparsable", "뭐라고 쓴 건지"),
        Done("r1", 2),
        Guidance(code="diagnosis", message="진단은 도와드릴 수 없어요."),
        EventDrafts((_create_draft(), _update_draft())),
    ],
    ids=lambda event: type(event).__name__,
)
def test_sent_events_become_sse_frames(event):
    """🚨 번역한 내용이 JSON 으로 못 바뀌면 200 을 보낸 뒤 스트림 중간에 연결이 끊긴다.

    화면은 그걸 "결과를 받지 못했어요"(unconfirmed) 로 받는다 — 테스트가 초록이어도 화면에서만
    터지는 종류라 frame() 까지 실제로 통과시켜 본다.
    """
    frame = sse.frame(*translate.to_sse(event))

    assert frame.startswith("event: ")
    assert frame.endswith("\n\n")


@pytest.mark.parametrize(
    "event",
    [
        MemoryNote("기록해 둘게요"),
        Unavailable(("activity",)),
        FoodRouted("meal_idea", "toddler", ("search",), True, "mock"),
        Unwritten(hints=1, tools=0, note=True),
        Rerouted(bounced=1),
        # 화면은 관찰 내용 전체를 원하는데 Saved 는 id 만 준다 — 8단계에서 행을 읽어 채운다
        Saved((Ref("observation_food", "o1"),)),
    ],
    ids=lambda event: type(event).__name__,
)
def test_held_events_are_not_sent(event):
    assert translate.to_sse(event) is None


def test_relay_puts_translated_events_on_the_channel():
    channel = registry.open_run(parent_id=PARENT)
    emit = translate.relay(channel)

    emit(Step(1, 3, "시작"))
    emit(MemoryNote("내부용"))
    emit(Done(channel.run_id, 2))
    emit(Step(2, 3, "끝난 뒤에 온 것"))

    assert [name for name, _ in channel.events] == ["step", "done"]


def test_relay_drops_done_after_failed():
    """🚨 끝 신호는 하나다. pipeline 은 Failed 뒤에 Done 을 보내는데, 화면의 `case "done"` 은
    상태를 성공으로 덮는다 — 그 Done 이 나가면 실패가 "다 됐어요" 가 된다 (#140 리뷰).
    막는 것은 채널이다(registry.publish). 여기서는 pipeline 순서 그대로 넣어서 확인한다.
    """
    channel = registry.open_run(parent_id=PARENT)
    emit = translate.relay(channel)

    emit(Step(1, 3, "시작"))
    emit(Failed("unparsable", "뭐라고 쓴 건지"))
    emit(Done(channel.run_id, 1))

    assert [name for name, _ in channel.events] == ["step", "failed"]


SECRET = "계란말이 또 찾아요"


class _RaisingDraft:
    """초안 모양을 만들다 터진다. 메시지에 원문이 섞인다 — 실제 pydantic 에러처럼."""

    def to_payload(self) -> dict:
        raise ValueError(f"input_value={SECRET!r}")


class _NotJsonDraft:
    """모양은 만들었는데 JSON 으로 못 바뀌는 값이 들어 있다."""

    def to_payload(self) -> dict:
        return {"title": SECRET, "starts_at": object()}


@pytest.mark.parametrize("draft", [_RaisingDraft(), _NotJsonDraft()], ids=["raises", "not_json"])
def test_relay_skips_an_event_it_cannot_translate(draft, caplog):
    """🚨 번역이 터져도 pipeline 을 멈추지 않는다 — 그 이벤트 하나만 빼고 run 은 끝까지 간다.

    emit 은 pipeline 안에서 불린다. 여기서 예외가 올라가면 Memory 가 이미 저장한 뒤에 run 이
    실패로 끝나고, 보호자가 다시 보내면 두 번 저장된다. JSON 으로 못 바뀌는 값을 채널에 넣으면
    200 을 보낸 뒤 스트림 중간에 끊긴다 — 둘 다 여기서 걸러야 한다.
    로그에는 이벤트 종류만 남고 내용(원문)은 남지 않는다 (루트 §2).
    """
    channel = registry.open_run(parent_id=PARENT)
    emit = translate.relay(channel)

    emit(EventDrafts((draft,)))  # 예외가 여기서 올라오면 이 테스트가 실패한다
    emit(Done(channel.run_id, 2))

    assert [name for name, _ in channel.events] == ["done"]
    assert "EventDrafts" in caplog.text
    assert SECRET not in caplog.text
