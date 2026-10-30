"""Memory Agent 진입점. 이 디렉토리에서 밖으로 열려 있는 유일한 함수.

raw_text 하나를 받아 tool calling을 끝까지 돌리고 결과를 돌려준다.
app/api와 이후의 Supervisor는 내부(registry·prompt·client)를 모른 채 run()만 부른다.

루프가 지는 책임은 세 가지다.
  - 모델이 낸 arguments가 깨져도 루프를 죽이지 않는다
  - 같은 인자로 성공한 쓰기 작업을 두 번 실행하지 않는다
  - 끝나지 않는 대화를 끊고, 끝났는지 여부를 호출자에게 알린다

task 를 넘기면(Supervisor 경로) 다섯 가지가 더 붙는다.
  - [tool 묶음] 기록 기본 묶음은 항상, 수정·삭제는 MemoryTask.open_lookup_edit 일 때만
  - [허용 목록] 모델에게 안 보여준 tool은 이름으로 불러도 실행하지 않는다
  - [조기 종료] 할 일을 다 한 게 코드로 확인되면 요약 호출 없이 끝낸다 (_covered)
  - [unclear] Supervisor 가 unclear 로 본 조각은 관찰로 저장하지 않는다 (_in_unclear)
  - [고쳐 말하기] lookup_edit 조각은 조회 결과를 본 뒤에만 새로 저장한다 (_skipped_lookup)
task 가 없어도 위험 물질 이름이 대상인 식사 · 놀이 · 배움 · 생활 관찰은 저장하지 않는다 (_hazard).
task 없이 부르면 전체 tool 을 열고 끊지 않는다 — Supervisor 없이 Memory 만 재는 경로다.
"""

import json
import logging
import re
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from app.agents.common.llm_client import LLMClient
from app.agents.memory.bundles import MUTATING_PREFIXES, WRITES_FOR, tools_for
from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft
from app.agents.memory.prompt import build_system_prompt
from app.agents.memory.registry import TOOL_SPECS, execute_tool, specs_for
from app.agents.memory.result import ErrorCode, fail
from app.agents.memory.schemas.reply import (
    CONTINUATION_REPLY_FORMAT,
    REPLY_FORMAT,
    ContinuationReplyOutput,
    ReplyKind,
    ReplyOutput,
)
from app.agents.memory.schemas.task import (
    FoundRef,
    MemoryHint,
    MemoryTask,
    PendingMemoryContext,
    WorkType,
)
from app.rules.hazard import FOOD_NAME_TERMS, PLAY_NAME_TERMS, hazard_names
from app.rules.term_match import Term

logger = logging.getLogger(__name__)

# 복합 발화 대비. 세는 것은 **LLM 왕복 수**이지 tool 건수가 아니다 —
# 한 왕복에 tool 이 여러 개 와도 그 바퀴에서 전부 실행하고 step 은 1만 올라간다.
MAX_STEPS = 7
MAX_COMPLETION_TOKENS = 1400

# 재질문 상한. 한 조각에 처음 질문까지 포함해 이만큼 물었으면 이어받기 run 이 또 물어도 되묻기
# 맥락을 만들지 않고 끝낸다(`ask_limit_reached`).
MAX_QUESTIONS = 4

# 되묻기 맥락에 남기는 조회 결과 행 수의 상한. 넘으면 하나도 남기지 않는다
MAX_FOUND = 40

# 조기 종료: 모든 힌트에 대해 성공한 쓰기가 run 전체에서 그 수 이상이면 요약 호출 없이 끝냄
EARLY_STOP = True

# 이 말이 든 힌트가 있으면 조기 종료하지 않음
# 알림 요청은 쓰기 없이 안내로 끝나야 함
_NEEDS_REPLY_CUES = ("알림", "알람")

# 이 tool을 부른 run은 조기 종료하지 않음(후속 변경이 있을 수 있음)
_OPEN_ENDED = frozenset({"create_event_item", "update_event_item", "delete_event_item"})

EndedBy = Literal["model", "coverage", "max_steps"]

_HINT_HEADER = "[먼저 나눠 본 기록 후보 — 빠진 게 있을 수 있다. 발화 전체에서 기록할 것을 찾는다]"

_CONTINUATION_HEADER = """[이어서 처리할 것]
아래 조각만 보호자의 새 답으로 보완한다. 이전에 이미 저장한 다른 기록은 다시 저장하지 않는다.
새 답만 따로 관찰로 저장하지 않는다. 여전히 필요한 값이 없으면 질문 하나만 다시 한다.
답에 조각과 상관없는 기록이나 요청이 섞여 있으면 그 부분은 저장하지도 답하지도 않고
leftover를 true로 둔다. 따로 보내 달라는 안내는 따로 붙으니 text 에 쓰지 않는다.
답이 조각과 상관없는 말뿐이면 직전에 물은 것을 다시 묻는다. text 를 비우지 않는다."""

# 강등 경로의 조각은 나누지 못한 원문이다. 코드가 앞 run 에서 아무것도 쓰지 않았을 때만
# 이 맥락을 남기므로 "하나도 저장하지 않았다" 는 사실이다.
# 저장할 범위를 "이 글에 적힌 기록" 으로 묶는다.
_WHOLE_FRAGMENT = (
    "(나누지 못한 원문 전체다. 이 글에 적힌 기록은 아직 하나도 저장하지 않았다. "
    "물은 것과 상관없는 것도 이 글에 적힌 기록이면 하나씩 모두 저장한다. "
    "보호자의 답에만 있고 이 글에 없는 기록은 섞인 말이다)"
)

# unclear 조각만 있던 입력에서 물은 뒤의 이어받기. 원문을 못 나눈 강등 경로(_WHOLE_FRAGMENT)와
# 달리 "아니" 가 답일 수 있는 질문이다. _WHOLE_FRAGMENT 를 붙이면 아니라고 해도 같은 걸 다시 물었다
_UNCLEAR_FRAGMENT = (
    "(아이가 한 일인지 알 수 없어 물었던 문장이다. 보호자가 아이 얘기라고 답했을 때만 이 글에 적힌 "
    "기록을 저장한다. 보호자 자신의 얘기라거나 아니라고 답하면 저장하지 않고, 아이 기록이 아니라서 "
    "남기지 않았다고 한 줄로 알리고 끝낸다. 이때는 다시 묻지 않는다)"
)

# Supervisor 가 unclear 로 본 조각의 관찰 저장. tool 을 빼면 모델이 이유를 몰라 저장 없이
# "기록했어요" 라고 답했다. 그래서 열어 둔 채 거절하고 다음 행동을 적는다.
# 묻지 않는다. "아이가 그랬나요?" 로 물으면 물을지가 반반으로 갈렸고, "아니 내가 먹은 거야" 라는
# 답에도 저장한 적이 있다(6번 중 1번). 아이 얘기면 아이를 주어로 다시 말하면 record 로 간다
_NOT_CHILD_RECORD_ALONE = (
    "이 발화는 아이가 한 일이나 보인 반응인지 원문에서 드러나지 않아 관찰로 저장하지도 고치지도 "
    "않는다. 다시 부르지 않는다. 묻지 않고, 아이 기록인지 알 수 없어 저장하지 않았다고 한 줄로 "
    "알린다. 아이 얘기였다면 아이를 주어로 다시 말해 달라고 짧게 덧붙인다. 보호자 얘기를 "
    "'아이도 ~?' 처럼 아이에게 옮겨 묻지 않는다."
)
_NOT_CHILD_RECORD_MIXED = (
    "이 문장은 아이가 한 일이나 보인 반응인지 원문에서 드러나지 않아 관찰로 저장하지 않는다. "
    "다시 부르지 않는다. 발화의 다른 기록은 그대로 저장한다. 이 문장은 묻지 않고, "
    "아이 기록인지 알 수 없어 남기지 않았다고 한 줄로만 알린다."
)

# 위험 물질이 대상인 식사 · 놀이 · 배움 · 생활 관찰 (#626).
# 사전검사 · Supervisor 를 지나온 것의 마지막 자리다.
# 저장되면 Curator 가 취향 후보를 만들어 추천 근거가 된다. 안내는 pipeline 이 따로 낸다
_HAZARD = (
    "음식이 아닌 위험한 것이라 관찰로 저장하지 않는다. 다시 부르지 않는다. "
    "다른 이름 · 다른 종류의 관찰로 바꿔 저장하지 않는다. 기록하지 않았다고 한 줄로만 알린다."
)

# 고쳐 말하는 조각(lookup_edit)인데 조회 없이 새로 저장. "할머니 말고 할아버지랑 산책했어" 처럼
# 바뀌는 값이 사람·장소면 프롬프트 규칙이 있어도 3번 다 새 행을 만들었다
_LOOKUP_FIRST = (
    "이 문장은 저장된 기록을 고치는 말이다. 새로 저장하기 전에 query_observation_* 로 "
    "바뀌기 전 값이 든 기록을 먼저 찾고, 그 결과를 본 뒤에 [고쳐 말할 때] 규칙대로 고치거나 "
    "저장한다. 찾기와 저장을 한 응답에 같이 부르지 않는다."
)

# unclear 조각만 있던 입력을 되묻기로 끝낸 턴. "나 딸기 좋아해" 에 tool 없이 바로
# "아이의 음식 취향으로 기록할까요?" 라고 물어 거절 메시지의 안내가 닿지 않았다(3번 중 2번).
# 프롬프트에 더해도 그대로라 이 턴만 코드가 집어 run 당 한 번 다시 확인한다.
# 그래도 물으면 되묻기 맥락(unclear=True)이 남고, 이어받기는 _UNCLEAR_FRAGMENT 를 받는다
_UNCLEAR_QUESTION = (
    "보호자에게 보이지 않는 확인이다. 여기에 답하지 말고 보호자에게 보낼 말만 다시 쓴다. "
    "이 발화는 아이 기록인지 알 수 없다고 판정됐다. 묻지 않고, 아이 기록인지 알 수 없어 "
    "저장하지 않았다고 한 줄로 알린다(kind=message). 아이 얘기였다면 아이를 주어로 다시 말해 "
    "달라고 짧게 덧붙인다."
)

# 이어받기 때 앞서 조회로 찾은 행을 다시 읽어 보여 주는 칸의 머리
_FOUND_HEADER = (
    "[앞서 조회해서 찾은 기록 — 보호자가 이 기록을 말한 것이면 다시 조회하지 않고 이 id 를 쓴다]"
)

_BAD_JSON = "arguments가 올바른 JSON이 아니다. 스키마에 맞는 JSON으로 다시 만든다."

# 모델이 tool도 안 부르고 답도 비운 턴을 내면 사용자는 아무것도 못 봄.
# run 당 한 번만 다시 묻게 한다
_EMPTY_TURN = (
    "답이 비어 있다. 한 문장으로 답한다 — 무엇을 했는지, 저장하지 않았으면 무엇을 물어봐야 하는지."
)
# 되묻는데 이어 붙일 조각을 잘못 지목했을 때 run 당 한 번만 다시 고르게 한다
_PENDING_RETRY = (
    "pending_hint 가 아직 저장하지 않은 기록 후보가 아니다. 후보 중에서 그대로 하나를 골라 "
    "다시 답한다."
)
# 이어받기에서 저장에 성공한 것 없이 말로 끝낸 턴. 아무것도 저장하지 않았는데 "기록할게요" 처럼
# 저장한 것으로 들리게 끝낸 적이 있다(#246 라이브). 말하는 턴에서 run 이 끝나 그 말은 지켜지지
# 않는다. 프롬프트에 규칙을 더하면 tool 을 부르는 다른 이어받기(원문 전체 조각 저장)가 흔들려서,
# 이 턴만 코드가 집어 run 당 한 번 다시 확인하게 한다
_UNSAVED_REPLY = (
    "보호자에게 보이지 않는 확인이다. 여기에 답하지 말고 보호자에게 보낼 말만 다시 쓴다. "
    "이번에는 아무것도 저장하지 않았고, 이 말을 보내면 처리가 끝나 나중에 기록할 수도 없다. "
    "저장했다거나 기록하겠다는 말은 넣지 않는다. 필요한 값이 없으면 그 값을 묻는다."
)
# 처음 run 도 저장 없이 "기록했어요" 로 끝낸 적이 있다 — 저장할 곳이 없는 채팅의
# 키 · 몸무게에 (#625 멘토 리뷰). 처음 run 은 알림 안내 · 조회 답처럼 저장 없이 말로 끝나는 게
# 정상이라 이어받기처럼 전부 다시 확인하지 않고, 저장했다 · 하겠다는 말로 끝났을 때만 확인한다.
# 조회 · 수정 조각만 맡은 run 은 보지 않는다 (_lookup_only)
_SAVED_CLAIM = re.compile(
    r"(기록|저장)\s?(했어|했습|할게|하겠|해\s?(뒀|두었|놨|놓았|둘게|놓을게|드렸|드릴게))"
)
# 다시 확인해도 저장한 척하면 그 말 대신 내보낸다. 지켜지지 않을 말을 보호자에게 보내지 않는다
_UNSAVED_FALLBACK = "이번에는 아무것도 저장하지 않았어요."
_ALREADY_DONE = (
    "같은 작업을 이미 했다. 다시 부르지 않는다. "
    "같은 날 같은 대상은 한 건으로 합치고, 내용을 더할 거면 update로 고친다."
)


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]  # ToolResult.to_payload()

    @property
    def success(self) -> bool:
        return bool(self.result.get("success"))


@dataclass(frozen=True)
class MemoryReply:
    """보호자에게 나가는 문장 하나. text 와 kind 는 항상 같이 움직인다."""

    text: str
    kind: ReplyKind


@dataclass
class MemoryAgentResult:
    reply: MemoryReply | None  # 끝내지 못했거나 조기 종료했으면 None
    completed: bool  # MAX_STEPS 안에 마쳤는지 여부
    steps: int  # LLM 왕복 수. tool 건수가 아니다 — 그건 len(calls)
    calls: list[ToolCallRecord] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    # model: 모델이 tool 없이 말하고 끝남. 되묻기·조회 답·알림 안내가 전부 여기라
    #   질문 여부를 이 값으로 가르지 않는다 — reply.kind 를 본다
    # coverage: 할 일을 다 한 게 확인돼 요약 호출 없이 끝남
    # max_steps: 끝내지 못함
    ended_by: EndedBy = "model"
    drafts: tuple[EventDraft, ...] = ()  # 보호자 제출을 기다리는 일정 초안
    pending: PendingMemoryContext | None = None  # 되묻고 멈춘 조각. kind=question 일 때만
    leftover: bool = False  # 이어받기에서 답에 섞인 다른 기록·요청을 처리하지 않고 남겼는지.
    # 재질문 상한에 닿아 또 물으려던 질문을 맥락 없이 끝냈는지.
    # pipeline 이 질문 대신 상수 문구를 낸다
    ask_limit_reached: bool = False

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.calls]

    @property
    def final_message(self) -> str | None:
        """이전 이름. pipeline 로그와 eval 이 이걸로 본다."""
        return self.reply.text if self.reply is not None else None

    @property
    def persisted(self) -> bool:
        """DB 에 실제로 쓴 호출이 하나라도 있는가. 일정 초안만 만든 run 은 False."""
        return any(persisted_write(call) for call in self.calls)

    def refused(self, code: str) -> bool:
        """이 코드로 거절된 호출이 있는가. pipeline 이 저장 게이트 거절(`HAZARD`)을 본다."""
        return _refused(self.calls, code)


async def run(
    raw_text: str,
    context: AgentContext,
    *,
    client: LLMClient | None = None,
    max_steps: int = MAX_STEPS,
    task: MemoryTask | None = None,
    continuation: PendingMemoryContext | None = None,
) -> MemoryAgentResult:
    """발화 한 건을 처리한다.

    task는 Supervisor 경로로, raw_text는 task.raw_text와 같아야 한다.
    continuation은 이전 run의 되묻기를 이어받는 경로다. raw_text는 보호자의 새 답이고,
    task는 미완료 조각으로 코드가 만든다.
    """
    if continuation is not None:
        if task is not None:
            raise ValueError("continuation 과 task 를 같이 넘기지 않는다")
        task = MemoryTask(
            raw_text=continuation.hint_text,
            hints=(MemoryHint(text=continuation.hint_text, work=continuation.work),),
        )
    elif task is not None and task.raw_text != raw_text:
        raise ValueError("task.raw_text와 raw_text가 다르다")

    shown = await _reread(continuation.found, context) if continuation is not None else []
    llm = client or LLMClient(role="memory")
    allowed = tools_for(task) if task is not None else None
    tools = specs_for(allowed) if allowed is not None else TOOL_SPECS
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context)},
        {
            "role": "user",
            "content": (
                _continuation_message(raw_text, continuation, shown)
                if continuation is not None
                else _user_message(raw_text, task)
            ),
        },
    ]
    calls: list[ToolCallRecord] = []
    usage: dict[str, int] = {}
    succeeded: set[tuple[str, str]] = set()
    nudged = False  # 빈 턴을 다시 물은 적이 있는지
    nudged_hint = False  # 조각 지목을 다시 물은 적이 있는지
    rechecked = False  # 저장 없이 말로 끝낸 이어받기를 다시 확인하게 한 적이 있는지
    asked_unclear = False  # unclear 만 있는 입력의 되묻기를 다시 확인하게 한 적이 있는지
    # 이어받기는 답에 다른 말이 섞였는지(leftover)를 더 받는다
    reply_format = CONTINUATION_REPLY_FORMAT if continuation is not None else REPLY_FORMAT
    reply_model: type[ReplyOutput] = (
        ContinuationReplyOutput if continuation is not None else ReplyOutput
    )

    for step in range(max_steps):
        response = await llm.chat(
            messages=messages,
            tools=tools,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            # 말하는 턴의 content를 스키마 JSON 으로 받는다. tool을 부르는 턴에는 쓰이지 않는다
            response_format=reply_format,
        )
        _accumulate(usage, response.usage)

        tool_calls = getattr(response.message, "tool_calls", None) or []
        if not tool_calls:
            content = (response.message.content or "").strip()
            parsed = _parse_reply(content, reply_model)
            # structured output 이라 빈 턴은 text 가 빈 JSON 으로도 온다 (라이브 RC26)
            spoken = parsed.text.strip() if parsed is not None else content
            if not spoken and not nudged:
                # 빈 턴. 응답 문구는 화면이 지어내지 않으므로 모델에게 한 번 더 묻는다
                nudged = True
                messages.append({"role": "user", "content": _EMPTY_TURN})
                continue
            if parsed is not None:
                replied = MemoryReply(text=parsed.text, kind=parsed.kind)
                # 재질문 상한은 여기서 한 번만 판정한다. 상한이면 맥락을 만들지 않고 표시만 싣는다
                at_limit = _at_ask_limit(replied, continuation, calls)
                pending = (
                    None
                    if at_limit
                    else _pending(replied, parsed.pending_hint, task, continuation, raw_text, calls)
                )
                misnamed = (
                    replied.kind == "question"
                    and pending is None
                    and continuation is None
                    and task is not None
                    and len(task.hints) > 1
                    and bool(_open_hints(task, calls))
                )
                if misnamed and not nudged_hint:
                    # 이어 붙일 조각을 잘못 지목했다. 한 번만 다시 고르게 한다
                    nudged_hint = True
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": _PENDING_RETRY})
                    continue
                # 조회 · 수정은 "지우지 않을게요" 처럼 저장 없이 끝나는 말이 정상이라 보지 않는다
                unclear_question = (
                    continuation is None
                    and task is not None
                    and task.unclear_only
                    and replied.kind == "question"
                )
                if unclear_question and not asked_unclear:
                    asked_unclear = True
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": _UNCLEAR_QUESTION})
                    continue
                # unclear 맥락은 "아이 얘기가 아니에요" 면 저장 없이 끝나는 게 맞다. 재확인 문구의
                # "필요한 값을 묻는다" 가 같은 질문을 다시 부를 수 있다
                recording = (
                    continuation is not None
                    and continuation.work is not WorkType.LOOKUP_EDIT
                    and not continuation.unclear
                )
                # 실패한 쓰기도 저장이 아니다
                unsaved = replied.kind == "message" and not _wrote(calls)
                claimed = unsaved and not _lookup_only(task) and _claims_saved(replied.text)
                if (recording and unsaved or continuation is None and claimed) and not rechecked:
                    # 저장 없이 말로 끝냈다. 저장한 척했을 수 있어 한 번 확인한다
                    rechecked = True
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": _UNSAVED_REPLY})
                    continue
                if rechecked and claimed:
                    replied = MemoryReply(text=_UNSAVED_FALLBACK, kind="message")
                return MemoryAgentResult(
                    reply=replied,
                    completed=True,
                    steps=step + 1,
                    calls=calls,
                    usage=usage,
                    drafts=context.drafts.all(),
                    pending=pending,
                    leftover=isinstance(parsed, ContinuationReplyOutput) and parsed.leftover,
                    ask_limit_reached=at_limit,
                )
            if content:
                # 스키마와 안 맞는 답. provider가 형식을 무시했을 수 있어 warning으로 남긴다 —
                # 이게 반복되면 모든 되묻기가 message로 나가 "이어서 적기" 가 안 열린다
                logger.warning("응답이 스키마와 맞지 않아 message 로 둔다 steps=%d", step + 1)
            return MemoryAgentResult(
                reply=MemoryReply(text=content, kind="message") if content else None,
                completed=True,
                steps=step + 1,
                calls=calls,
                usage=usage,
                drafts=context.drafts.all(),
            )

        messages.append(_assistant_message(response.message))
        step_records: list[ToolCallRecord] = []
        # 조회를 했는지는 이전 응답까지만 본다. 같은 응답의 조회는 모델이 결과를 못 본 것이다
        prior = list(calls)
        for call in tool_calls:  # 한 응답에 여러 개가 와도 전부, 온 순서대로 실행한다
            record = await _execute(
                call, context, succeeded, allowed, task=task, prior=prior, shown=shown
            )
            calls.append(record)
            step_records.append(record)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(record.result, ensure_ascii=False),
                }
            )

        # 이어받기는 조기 종료하지 않는다. 조각이 하나라 저장 한 번에 끝나 버리면
        # leftover를 받을 턴이 없다
        if (
            task is not None
            and continuation is None
            and EARLY_STOP
            and _covered(task, step_records, calls)
            # unclear 조각을 거절했으면 남기지 않았다고 알릴 턴을 준다
            and not _refused(calls, ErrorCode.NOT_CHILD_RECORD)
        ):
            # 모델에게 "무엇을 했는지" 요약을 받으려고 한 번 더 부르지 않는다
            return MemoryAgentResult(
                reply=None,
                completed=True,
                steps=step + 1,
                calls=calls,
                usage=usage,
                ended_by="coverage",
                drafts=context.drafts.all(),
            )

    # 모델이 마무리 응답을 내지 않아 미완료된 것으로 간주
    logger.warning("memory agent 미완료 max_steps=%d tools=%s", max_steps, [c.name for c in calls])
    return MemoryAgentResult(
        reply=None,
        completed=False,
        steps=max_steps,
        calls=calls,
        usage=usage,
        ended_by="max_steps",
        drafts=context.drafts.all(),
    )


def _user_message(raw_text: str, task: MemoryTask | None) -> str:
    """task 모드면 원문 뒤에 기록 후보를 붙인다. 후보가 없으면(강등 포함) 원문만.

    작업 종류 라벨(observe · schedule · lookup_edit)은 싣지 않는다 — 코드(묶음·종료 판정)만 쓴다.
    REQUEST 조각도 싣지 않는다 — "이건 요청" 이라는 표시를 보면 모델이 그 부분의 기록을 건너뛴다.
    """
    if task is None or not task.hints:
        return raw_text
    candidates = "\n".join(f"- {hint.text}" for hint in task.hints)
    return f"[보호자 발화]\n{raw_text}\n\n{_HINT_HEADER}\n{candidates}"


def _written_texts(calls: list[ToolCallRecord]) -> list[str]:
    """이번 run 에서 저장에 성공한 관찰의 raw_text."""
    return [
        _squash(str(call.arguments.get("raw_text", "")))
        for call in calls
        if call.success and call.name.startswith("create_observation_")
    ]


def _wrote(calls: list[ToolCallRecord]) -> bool:
    """이번 run 에서 성공한 쓰기가 하나라도 있는지. 일정 초안도 센다."""
    return any(call.success and call.name.startswith(MUTATING_PREFIXES) for call in calls)


def _claims_saved(text: str) -> bool:
    """저장했다 · 하겠다고 끝내는 말인가.

    지난 기록을 알려 주는 "기록했던" · 묻는 "기록할까요" 는 아니다.
    """
    return bool(_SAVED_CLAIM.search(text))


def _lookup_only(task: MemoryTask | None) -> bool:
    """조회 · 수정 조각만 맡은 run 인가.

    여기서 "기록했어요" 는 지난 기록을 알려 주는 말이다. 다시 물어도 같은 답이 나와
    맞는 조회 답이 _UNSAVED_FALLBACK 으로 바뀌었다. 일정 · 관찰 조각이 섞이면 그대로 본다.
    """
    return (
        task is not None
        and bool(task.hints)
        and all(hint.work is WorkType.LOOKUP_EDIT for hint in task.hints)
    )


def _asked(continuation: PendingMemoryContext) -> int:
    """그 조각에 지금까지 물은 횟수. 처음 질문에 이어받은 (질문, 답) 쌍 수를 더한다.

    transcript 는 (질문, 답) 을 평평하게 이어 붙여서 두 칸이 한 번이다.
    """
    return 1 + len(continuation.transcript) // 2


def _at_ask_limit(
    reply: MemoryReply,
    continuation: PendingMemoryContext | None,
    calls: list[ToolCallRecord],
) -> bool:
    """이어받기에서 또 물으려는데 그 조각에 이미 상한만큼 물었는가.

    상한 판정은 이것 하나다. run() 이 이 값으로 맥락을 만들지 않을지와 ask_limit_reached 를
    함께 정한다. 이번 run 에서 무엇이든 썼으면 상한이 아니다. 그 조각은 끝난 것으로 보고
    맥락을 원래 만들지 않으며, pipeline 은 맥락 없는 질문으로 다룬다.
    """
    return (
        reply.kind == "question"
        and continuation is not None
        and not _wrote(calls)
        and _asked(continuation) >= MAX_QUESTIONS
    )


def persisted_write(call: ToolCallRecord) -> bool:
    """DB 에 실제로 쓴 호출인가. 일정 초안과 바뀐 것 없는 성공은 세지 않는다.

    일정 · 준비물의 create/update 는 초안만 만든다(`draft=True`). 같은 값으로 바꾸거나
    이미 있는 준비물이면 아무것도 쓰지 않는다(`changed=[]` · `added=False`).
    준비물 챙김 표시는 바로 쓰고, 이름 변경과 한 호출에 와도 먼저 쓴다 — 그때 결과가
    이름 초안(`draft=True`)이어도 쓴 것이다. 결과 모양은 `tools/schedule.py` 가 정한다.
    쓰기 tool 을 더하면 test_persisted_write.py 의 CASES 에 그 tool 을 같이 넣는다.
    """
    if not call.success or not call.name.startswith(MUTATING_PREFIXES):
        return False
    if call.name == "update_event_item" and call.arguments.get("is_prepared") is not None:
        return True
    data = call.result.get("data") or {}
    if data.get("draft") is True:
        return False
    return data.get("changed") != [] and data.get("added") is not False


def _open_hints(task: MemoryTask, calls: list[ToolCallRecord]) -> list[MemoryHint]:
    """아직 저장하지 않은 후보. 관찰 조각만 저장 여부를 가릴 수 있고 나머지는 그대로 둔다."""
    written = _written_texts(calls)

    def kept(hint: MemoryHint) -> bool:
        text = _squash(hint.text)
        return any(text and done and (text in done or done in text) for done in written)

    return [
        hint for hint in task.hints if WorkType(hint.work) is not WorkType.OBSERVE or not kept(hint)
    ]


def _squash(text: str) -> str:
    return " ".join(text.split())


_OBSERVATION_RESOURCES = frozenset(
    f"observation_{domain}" for domain in ("food", "health", "education", "activity", "routine")
)


async def _reread(
    found: tuple[FoundRef, ...], context: AgentContext
) -> list[tuple[FoundRef, dict[str, Any]]]:
    """맥락에 남은 id를 store로 다시 읽는다. 그새 지워졌거나 이 아이 것이 아니면 빠진다."""
    shown: list[tuple[FoundRef, dict[str, Any]]] = []
    for ref in found:
        if ref.resource in _OBSERVATION_RESOURCES:
            observation = await context.store.get_observation(
                domain=ref.resource.removeprefix("observation_"), observation_id=ref.id
            )
            if observation is not None:
                shown.append((ref, observation.to_summary()))
        elif ref.resource == "event":
            event = await context.store.get_event(event_id=ref.id)
            if event is not None:
                # query_event 결과와 같은 모양으로 준비물까지 싣는다
                items = await context.store.list_event_items(event_id=ref.id)
                shown.append((ref, event.to_summary(items=items)))
    return shown


def _continuation_message(
    answer: str,
    pending: PendingMemoryContext,
    shown: Sequence[tuple[FoundRef, dict[str, Any]]] = (),
) -> str:
    """미완료 조각 · 이전 질문 · 새 답을 역할을 갈라 넣는다. 원문 전체는 넣지 않는다."""
    rounds = ""
    if pending.transcript:
        lines = "\n".join(pending.transcript)
        rounds = f"\n\n[앞서 주고받은 것]\n{lines}"
    # 헤더가 아니라 원문 전체 조각에만 붙인다. 헤더에 두면 답에 섞인 말까지 저장했다
    if pending.unclear:
        note = f"\n{_UNCLEAR_FRAGMENT}"
    elif pending.whole:
        note = f"\n{_WHOLE_FRAGMENT}"
    else:
        note = ""
    found = ""
    if shown:
        lines = "\n".join(
            f"- {ref.resource} {json.dumps(summary, ensure_ascii=False)}" for ref, summary in shown
        )
        found = f"{_FOUND_HEADER}\n{lines}\n\n"
    return (
        f"{_CONTINUATION_HEADER}{rounds}\n\n"
        f"[아직 저장하지 못한 조각]\n{pending.hint_text}{note}\n\n"
        f"{found}"
        f"[직전에 물은 것]\n{pending.question}\n\n"
        f"[보호자의 답]\n{answer}"
    )


def _covered(
    task: MemoryTask, step_records: list[ToolCallRecord], calls: list[ToolCallRecord]
) -> bool:
    """현재 스텝에서 작업을 종료해도 되는지 확인한다.

    다음 조건을 모두 만족
    - 이번 스텝에서 실행한 호출이 모두 성공
    - 이번 스텝의 호출이 모두 쓰기 작업
    - run 전체에 기존 일정의 준비물 쓰기(_OPEN_ENDED)가 없음
    - 작업 종류별 성공한 쓰기 수가 run 전체의 힌트 수를 충족
    - 힌트가 하나 이상 있고 알림 요청 힌트는 없음

    조건을 만족하지 않으면 다음 스텝에서 모델이 작업을 이어감
    """

    if not task.hints or not step_records:
        return False
    if any(cue in hint.text for hint in task.hints for cue in _NEEDS_REPLY_CUES):
        return False
    if not all(record.success for record in step_records):
        return False
    if not all(record.name.startswith(MUTATING_PREFIXES) for record in step_records):
        return False
    if any(record.name in _OPEN_ENDED for record in calls):
        return False

    written = [record.name for record in calls if record.success]
    needed = Counter(WorkType(hint.work) for hint in task.hints)
    return all(
        sum(name.startswith(WRITES_FOR[work]) for name in written) >= count
        for work, count in needed.items()
    )


def _parse_reply(content: str, model: type[ReplyOutput] = ReplyOutput) -> ReplyOutput | None:
    """말하는 턴의 content를 스키마로 읽는다. 안 맞으면 None -> 호출부가 message 로 둔다."""
    if not content:
        return None
    try:
        return model.model_validate_json(content)
    except ValidationError:
        return None


def _pending(
    reply: MemoryReply,
    named: str | None,
    task: MemoryTask | None,
    continuation: PendingMemoryContext | None = None,
    answer: str = "",
    calls: list[ToolCallRecord] | None = None,
) -> PendingMemoryContext | None:
    """되묻기로 끝났을 때 이어 붙일 조각을 고른다. 없으면 None.

    - 이어받기 중이면 조각은 그대로 두고 방금 실패한 질문과 답을 transcript에 쌓는다.
      이번 run 에서 무엇이든 썼으면 그 조각은 끝난 것으로 보고 pending을 만들지 않는다.
      재질문 상한은 run() 이 _at_ask_limit 로 먼저 보고, 상한이면 이 함수를 부르지 않는다.
    - 이번 run 에서 이미 저장한 관찰 조각은 고르지 않는다.
    - 후보가 하나뿐이면 모델에게 묻지 않고 그것을 쓴다.
    - 후보가 없으면(강등 경로) 원문 전체가 한 조각인데,
      일정 초안까지 무엇이든 썼으면 조각을 가를 수 없어 pending을 만들지 않는다.
    - 둘 이상이면 모델이 지목한 문장이 아직 저장하지 않은 후보여야 한다.
    """
    calls = calls or []
    if reply.kind != "question":
        return None
    found = _found(calls, continuation.found if continuation is not None else ())
    if continuation is not None:
        # 저장한 조각을 남기면 다음 답에서 한 번 더 저장된다.
        # 이어받기는 조기 종료하지 않아서 저장한 뒤에 묻는 경우가 생긴다
        if _wrote(calls):
            return None
        return PendingMemoryContext(
            hint_text=continuation.hint_text,
            question=reply.text,
            work=continuation.work,
            transcript=(*continuation.transcript, continuation.question, answer),
            whole=continuation.whole,
            unclear=continuation.unclear,
            found=found,
        )
    if task is None:
        return None
    if not task.hints:
        # whole 은 "조각 안의 기록은 하나도 저장하지 않았다" 로 이어받기에 넘어간다.
        # 관찰만 세면 일정 초안을 만든 run 도 맥락이 남아 같은 초안이 한 번 더 생긴다
        if _wrote(calls):
            return None
        # 작업 종류를 모른다. observe로 두면 이어받기가 수정·삭제 tool을 열지 않는다
        return PendingMemoryContext(
            # unclear 만 있었으면 그 조각만 잇는다. 원문에 섞인 요청은 이어받기 대상이 아니다
            hint_text=" ".join(task.unclear) if task.unclear_only else task.raw_text,
            question=reply.text,
            work=WorkType.OBSERVE,
            whole=True,
            unclear=task.unclear_only,
            found=found,
        )
    open_hints = _open_hints(task, calls)
    if len(task.hints) == 1:
        if not open_hints:
            return None
        hint = open_hints[0]
        return PendingMemoryContext(
            hint_text=hint.text, question=reply.text, work=WorkType(hint.work), found=found
        )

    chosen = next((hint for hint in open_hints if hint.text == named), None)
    if chosen is None:
        logger.info("pending_hint 가 후보에 없다 hints=%d", len(task.hints))
        return None
    return PendingMemoryContext(
        hint_text=chosen.text, question=reply.text, work=WorkType(chosen.work), found=found
    )


def _found(calls: list[ToolCallRecord], carried: tuple[FoundRef, ...] = ()) -> tuple[FoundRef, ...]:
    """되묻기 전에 조회로 찾은 행. 이번 run 에서 행이 나온 마지막 조회의 결과다.

    질문은 마지막 조회를 보고 나온다. 앞선 넓은 조회까지 남기면 질문과 무관한 행이 같이 보인다.
    이번 run에 행이 나온 조회가 없으면 앞 맥락의 것을 그대로 넘긴다.
    id는 tool 결과에서 코드가 옮긴다. 실패한 조회는 data가 없어 들어오지 않는다.
    MAX_FOUND를 넘으면 하나도 남기지 않는다.
    """
    refs = list(carried)
    for call in reversed(calls):
        if not call.success:
            continue
        data = call.result.get("data") or {}
        if call.name.startswith("query_observation_"):
            resource, rows = call.name.removeprefix("query_"), data.get("records") or []
        elif call.name == "query_event":
            resource, rows = "event", data.get("events") or []
        else:
            continue
        if rows:
            refs = list(dict.fromkeys(FoundRef(resource, str(row["id"])) for row in rows))
            break
    return tuple(refs) if len(refs) <= MAX_FOUND else ()


async def _execute(
    call: Any,
    context: AgentContext,
    succeeded: set[tuple[str, str]],
    allowed: Collection[str] | None = None,
    *,
    task: MemoryTask | None = None,
    prior: list[ToolCallRecord] | None = None,
    shown: Sequence[tuple[FoundRef, dict[str, Any]]] = (),
) -> ToolCallRecord:
    name = call.function.name
    arguments = _parse_arguments(call.function.arguments)
    if arguments is None:
        # registry는 dict를 전제
        return _failed(name, {}, ErrorCode.INVALID_ARGS, _BAD_JSON)
    if _hazard(name, arguments):
        return _failed(name, arguments, ErrorCode.HAZARD, _HAZARD)
    if task is not None and task.unclear_only and name.startswith("update_observation_"):
        # 아이 기록이 아닌 말로 이미 저장된 아이 기록을 고치지 않는다
        return _failed(name, arguments, ErrorCode.NOT_CHILD_RECORD, _NOT_CHILD_RECORD_ALONE)
    if task is not None and name.startswith(WRITES_FOR[WorkType.OBSERVE]):
        if _in_unclear(arguments, task):
            message = _NOT_CHILD_RECORD_ALONE if task.unclear_only else _NOT_CHILD_RECORD_MIXED
            return _failed(name, arguments, ErrorCode.NOT_CHILD_RECORD, message)
        if _skipped_lookup(
            str(arguments.get("raw_text", "")),
            task,
            prior or [],
            # 같은 종류의 관찰을 보여 줬을 때만 조회한 것으로 본다
            looked_up=name.removeprefix("create_") in {ref.resource for ref, _ in shown},
        ):
            return _failed(name, arguments, ErrorCode.TARGET_REQUIRED, _LOOKUP_FIRST)

    key = _dedup_key(name, arguments)
    if key is not None and key in succeeded:
        return _failed(name, arguments, ErrorCode.INVALID_ARGS, _ALREADY_DONE)

    result = await execute_tool(name, arguments, context, allowed=allowed)
    if key is not None and result.success:
        succeeded.add(key)  # 실패한 호출은 고쳐서 다시 부를 수 있어야 한다
    return ToolCallRecord(name=name, arguments=arguments, result=result.to_payload())


def _letters(text: str) -> str:
    """글자·숫자만. 띄어쓰기·문장부호가 달라도 같은 조각으로 본다."""
    return "".join(char for char in text if char.isalnum())


# 관찰 create 인자 중 "무엇에 대한 기록인가" 를 담는 칸. 도메인마다 이름이 다르다
_TARGET_FIELDS = ("subject", "symptom", "activity", "topic")


def _targets(arguments: dict[str, Any]) -> list[str]:
    """관찰 대상 이름(글자만). symptom 은 목록이다."""
    names: list[str] = []
    for field_name in _TARGET_FIELDS:
        value = arguments.get(field_name)
        values = value if isinstance(value, list) else [value]
        names.extend(_letters(str(item)) for item in values if item)
    return [name for name in names if name]


# 위험 물질을 보는 관찰과 그 대상 이름 칸. 음식 · 생활은 술 · 담배 · 화학제품까지, 놀이 · 배움은
# 마약류만 — "담배의 해로움" 같은 배움 주제와 "동전 모으기" 같은 놀이는 기록이다.
# 건강 관찰은 보지 않는다 — 위험 물질 사건을 건강 기록으로 남길지는 PM 결정이다 (#626 범위 밖)
_HAZARD_FIELDS: dict[str, tuple[tuple[str, ...], tuple[Term, ...]]] = {
    "observation_food": (("subject",), FOOD_NAME_TERMS),
    "observation_activity": (("activity", "subject"), PLAY_NAME_TERMS),
    "observation_education": (("topic", "subject"), PLAY_NAME_TERMS),
    # 식사에서 거절된 뒤 "소주 마시기" 로 바꿔 저장하는 길을 막는다 (#626 리뷰)
    "observation_routine": (("subject",), FOOD_NAME_TERMS),
}


def _hazard(name: str, arguments: dict[str, Any]) -> bool:
    """관찰 create · update 의 대상 이름이 위험 물질인가. 원문(raw_text)은 보지 않는다."""
    for prefix in ("create_", "update_"):
        found = _HAZARD_FIELDS.get(name.removeprefix(prefix)) if name.startswith(prefix) else None
        if found is not None:
            fields, terms = found
            names = [str(arguments[f]) for f in fields if arguments.get(f)]
            return bool(names) and bool(hazard_names(names, terms))
    return False


def _in_unclear(arguments: dict[str, Any], task: MemoryTask) -> bool:
    """이 관찰이 Supervisor 가 unclear 로 본 조각의 기록인가.

    기록 조각 없이 unclear 만 있으면 무엇을 적든 막는다. 섞여 있으면 둘 중 하나로 본다.
      - 대상 이름이 unclear 조각에만 나오고 기록 조각에는 없다 — 원문을 길게·바꿔 적어도 막힌다
      - 원문이 unclear 조각 안에 들어간다
    기록 조각과 겹치는 원문은 기록으로 본다 — 모델이 원문을 길게 적어도 기록이 막히지 않게.
    """
    if not task.unclear:
        return False
    if task.unclear_only:
        return True
    unclear = [_letters(segment) for segment in task.unclear]
    hints = [_letters(hint.text) for hint in task.hints]
    names = _targets(arguments)
    in_unclear = any(name in segment for name in names for segment in unclear)
    in_hint = any(name in hint for name in names for hint in hints)
    if in_unclear and not in_hint:
        return True
    text = _letters(str(arguments.get("raw_text", "")))
    if not text or any(text in hint or hint in text for hint in hints):
        return False
    return any(text in segment for segment in unclear)


def _refused(calls: list[ToolCallRecord], code: str) -> bool:
    """이번 run 에 이 코드로 거절된 호출이 있는가."""
    return any(call.result.get("error", {}).get("code") == code for call in calls)


def _skipped_lookup(
    raw_text: str, task: MemoryTask, prior: list[ToolCallRecord], *, looked_up: bool = False
) -> bool:
    """고쳐 말하는 조각(lookup_edit)으로 관찰을 새로 만들면서 저장된 기록을 찾아본 적이 없는가.

    수정인지는 Supervisor 라벨을 따른다. 찾아본 뒤에 만드는 것은 막지 않는다 — 바뀌기 전 기록이
    없으면 바뀐 값만 새로 저장하는 게 맞다. prior 는 이전 응답까지의 호출이다.
    원문이 관찰(observe) 조각에도 들어가면 그 조각의 기록으로 보고 막지 않는다.
    looked_up 은 이어받기에서 앞서 찾은 기록을 보여 준 경우로, 조회한 것으로 본다.
    """
    if looked_up:
        return False
    edits = [_letters(hint.text) for hint in task.hints if hint.work == WorkType.LOOKUP_EDIT]
    if not edits:
        return False
    if any(call.success and call.name.startswith("query_observation_") for call in prior):
        return False
    text = _letters(raw_text)
    if not text:
        return False
    observes = [_letters(hint.text) for hint in task.hints if hint.work == WorkType.OBSERVE]
    if any(text in hint or hint in text for hint in observes if hint):
        return False
    return any(edit and (text in edit or edit in text) for edit in edits)


def _parse_arguments(raw: str | None) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _dedup_key(name: str, arguments: dict[str, Any]) -> tuple[str, str] | None:
    """같은 run에서 두 번 하면 안 되는 작업인지 가른다.

    관찰 저장은 인자가 아니라 대상으로 센다. 인자 전체로 비교하면 raw_text를 한 글자만
    다르게 준 두 호출이 다 통과해 같은 관찰이 두 행으로 남는다.
    """
    if not name.startswith(MUTATING_PREFIXES):
        return None  # 분리·조회는 반복 호출해도 상태 안 바뀜
    if name.startswith("create_observation_"):
        target = arguments.get("subject") or arguments.get("symptom") or arguments.get("raw_text")
        return name, json.dumps(
            [target, arguments.get("observed_on")], sort_keys=True, ensure_ascii=False
        )
    return name, json.dumps(arguments, sort_keys=True, ensure_ascii=False)


def _failed(name: str, arguments: dict[str, Any], code: str, message: str) -> ToolCallRecord:
    result = fail("parse", name, code, message)
    return ToolCallRecord(name=name, arguments=arguments, result=result.to_payload())


def _assistant_message(message: Any) -> dict[str, Any]:
    """모델 응답을 다음 요청에 그대로 돌려보낼 형태로 만든다. SDK 객체를 그대로 덤프하지 않는다."""
    payload: dict[str, Any] = {"role": "assistant", "content": message.content}
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ]
    return payload


def _accumulate(total: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value
