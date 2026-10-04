"""Memory Agent 진입점. 이 디렉토리에서 밖으로 열려 있는 유일한 함수.

raw_text 하나를 받아 tool calling을 끝까지 돌리고 결과를 돌려준다.
app/api와 이후의 Supervisor는 내부(registry·prompt·client)를 모른 채 run()만 부른다.

루프가 지는 책임은 세 가지다.
  - 모델이 낸 arguments가 깨져도 루프를 죽이지 않는다
  - 같은 인자로 성공한 쓰기 작업을 두 번 실행하지 않는다
  - 끝나지 않는 대화를 끊고, 끝났는지 여부를 호출자에게 알린다

task 를 넘기면(Supervisor 경로) 세 가지가 더 붙는다.
  - [tool 묶음] 기록 기본 묶음은 항상, 수정·삭제는 lookup_edit 조각이 있을 때만
  - [허용 목록] 모델에게 안 보여준 tool은 이름으로 불러도 실행하지 않는다
  - [조기 종료] 할 일을 다 한 게 코드로 확인되면 요약 호출 없이 끝낸다 (_covered)
task 없이 부르면 전체 tool 을 열고 끊지 않는다 — Supervisor 없이 Memory 만 재는 경로다.
"""

import json
import logging
from collections import Counter
from collections.abc import Collection
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
    MemoryHint,
    MemoryTask,
    PendingMemoryContext,
    WorkType,
)

logger = logging.getLogger(__name__)

# 복합 발화 대비. 세는 것은 **LLM 왕복 수**이지 tool 건수가 아니다 —
# 한 왕복에 tool 이 여러 개 와도 그 바퀴에서 전부 실행하고 step 은 1만 올라간다.
MAX_STEPS = 7
MAX_COMPLETION_TOKENS = 1400

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

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.calls]

    @property
    def final_message(self) -> str | None:
        """이전 이름. pipeline 로그와 eval 이 이걸로 본다."""
        return self.reply.text if self.reply is not None else None


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

    llm = client or LLMClient(role="memory")
    allowed = tools_for(task) if task is not None else None
    tools = specs_for(allowed) if allowed is not None else TOOL_SPECS
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context)},
        {
            "role": "user",
            "content": (
                _continuation_message(raw_text, continuation)
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
                pending = _pending(
                    replied, parsed.pending_hint, task, continuation, raw_text, calls
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
                return MemoryAgentResult(
                    reply=replied,
                    completed=True,
                    steps=step + 1,
                    calls=calls,
                    usage=usage,
                    drafts=context.drafts.all(),
                    pending=pending,
                    leftover=isinstance(parsed, ContinuationReplyOutput) and parsed.leftover,
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
        for call in tool_calls:  # 한 응답에 여러 개가 와도 전부, 온 순서대로 실행한다
            record = await _execute(call, context, succeeded, allowed)
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


def _continuation_message(answer: str, pending: PendingMemoryContext) -> str:
    """미완료 조각 · 이전 질문 · 새 답을 역할을 갈라 넣는다. 원문 전체는 넣지 않는다."""
    rounds = ""
    if pending.transcript:
        lines = "\n".join(pending.transcript)
        rounds = f"\n\n[앞서 주고받은 것]\n{lines}"
    # 헤더가 아니라 원문 전체 조각에만 붙인다. 헤더에 두면 답에 섞인 말까지 저장했다
    whole = f"\n{_WHOLE_FRAGMENT}" if pending.whole else ""
    return (
        f"{_CONTINUATION_HEADER}{rounds}\n\n"
        f"[아직 저장하지 못한 조각]\n{pending.hint_text}{whole}\n\n"
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
    - 이번 run 에서 이미 저장한 관찰 조각은 고르지 않는다.
    - 후보가 하나뿐이면 모델에게 묻지 않고 그것을 쓴다.
    - 후보가 없으면(강등 경로) 원문 전체가 한 조각인데,
      일정 초안까지 무엇이든 썼으면 조각을 가를 수 없어 pending을 만들지 않는다.
    - 둘 이상이면 모델이 지목한 문장이 아직 저장하지 않은 후보여야 한다.
    """
    calls = calls or []
    if reply.kind != "question":
        return None
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
            hint_text=task.raw_text, question=reply.text, work=WorkType.OBSERVE, whole=True
        )
    open_hints = _open_hints(task, calls)
    if len(task.hints) == 1:
        if not open_hints:
            return None
        hint = open_hints[0]
        return PendingMemoryContext(
            hint_text=hint.text, question=reply.text, work=WorkType(hint.work)
        )

    chosen = next((hint for hint in open_hints if hint.text == named), None)
    if chosen is None:
        logger.info("pending_hint 가 후보에 없다 hints=%d", len(task.hints))
        return None
    return PendingMemoryContext(
        hint_text=chosen.text, question=reply.text, work=WorkType(chosen.work)
    )


async def _execute(
    call: Any,
    context: AgentContext,
    succeeded: set[tuple[str, str]],
    allowed: Collection[str] | None = None,
) -> ToolCallRecord:
    name = call.function.name
    arguments = _parse_arguments(call.function.arguments)
    if arguments is None:
        # registry는 dict를 전제
        return _failed(name, {}, ErrorCode.INVALID_ARGS, _BAD_JSON)

    key = _dedup_key(name, arguments)
    if key is not None and key in succeeded:
        return _failed(name, arguments, ErrorCode.INVALID_ARGS, _ALREADY_DONE)

    result = await execute_tool(name, arguments, context, allowed=allowed)
    if key is not None and result.success:
        succeeded.add(key)  # 실패한 호출은 고쳐서 다시 부를 수 있어야 한다
    return ToolCallRecord(name=name, arguments=arguments, result=result.to_payload())


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
