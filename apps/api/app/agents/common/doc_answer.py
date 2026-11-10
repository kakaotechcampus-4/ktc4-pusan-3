"""일반 정보 질문의 문서 근거 답변 (#574) — 세 Agent 가 같이 쓰는 뼈대.

"보통 3세는 숫자 몇까지 세?" 처럼 아이 일반을 묻는 질문(`task_type=info`)에 추천 카드 대신, 질문 속
나이와 겹치는 문서 행을 근거로 단 답변 한 덩어리(readout `doc_answer`)를 낸다. 순서는 코드가 정한다.

    1. 안전 조회 실패            → 그 Agent 의 blocked readout. 모델 0회
    2. 질문 속 나이 → 월령 구간  → 없으면 아이 월령 한 달. 범위 밖이면 `info.out_of_range`
    3. 도메인이 안전하게 거른 행 → 질문 낱말과 겹치는 순으로 `INFO_ROW_LIMIT` 행.
                                   0행이면 `info.no_rows`
    4. 모델 1회                  → 출력 tool 을 강제한다. 다시 부르지 않는다
    5. 코드 검사                 → 평가 표현 · "우리 아이" 가 하나라도 있으면 답 전체를 버린다.
                                   보여 주지 않은 id · 인용 없음 · 단 행에 없는 숫자 · 도메인 검사에
                                   걸린 문장은 그 문장만 뺀다
    6. readout                   → 문장을 이어 붙이고, 실제로 단 행만 `source_refs` 로 싣는다

도메인은 `DocAnswerSpec` 으로 행 읽기(안전 거르기 포함) · 문장 검사만 넘긴다. 알레르기 · 위험 용어 ·
판정 표현 규칙은 각 Agent 가 가진 그대로다(루트 §2 — 규칙은 코드가 막는다).

모델 입력은 질문 · 질문에 적힌 나이 · 거른 행뿐이다. 아이 이름 · 실제 나이 · 기록 · 알레르기 목록은
싣지 않는다(§2 Agent 별 필요 필드만). 결과는 추천이 아니라서 `suggestions` 는 늘 비고, 개인화
집계에도 05 에도 들어가지 않는다.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.common.llm_client import LLMConfigError, LLMError
from app.agents.common.prompt_fingerprint import register_prompt, tools_text
from app.agents.common.readout import Readout, ReadoutCatalog
from app.agents.common.refs import DocKind, Ref
from app.agents.common.result import AgentStatus, DomainAgentResult
from app.agents.common.schemas.task import INFO_TASK, DomainTask
from app.agents.common.tool_schema import ToolDefinition, build_tool_spec
from app.rules.age import asked_age_label, asked_month_range
from app.rules.evaluative import find_evaluative
from app.rules.term_match import normalize

logger = logging.getLogger(__name__)

DOC_ANSWER_READOUTS = ReadoutCatalog.from_yaml(Path(__file__).with_name("doc_answer.readout.yaml"))

# 모델에 보내는 행 수. 안전 필터에서 빠진 자리를 채울 수 있게 도메인은 넉넉히 읽어 넘긴다
INFO_ROW_LIMIT = 5
MAX_SENTENCES = 4
MAX_SENTENCE_CHARS = 120
MAX_CITED_ROWS = 3
MAX_COMPLETION_TOKENS = 600
TOOL_NAME = "answer_from_docs"
# 프롬프트 지문 · 호출 로그 이름 (#770). Agent 마다가 아니라 세 Agent 가 같이 쓰는 한 프롬프트다
PROMPT_NAME = "doc_answer"

# 아이 일반을 묻는 답에 이 아이 얘기가 섞이면 개인화인 척이 된다(§2). 공백을 지운 문장에 건다
_OUR_CHILD = re.compile(r"(?:우리|저희|내)(?:아이|애|아기|아가|딸|아들|자녀|새싹)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# 문장에서 나이로 쓴 숫자 — 질문에 적힌 나이를 되풀이하는 것만 질문에서 온 숫자로 허용한다
_AGE_USE = re.compile(r"(\d+)\s*(?:세|살|개월)")
# 질문 낱말과 행을 맞출 때 쓰는 낱말 길이 하한 — 한 글자는 조사와 섞여 아무 데나 걸린다
_MIN_TERM = 2


@dataclass(frozen=True)
class DocLine:
    """세 문서 행의 공통 모양 — 모델 입력과 검사가 보는 칸만."""

    id: UUID
    title: str
    body: str
    materials: tuple[str, ...] = ()


# 구간 하한 · 상한 · 질문 → 그 Agent 의 안전 규칙으로 거른 행
RowReader = Callable[[int, int, str], Awaitable[Sequence[DocLine]]]
# 문장 · 구간 하한 → 거절 사유. 통과면 None
SentenceCheck = Callable[[str, int], str | None]


@dataclass(frozen=True)
class DocAnswerSpec:
    agent: str
    doc_kind: DocKind
    topic: str  # 제목에 들어가는 낱말 — 놀이 · 먹을거리 · 생활과 배움
    rows: RowReader
    check: SentenceCheck
    blocked: Readout  # 안전 조회 실패 때 그 Agent 가 내는 readout


class GroundedSentence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 길이 · 개수 상한은 스키마가 아니라 코드가 문장 단위로 건다 — 한 문장이 넘쳤다고 검증이 통째로
    # 실패해 유효한 나머지 문장까지 버리지 않게(`_sentence_problem`). 모델에게는 설명으로만 알린다
    text: Annotated[
        str, Field(min_length=1, description=f"답 한 문장. {MAX_SENTENCE_CHARS}자 이내")
    ]
    row_ids: Annotated[list[str], Field(description="이 문장이 바탕으로 한 [자료] id 1~3개")]


class DocAnswerSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answerable: Annotated[
        bool, Field(description="[자료]만으로 질문에 답할 수 있는가. 아니면 sentences 를 비운다")
    ]
    normative_question: Annotated[
        bool,
        Field(description="나이별 기준 · 정상 범위 · 몇 살까지 해야 하는지를 묻는 질문인가"),
    ]
    sentences: Annotated[
        list[GroundedSentence], Field(description=f"answerable 이면 2~{MAX_SENTENCES}문장")
    ]


ANSWER_TOOL = ToolDefinition(
    name=TOOL_NAME,
    description="[자료]만으로 보호자의 일반 질문에 답한다. 이 tool 을 한 번만 부른다.",
    args=DocAnswerSubmission,
)
_TOOL_SPEC = build_tool_spec(ANSWER_TOOL)
_TOOL_CHOICE = {"type": "function", "function": {"name": TOOL_NAME}}

SYSTEM_PROMPT = """
[역할]
너는 보호자가 아이 일반에 대해 묻는 질문에 [자료]만으로 답하는 도우미다. 이 아이 얘기가 아니다.

[답하는 법]
- answer_from_docs 를 한 번 부른다. 문장마다 바탕으로 한 [자료] id 를 1~3개 단다.
- [자료]에 없는 내용은 쓰지 않는다. 숫자는 [자료]나 질문에 있는 것만 쓴다.
- [자료]로 답할 수 없으면 answerable 을 false 로 하고 sentences 를 비운다.
- 2~4문장, 한 문장은 120자 이내. 해 볼 수 있는 놀이 · 활동 · 연습을 설명한다.

[하지 않는 것]
- 나이별 기준 · 정상 범위 · "몇 살까지 해야 한다" 를 말하지 않는다. 그런 질문이면
  normative_question 을 true 로 하고, 기준 대신 [자료]의 관련 활동만 설명한다.
- 또래 · 평균 · 정상 · 늦다 · 빠르다 · 뛰어나다 같은 비교 · 평가하는 말을 쓰지 않는다.
- "우리 아이" · "우리 애" 처럼 이 아이 얘기로 쓰지 않는다.
- 진단 · 약 · 영양제를 말하지 않는다.
""".strip()


async def answer_from_docs(
    spec: DocAnswerSpec,
    task: DomainTask,
    *,
    child_months: int,
    month_limit: int,
    safety_ok: bool,
    llm: Any,
) -> DomainAgentResult:
    """`task_type=info` 한 건을 문서 근거 답변 하나로. 모델은 0회 또는 1회다."""
    if not safety_ok:
        return _result(spec, "blocked", (spec.blocked,))

    question = " ".join(text.strip() for text in task.request_texts if text.strip())
    asked = asked_month_range(question)
    lo, hi = asked if asked is not None else (child_months, child_months + 1)
    if lo >= hi or lo >= month_limit or lo < 0:
        return _result(spec, "completed", (DOC_ANSWER_READOUTS.render("info.out_of_range"),))
    hi = min(hi, month_limit)

    rows = _ranked(await spec.rows(lo, hi, question), question)[:INFO_ROW_LIMIT]
    if not rows:
        return _result(spec, "completed", (DOC_ANSWER_READOUTS.render("info.no_rows"),))

    # 질문에 나이가 없으면 모델에 나이를 싣지 않는다 — 아이의 실제 나이가 된다
    label = asked_age_label(lo, hi) if asked is not None else None
    asked_label = label or ""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_message(question, label, rows)},
    ]
    submission = await _submit(llm, messages, task)
    if submission is None:
        return _result(spec, "degraded", (DOC_ANSWER_READOUTS.render("info.fallback"),), calls=1)
    if not submission.answerable:
        return _result(spec, "completed", (DOC_ANSWER_READOUTS.render("info.no_match"),), calls=1)

    submitted = submission.sentences[:MAX_SENTENCES]
    texts = [sentence.text for sentence in submitted]
    if any(find_evaluative(text) or _about_our_child(text) for text in texts):
        # 한 문장이라도 판정 · 이 아이 얘기면 답 전체가 그 틀로 쓰였다고 본다
        logger.warning("일반 정보 답변을 버림 agent=%s reason=evaluative run_id=%s",
                       spec.agent, task.run_id)  # fmt: skip
        return _result(spec, "degraded", (DOC_ANSWER_READOUTS.render("info.fallback"),), calls=1)

    shown = {row.id: row for row in rows}
    kept: list[str] = []
    cited: list[UUID] = []
    for sentence in submitted:
        ids = _cited(sentence.row_ids, shown)
        reason = _sentence_problem(sentence.text, ids, shown, asked_label, spec, lo)
        if reason is not None:
            logger.info("일반 정보 답변 문장을 뺌 agent=%s reason=%s run_id=%s",
                        spec.agent, reason, task.run_id)  # fmt: skip
            continue
        kept.append(sentence.text.strip())
        cited += [key for key in ids if key not in cited]
    if not kept:
        return _result(spec, "degraded", (DOC_ANSWER_READOUTS.render("info.fallback"),), calls=1)

    answer = Readout(
        kind="doc_answer",
        title=f"{asked_age_label(lo, hi)} 무렵 {spec.topic} · 일반 자료",
        body=" ".join(kept),
        authored_by="model",
        source_refs=tuple(Ref(kind=spec.doc_kind, id=key) for key in cited),
    )
    readouts: tuple[Readout, ...] = (answer,)
    if submission.normative_question:
        readouts = (DOC_ANSWER_READOUTS.render("info.norm_notice"), answer)
    return _result(spec, "completed", readouts, calls=1)


def _ranked(rows: Sequence[DocLine], question: str) -> list[DocLine]:
    """질문 낱말과 겹치는 행이 먼저. 같으면 도메인이 넘긴 순서 그대로다(결정적)."""
    asked = normalize(question)

    def score(row: DocLine) -> int:
        words = (*row.title.split(), *row.body.split(), *row.materials)
        terms = {normalize(word) for word in words}
        return sum(1 for term in terms if len(term) >= _MIN_TERM and term in asked)

    order = {row.id: index for index, row in enumerate(rows)}
    return sorted(rows, key=lambda row: (-score(row), order[row.id]))


def _user_message(question: str, label: str | None, rows: Sequence[DocLine]) -> str:
    age = f"[질문의 나이] {label}" if label else "[질문의 나이] 질문에 나이가 없다"
    lines = [f"[질문] {question}", age, "[자료]"]
    for row in rows:
        materials = f" (준비물 {', '.join(row.materials)})" if row.materials else ""
        lines.append(f"- id {row.id} · {row.title}: {row.body}{materials}")
    return "\n".join(lines)


async def _submit(llm: Any, messages: list[dict[str, Any]], task: DomainTask):
    """모델 1회. 실패 · tool 없음 · 인자 오류면 None — 다시 부르지 않는다."""
    try:
        response = await llm.chat(
            messages,
            tools=[_TOOL_SPEC],
            tool_choice=_TOOL_CHOICE,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            prompt_name=PROMPT_NAME,
        )
    except LLMConfigError:
        raise
    except LLMError as exc:
        logger.warning("일반 정보 답변 모델 호출 실패 run_id=%s reason=%s", task.run_id, exc)
        return None
    calls = getattr(response.message, "tool_calls", None) or []
    call = next((c for c in calls if c.function.name == TOOL_NAME), None)
    if call is None:
        logger.warning("일반 정보 답변 tool 호출 없음 run_id=%s", task.run_id)
        return None
    try:
        return DocAnswerSubmission.model_validate(json.loads(call.function.arguments or "{}"))
    except (json.JSONDecodeError, ValidationError):
        logger.warning("일반 정보 답변 인자 오류 run_id=%s", task.run_id)
        return None


def _cited(raw_ids: Sequence[str], shown: dict[UUID, DocLine]) -> list[UUID] | None:
    """문장이 단 id. 하나라도 보여 주지 않은 id 거나 3개를 넘으면 None — 지어낸 근거다."""
    if len(raw_ids) > MAX_CITED_ROWS:
        return None
    ids: list[UUID] = []
    for raw in raw_ids:
        try:
            key = UUID(raw)
        except ValueError:
            return None
        if key not in shown:
            return None
        ids.append(key)
    return ids or None


def _sentence_problem(
    text: str,
    ids: list[UUID] | None,
    shown: dict[UUID, DocLine],
    asked_label: str,
    spec: DocAnswerSpec,
    lo: int,
) -> str | None:
    if ids is None:
        return "unknown_or_missing_id"
    if len(text) > MAX_SENTENCE_CHARS:
        return "too_long"
    grounds = " ".join(
        part for key in ids for part in (shown[key].title, shown[key].body, *shown[key].materials)
    )
    # 질문에서 온 숫자는 질문에 적힌 나이를 나이로 되풀이할 때만 쓸 수 있다. 질문 속 기준 숫자
    # ("10까지 세?")를 모델이 그대로 확인해 주면 나이별 기준을 말하는 것이다(§1)
    asked_ages = set(_NUMBER.findall(asked_label))
    age_numbers = {n for n in _AGE_USE.findall(text) if n in asked_ages}
    known = set(_NUMBER.findall(grounds)) | age_numbers
    if any(number not in known for number in _NUMBER.findall(text)):
        return "ungrounded_number"
    return spec.check(text, lo)


def _about_our_child(text: str) -> bool:
    return bool(_OUR_CHILD.search(normalize(text)))


def _result(
    spec: DocAnswerSpec, status: AgentStatus, readouts: tuple[Readout, ...], *, calls: int = 0
) -> DomainAgentResult:
    return DomainAgentResult(
        agent=spec.agent,
        task_type=INFO_TASK,
        status=status,
        readouts=readouts,
        model_calls=calls,
    )


def _fingerprint_parts() -> tuple[str, ...]:
    """프롬프트 지문(#770)의 입력.

    system 프롬프트, user 메시지 틀(나이가 있을 때 · 없을 때), tool spec.
    """
    row = DocLine(id=UUID(int=0), title="제목", body="본문", materials=("준비물",))
    return (
        SYSTEM_PROMPT,
        _user_message("질문", "만 3세", (row,)),
        _user_message("질문", None, (row,)),
        tools_text([ANSWER_TOOL]),
    )


register_prompt(PROMPT_NAME, _fingerprint_parts)
