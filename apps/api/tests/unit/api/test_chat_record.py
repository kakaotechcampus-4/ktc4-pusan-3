"""run 이벤트 → 대화 기록 모양 — app/api/runs/chat_record.py 의 `to_reply` (#228).

DB 도 LLM 도 없이 객체만 넣어 본다. 저장 · 날짜 · 연결 확인은 tests/integration/test_chat_record.py.
"""

import uuid
from datetime import datetime
from typing import get_args
from zoneinfo import ZoneInfo

import pytest

from app.agents import entrypoint

# readout · 초안을 만들 부품이라 진입점이 내보내지 않는다. 픽스처를 만드는 테스트라 레이어 경계
# (api 는 진입점만) 밖이다 — test_run_translate.py 와 같다.
from app.agents.common.readout import Readout
from app.agents.common.refs import Ref as EvidenceRef
from app.agents.entrypoint import (
    AgentQuestion,
    AgentResult,
    DomainRouted,
    Done,
    EmptyResult,
    EventDraft,
    EventDrafts,
    Failed,
    Guidance,
    MemoryNote,
    Partial,
    PendingReply,
    Ref,
    Rerouted,
    RetryContext,
    Saved,
    Step,
    Unavailable,
    Unwritten,
)
from app.agents.memory.drafts import DraftItem
from app.api.chat_replies import CHAT_REPLIES
from app.api.runs.chat_record import to_reply

KST = ZoneInfo("Asia/Seoul")

RECORDED = {
    Guidance,
    EventDrafts,
    MemoryNote,
    Unavailable,
    Partial,
    AgentResult,
    AgentQuestion,
    Saved,
}
"""대화에 남기는 것. 끝 상태(`done`)는 이벤트가 아니라 채널에서 읽어 따로 붙인다."""
SKIPPED = {
    Step,  # 진행 중에만 의미가 있다
    Done,  # 끝 상태는 채널에 실제로 나간 신호에서 읽는다 — 러너가 직접 넣는 done 도 있다
    Failed,  # 실패한 run 은 남기지 않는다
    PendingReply,  # 되묻기 조각 원문
    RetryContext,  # 05 보관함 맥락 · 요청 원문 조각
    EmptyResult,
    DomainRouted,  # 로그 · 지표용
    Unwritten,
    Rerouted,
}


def test_every_pipeline_event_is_either_recorded_or_skipped():
    """pipeline 에 이벤트가 새로 생기면 여기서 실패한다 — 대화에 남길지 정하라는 신호다.

    안 정하면 `to_reply` 가 조용히 버려서, 화면에는 보였는데 지난 대화에서는 빠진다.
    """
    undecided = sorted(e.__name__ for e in set(get_args(entrypoint.Event)) - RECORDED - SKIPPED)
    assert not undecided, f"남길지 정해 주세요 → chat_record.py · 이 파일: {undecided}"


@pytest.mark.parametrize(
    "event",
    [
        MemoryNote("언제부터였어요?", "question"),
        Guidance(code="safety_record", message="알레르기는 직접 입력해 주세요."),
        Unavailable(("growth",)),
        Partial("timeout_20s", ("activity",), ("food",), ("food",)),
        Saved((Ref("observation_food", str(uuid.UUID(int=3))),)),
        EventDrafts(
            (
                EventDraft(
                    op="create",
                    event_id=None,
                    title="물놀이",
                    starts_at=datetime(2026, 9, 25, 10, 0, tzinfo=KST),
                    ends_at=None,
                    all_day=False,
                    event_type="episodic",
                    category="activity",
                    items=(DraftItem(item_id=None, item_name="수영복"),),
                ),
            )
        ),
        AgentResult(
            agent="growth",
            task_type="growth_review",
            status="completed",
            readouts=(
                Readout(
                    kind="growth_delta",
                    body="3월 2일부터 6월 2일까지 3개월간 키 2.1cm 늘었어요.",
                    authored_by="code",
                    source_refs=(EvidenceRef(kind="child_growth_log", id=uuid.UUID(int=7)),),
                ),
            ),
            suggestion_ids=("s1",),
            kind="personalized",
            reason="기록 2건을 근거로 골랐어요.",
        ),
        AgentQuestion(
            "growth",
            "routine_coaching",
            "양치는 요즘 어느 정도까지 혼자 하나요?",
            awaits_reply=True,
        ),
    ],
    ids=lambda event: type(event).__name__,
)
def test_recorded_events_survive_the_jsonb_round_trip(event):
    """jsonb 로 쓰고 다시 읽어도 같은 모양이다.

    못 읽는 행이 쌓이면 지난 대화가 통째로 안 열린다.
    """
    reply = to_reply(event)

    stored = CHAT_REPLIES.dump_python([reply], mode="json")

    assert CHAT_REPLIES.validate_python(stored) == [reply]


def test_readouts_keep_the_sentence_and_drop_source_refs():
    """readout 은 기록 값이 들어 있어도 문장 그대로다 — 그날 오간 말은 고치지 않는다 (#228 10-16).

    근거(`source_refs`)는 담지 않는다. 다시 그리는 길이 생기면 그때 칸을 늘린다.
    """
    readout = Readout(
        kind="notice",
        body="등록된 알레르기(꽃가루)가 있어요. 장소랑 재료를 한 번 확인해 주세요.",
        authored_by="code",
        source_refs=(EvidenceRef(kind="child_growth_log", id=uuid.UUID(int=7)),),
    )
    event = AgentResult(agent="activity", task_type=None, status="completed", readouts=(readout,))

    [stored] = CHAT_REPLIES.dump_python([to_reply(event)], mode="json")[0]["readouts"]

    assert stored == {
        "kind": "notice",
        "title": "",
        "body": readout.body,
        "authored_by": "code",
        "code": None,
    }


def test_질문은_agent_result_답으로_남고_답할_수_있는지도_남는다():
    """지난 대화가 이 한 칸으로 답하기를 다시 연다 (`askedQuestion` · reply_open). 저장하지 않으면
    앱을 다시 켠 뒤 열린 질문을 못 찾는다 (#537 · #615)."""
    event = AgentQuestion(
        "growth", "routine_coaching", "양치는 요즘 어느 정도까지?", awaits_reply=True
    )

    stored = CHAT_REPLIES.dump_python([to_reply(event)], mode="json")[0]

    assert stored == {
        "type": "agent_result",
        "agent": "growth",
        "task_type": "routine_coaching",
        "status": "completed",
        "readouts": [],
        "question": "양치는 요즘 어느 정도까지?",
        "awaits_reply": True,
        "suggestion_ids": [],
        "kind": None,
        "reason": None,
        "record_count": None,
    }


def test_카드는_질문을_남기지_않는다():
    event = AgentResult(
        agent="growth", task_type="routine_coaching", status="completed", readouts=()
    )

    stored = CHAT_REPLIES.dump_python([to_reply(event)], mode="json")[0]

    assert (stored["question"], stored["awaits_reply"]) == (None, False)


def test_옛_행은_awaits_reply_없이_읽는다():
    """배포 전에 쌓인 행에는 이 칸이 없다. 못 읽으면 그날 대화가 통째로 안 열린다."""
    old_row = {
        "type": "agent_result",
        "agent": "health",
        "task_type": "medication",
        "status": "completed",
        "readouts": [],
        "question": "복약 알림을 멈출까요?",
        "suggestion_ids": [],
        "kind": None,
        "reason": None,
        "record_count": None,
    }

    [reply] = CHAT_REPLIES.validate_python([old_row])

    assert (reply.question, reply.awaits_reply) == ("복약 알림을 멈출까요?", False)
