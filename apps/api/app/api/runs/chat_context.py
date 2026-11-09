"""`chat_turn` 행을 Supervisor 앞 대화(`ConversationContext`)로 바꾼다.

저장된 답에서 라벨만 꺼낸다 — Agent · 유형 · 상태 · 되물었는지 · 못 낸 Agent. 추천 이름은 답이
아니라 추천 행의 `content` 에서 온다(readout 제목은 대부분 비어 있다). readout 본문 · 되묻기 질문
문장 · 저장한 관찰 · 안내 · 일정 초안 · 삭제 제안은 싣지 않는다 — 알레르기 확인 · 성장 추이 ·
복약 코스 이름 같은 값이 모델 입력으로 새지 않게(루트 §2 · `agents/supervisor/context.py`).

모델에 간 적 없을 수 있는 말의 턴은 뺀다(`_sendable`). 후속 질문 때문에 처음으로 외부 LLM 에
가면 안 된다. 답은 턴마다 한 번만 읽는다(`prepare`) — 못 읽는 답의 ERROR 로그도 한 번이다.

못 읽는 답(배포를 되돌린 뒤 옛 코드가 만난 새 종류)은 건너뛰고 턴은 남긴다. 로그에는 내용 없이
턴 id 와 몇 번째인지만 남긴다.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import ValidationError

from app.agents.entrypoint import MAX_RECENT_TURNS, ConversationContext, PriorResult, PriorTurn
from app.api.chat_replies import (
    CHAT_REPLY,
    AgentResultReply,
    ChatReply,
    GuidanceReply,
    PartialReply,
)
from app.domains.chat.models import ChatTurn

log = logging.getLogger(__name__)

# 위험 물질 안내의 code. `agents/supervisor/routing.HAZARD_GUIDANCE.code` 와 같아야 한다
# (테스트가 본다)
_HAZARD_CODE = "hazard"
_HEALTH = "health"


@dataclass(frozen=True)
class PreparedTurns:
    """답을 한 번만 읽어 둔 턴들. 추천 id 를 꺼내고(DB 조회 전) 앞 대화로 바꾼다(조회 뒤)."""

    turns: tuple[tuple[ChatTurn, tuple[ChatReply, ...]], ...]

    def suggestion_ids(self) -> list[uuid.UUID]:
        """보낼 턴이 남긴 추천 id 를 저장 순서대로. 형식이 틀린 id 는 건너뛴다."""
        found: list[uuid.UUID] = []
        for _, replies in self.turns:
            for reply in replies:
                if not isinstance(reply, AgentResultReply):
                    continue
                for raw in reply.suggestion_ids:
                    try:
                        found.append(uuid.UUID(raw))
                    except ValueError:
                        continue
        return list(dict.fromkeys(found))

    def context(self, contents: Mapping[str, str]) -> ConversationContext:
        """앞 대화로. `contents` 는 추천 id(문자열) → 추천 content 다. 최근 세 턴만 남긴다."""
        prior = [_prior(turn, replies, contents) for turn, replies in self.turns]
        return ConversationContext(tuple(prior[-MAX_RECENT_TURNS:]))


def prepare(turns: Sequence[ChatTurn]) -> PreparedTurns:
    """턴(오래된 순)의 답을 한 번 읽고, 모델에 보내면 안 되는 턴을 뺀다."""
    kept: list[tuple[ChatTurn, tuple[ChatReply, ...]]] = []
    for turn in turns:
        replies = tuple(_replies(turn))
        if _sendable(turn, replies):
            kept.append((turn, replies))
    return PreparedTurns(tuple(kept))


def suggestion_ids(turns: Sequence[ChatTurn]) -> list[uuid.UUID]:
    return prepare(turns).suggestion_ids()


def to_context(turns: Sequence[ChatTurn], contents: Mapping[str, str]) -> ConversationContext:
    return prepare(turns).context(contents)


def _sendable(turn: ChatTurn, replies: Sequence[ChatReply]) -> bool:
    """이 턴의 보호자 말이 앞 대화로 모델에 가도 되는가.

    모델을 한 번도 부르지 않았을 수 있는 말은 뺀다 — 위험 물질 안내가 나간 턴(규칙이 막고 안내만
    냈다)과 되묻기에 답한 턴 전부. Health 답은 이어받기가 모델 0회이고 답에 약 이름이 든다. 결과
    모양(응급 안내만 · Health 실패)으로는 Health 답인지 가를 수 없어 `reply_to` 가 있으면 다 뺀다.
    되묻기에 답한 말은 질문의 답이라 후속 질문의 앞 대화로 쓸 일도 적다.
    """
    if turn.reply_to is not None:
        return False
    return not any(
        isinstance(reply, GuidanceReply) and reply.code == _HAZARD_CODE for reply in replies
    )


def _prior(turn: ChatTurn, replies: Sequence[ChatReply], contents: Mapping[str, str]) -> PriorTurn:
    results: list[PriorResult] = []
    failed: tuple[str, ...] = ()
    for reply in replies:
        if isinstance(reply, AgentResultReply):
            results.append(
                PriorResult(
                    agent=reply.agent,
                    task_type=reply.task_type,
                    status=reply.status,
                    suggestions=tuple(
                        _name(contents[sid]) for sid in reply.suggestion_ids if sid in contents
                    ),
                    asked=bool(reply.question),
                )
            )
        elif isinstance(reply, PartialReply):
            failed = tuple(reply.failed)
    return PriorTurn(text=turn.raw_text, results=tuple(results), failed_agents=failed)


def _name(content: str) -> str:
    """추천 이름은 content 의 첫 줄이다 — Growth 교육은 "제목 줄 + 본문" 이고 본문에는 아이 기록에서
    온 말이 든다(`growth/agent.py` 도 첫 줄을 이름으로 쓴다)."""
    return content.split("\n", 1)[0].strip()


def _replies(turn: ChatTurn) -> list[ChatReply]:
    replies: list[ChatReply] = []
    for index, item in enumerate(turn.replies):
        try:
            replies.append(CHAT_REPLY.validate_python(item))
        except ValidationError:
            log.error("chat_turn %s 의 답 %d 번째를 읽지 못해 건너뛴다", turn.id, index)
    return replies
