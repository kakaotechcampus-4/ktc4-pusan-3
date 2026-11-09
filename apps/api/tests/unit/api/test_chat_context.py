"""`chat_turn` 행 → Supervisor 앞 대화 변환 (app/api/runs/chat_context.py).

DB 없이 행을 메모리에서 만든다. 문장은 전부 지어낸 것이다.
추천 이름은 readout 이 아니라 추천 행의 content 에서 온다 — readout 본문은 어디로도 새지 않는다.
"""

import uuid

from app.agents.supervisor.context import PriorResult
from app.api.chat_replies import (
    CHAT_REPLIES,
    AgentResultReply,
    DoneReply,
    GuidanceReply,
    NoteReply,
    PartialReply,
    SavedReply,
    StoredReadout,
    StoredRef,
)
from app.api.runs.chat_context import suggestion_ids, to_context
from app.domains.chat.models import ChatTurn

SID = str(uuid.uuid4())


def chat_turn(text: str, replies: list, *, reply_to: str | None = None) -> ChatTurn:
    return ChatTurn(
        id=uuid.uuid4(),
        raw_text=text,
        reply_to=reply_to,
        replies=CHAT_REPLIES.dump_python(replies, mode="json"),
    )


def result(**changes) -> AgentResultReply:
    values = dict(
        agent="activity",
        task_type=None,
        status="completed",
        readouts=[],
        question=None,
        suggestion_ids=[],
        kind="general",
        reason=None,
        record_count=0,
    )
    return AgentResultReply(**{**values, **changes})


def test_추천은_content_로_readout_은_싣지_않는다() -> None:
    caution = StoredReadout(
        kind="caution", title="", body="땅콩 확인 문구", authored_by="code", code=None
    )
    turn = chat_turn(
        "실내 놀이 추천해줘",
        [
            result(readouts=[caution], suggestion_ids=[SID]),
            NoteReply(text="기록했어요.", kind="message"),
            SavedReply(refs=[StoredRef(kind="observation_food", id=str(uuid.uuid4()))]),
            DoneReply(completed=True),
        ],
    )

    context = to_context([turn], {SID: "블록 쌓기"})

    (prior,) = context.turns
    assert prior.text == "실내 놀이 추천해줘"
    assert prior.results == (PriorResult("activity", None, "completed", ("블록 쌓기",)),)
    assert "땅콩" not in context.render()
    assert "기록했어요" not in context.render()


def test_partial_의_실패_Agent_와_되묻기_표시를_싣는다() -> None:
    turn = chat_turn(
        "간식이랑 놀이 추천해줘",
        [
            result(agent="growth", task_type="routine_coaching", question="몇 번 해 봤어요?"),
            PartialReply(
                reason="timeout_20s",
                succeeded=["growth"],
                failed=["activity"],
                retryable=["activity"],
            ),
            DoneReply(completed=True),
        ],
    )

    (prior,) = to_context([turn], {}).turns

    assert prior.failed_agents == ("activity",)
    assert prior.results == (
        PriorResult("growth", "routine_coaching", "completed", (), asked=True),
    )


def test_코드가_쓴_질문_문장은_모델_입력에_싣지_않는다() -> None:
    """Health 의 되묻기는 코드 readout 이고 복약 코스 이름이 든다 — 물었다는 표시만 싣는다."""
    turn = chat_turn(
        "열이 나서 약 먹였어",
        [
            result(
                agent="health",
                task_type="medication",
                question="어떤 약을 말씀하시는 건가요? 등록한 약: 타이레놀 시럽",
            ),
            DoneReply(completed=True),
        ],
    )

    context = to_context([turn], {})

    (prior,) = context.turns
    assert prior.results[0].asked is True
    assert "타이레놀" not in context.render() and "어떤 약" not in context.render()


def test_위험_물질_안내가_나간_턴은_뺀다() -> None:
    """규칙이 막아 모델을 한 번도 부르지 않은 말이다 — 후속 질문 때문에 처음 모델에 가면 안 된다."""
    hazard = chat_turn(
        "아이가 세제를 마셨어",
        [
            GuidanceReply(code="hazard", message="지금 바로 119에 전화해 주세요.", deeplink=None),
            result(suggestion_ids=[SID]),
            DoneReply(completed=True),
        ],
    )
    normal = chat_turn("실내 놀이 추천해줘", [result(), DoneReply(completed=True)])

    context = to_context([hazard, normal], {SID: "블록 쌓기"})

    assert [turn.text for turn in context.turns] == ["실내 놀이 추천해줘"]
    assert suggestion_ids([hazard, normal]) == []


def test_Health_되묻기에_답한_턴은_뺀다() -> None:
    """Health 이어받기는 모델 0회다. 답에는 약 이름이 들어 있다."""
    answer = chat_turn(
        "타이레놀이요",
        [result(agent="health", task_type="medication"), DoneReply(completed=True)],
        reply_to="run-asked",
    )
    other = chat_turn("실내 놀이 추천해줘", [result(), DoneReply(completed=True)])

    assert [t.text for t in to_context([answer, other], {}).turns] == ["실내 놀이 추천해줘"]


def test_Health_결과여도_새_입력이면_싣는다() -> None:
    """병원 찾기 같은 새 입력은 Supervisor 가 읽은 말이다 — "다른 데는 없어?" 의 앞 대화다."""
    clinic = chat_turn(
        "지금 문 연 소아과 찾아줘",
        [result(agent="health", task_type="place_lookup"), DoneReply(completed=True)],
    )

    (prior,) = to_context([clinic], {}).turns

    assert prior.results[0].agent == "health"


def test_content_를_못_찾은_추천은_뺀다() -> None:
    other = str(uuid.uuid4())
    turn = chat_turn("실내 놀이 추천해줘", [result(suggestion_ids=[SID, other])])

    (prior,) = to_context([turn], {SID: "블록 쌓기"}).turns

    assert prior.results[0].suggestions == ("블록 쌓기",)


def test_못_읽는_답은_건너뛰고_턴은_남긴다() -> None:
    turn = chat_turn("실내 놀이 추천해줘", [result(suggestion_ids=[SID])])
    turn.replies = [{"type": "unknown_future_reply"}, *turn.replies]

    (prior,) = to_context([turn], {SID: "블록 쌓기"}).turns

    assert prior.text == "실내 놀이 추천해줘"
    assert prior.results[0].suggestions == ("블록 쌓기",)


def test_suggestion_ids_는_잘못된_id_를_건너뛰고_순서를_지킨다() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    turns = [
        chat_turn("가", [result(suggestion_ids=[str(first), "not-a-uuid"])]),
        chat_turn("나", [result(suggestion_ids=[str(second)])]),
    ]

    assert suggestion_ids(turns) == [first, second]


def test_위험_물질_안내_code_는_routing_의_것과_같다() -> None:
    from app.agents.supervisor.routing import HAZARD_GUIDANCE
    from app.api.runs.chat_context import _HAZARD_CODE

    assert _HAZARD_CODE == HAZARD_GUIDANCE.code


def test_되묻기에_답한_턴은_결과_모양과_상관없이_뺀다() -> None:
    """Health 답이 응급 안내만 남기거나 Health 가 실패해도 답(약 이름)은 모델에 간 적이 없다."""
    emergency = chat_turn(
        "타이레놀이요 숨을 잘 못 쉬어요",
        [
            GuidanceReply(code="emergency", message="지금 바로 119에 연락하세요.", deeplink=None),
            DoneReply(completed=True),
        ],
        reply_to="run-asked",
    )
    failed = chat_turn(
        "시럽이요",
        [
            PartialReply(reason="agent_error", succeeded=[], failed=["health"], retryable=[]),
            DoneReply(completed=True),
        ],
        reply_to="run-asked-2",
    )

    assert to_context([emergency, failed], {}).turns == ()


def test_추천_이름은_content_의_첫_줄만_싣는다() -> None:
    """Growth 교육 추천은 "제목 줄 + 본문" 이다 — 본문에는 아이 기록에서 온 말이 든다."""
    turn = chat_turn("한글 놀이 추천해줘", [result(agent="growth", suggestion_ids=[SID])])

    context = to_context([turn], {SID: "한글 블록 놀이\n지난주 기록한 낱말 놀이를 좋아해서"})

    assert context.turns[0].results[0].suggestions == ("한글 블록 놀이",)
    assert "지난주" not in context.render()


def test_빠진_턴을_빼고_최근_다섯_턴을_남기고_모델에는_세_턴만_보낸다() -> None:
    """ "다른 거" 를 여러 번 물어도 앞에서 보여 준 추천을 다시 내지 않게 다섯 턴을 든다 — Supervisor
    모델에 가는 글은 지금처럼 최근 세 턴이다 (#852)."""
    usable = [chat_turn(f"놀이 {i} 추천해줘", [result()]) for i in range(6)]
    hazard = chat_turn(
        "세제를 마셨어", [GuidanceReply(code="hazard", message="119", deeplink=None)]
    )

    context = to_context([*usable, hazard], {})

    assert [turn.text for turn in context.turns] == [f"놀이 {i} 추천해줘" for i in range(1, 6)]
    rendered = context.render()
    assert "놀이 3 추천해줘" in rendered and "놀이 2 추천해줘" not in rendered


def test_못_읽는_답은_한_번만_로그를_남긴다(caplog) -> None:
    import logging

    from app.api.runs.chat_context import prepare

    caplog.set_level(logging.ERROR)
    turn = chat_turn("실내 놀이 추천해줘", [result(suggestion_ids=[SID])])
    turn.replies = [{"type": "unknown_future_reply"}, *turn.replies]

    prepared = prepare([turn])
    prepared.suggestion_ids()
    prepared.context({SID: "블록 쌓기"})

    assert caplog.text.count("읽지 못해") == 1
