"""Supervisor 에 넘기는 앞 대화 — 후속 질문일 때만 만들어 이번 문장 앞 user 메시지 하나로 싣는다.

`render()` 가 모델에 가는 글의 정본이다. 자르기 · 개수 상한이 여기 한 곳에 있다.

넘기는 칸
  앞 입력 원문 · 결과마다 Agent · 유형 · 상태 · 추천 이름(`suggestion.content`) · 되물었다는
  표시 · partial 에서 못 낸 Agent

빼는 칸 — DTO 에 칸이 없어서 새어 들어갈 수 없다
  readout 본문(알레르기 확인 · 성장 추이 같은 아이 기록 값, 코드가 쓴 문구는 모델 입력 금지) ·
  되묻기 질문 문장(Health 는 코드가 쓴 readout 이고 등록한 복약 코스 이름이 든다) ·
  저장한 관찰 refs · 안내 · 일정 초안 · 삭제 제안.
  추천 이름은 readout 이 아니라 추천 행에 있다. readout 제목은 대부분 비어 있다.

🚨 글 칸은 `repr=False` 다 — 앞 입력 · 추천 이름이 로그나 예외 문구에 실리지 않게(루트 §2).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta

# 조회 범위. 결과를 읽고 다른 일을 하다 돌아와 묻는 경우까지 — 되묻기 · 다시 시도(15분)보다 길다
RECENT_WINDOW = timedelta(minutes=30)
MAX_RECENT_TURNS = 3
# 이미 보여 준 추천(`DomainTask.shown_before`)을 모으는 턴 수 — 모델에 가는 앞 대화(`render`)는
# 그대로 `MAX_RECENT_TURNS` 다. "다른 거" 를 여러 번 물어도 첫 목록이 다시 나오지 않게 (#844)
SHOWN_TURNS = 5

MAX_TURN_CHARS = 300
MAX_PRIOR_SUGGESTIONS = 3
MAX_SUGGESTION_CHARS = 60

CONTEXT_HEADER = "[앞 대화] 이번 입력을 이해하는 데만 쓴다. 조각은 이번 입력에서만 자른다."
EMPTY_CONTEXT = "[앞 대화] 없음 — 30분 안에 나눈 대화가 없다."
# 조회가 실패했을 때. "없음" 으로 시작해 프롬프트의 같은 규칙(unclear)을 타되, 대화가 없었다고
# 거짓으로 말하지 않는다
LOOKUP_FAILED = "[앞 대화] 없음 — 앞 대화를 읽지 못했다."


@dataclass(frozen=True)
class PriorResult:
    """앞 턴에서 Agent 하나가 낸 결과의 라벨. 본문은 싣지 않는다."""

    agent: str
    task_type: str | None
    status: str
    suggestions: tuple[str, ...] = field(default=(), repr=False)  # 추천 content, 저장 순서
    asked: bool = False  # 되물었다 — 질문 문장은 싣지 않는다


@dataclass(frozen=True)
class PriorTurn:
    text: str = field(repr=False)  # 앞 입력 원문
    results: tuple[PriorResult, ...] = ()
    failed_agents: tuple[str, ...] = ()  # partial 의 failed


@dataclass(frozen=True)
class ConversationContext:
    turns: tuple[PriorTurn, ...] = ()  # 오래된 순
    lookup_failed: bool = False  # 조회가 실패해 빈 맥락으로 간다

    def render(self) -> str:
        """모델에 보낼 글. 턴은 가장 최근 `MAX_RECENT_TURNS` 개, 빈 맥락은 `EMPTY_CONTEXT`."""
        turns = self.turns[-MAX_RECENT_TURNS:]
        if not turns:
            return LOOKUP_FAILED if self.lookup_failed else EMPTY_CONTEXT
        lines = [CONTEXT_HEADER]
        for number, turn in enumerate(turns, start=1):
            lines.append(f"{number}. 보호자: {_clip(turn.text, MAX_TURN_CHARS)}")
            lines.extend(f"   {_result_line(result)}" for result in turn.results)
            if turn.failed_agents:
                lines.append(f"   못 낸 것: {', '.join(turn.failed_agents)}")
        return "\n".join(lines)


# 앞 대화를 만들어 주는 포트. 후속 질문일 때만 pipeline 이 부른다
LoadRecentTurns = Callable[[], Awaitable[ConversationContext]]


def _result_line(result: PriorResult) -> str:
    label = result.agent if result.task_type is None else f"{result.agent}/{result.task_type}"
    line = f"{label} {result.status}"
    if result.suggestions:
        names = " · ".join(
            _clip(name, MAX_SUGGESTION_CHARS) for name in result.suggestions[:MAX_PRIOR_SUGGESTIONS]
        )
        line += f" — 추천: {names}"
    if result.asked:
        line += " — 되물음"
    return line


def _clip(text: str, limit: int) -> str:
    """줄바꿈을 한 칸으로 접고 `limit` 자까지만 둔다. 넘으면 `…` 를 붙인다."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"
