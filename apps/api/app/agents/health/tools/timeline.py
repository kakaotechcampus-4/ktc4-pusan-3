"""진료 준비 출력 tool — `compose_symptom_timeline`.

모델이 날짜순으로 정리한 줄을 받아 코드가 거르고(`review.py`), 통과하면 `Readout(model,
kind="symptom_timeline")` 를 state 에 둔다. 아무것도 저장하지 않는다.

- 거절이 있으면 아무것도 내지 않고 `INVALID_ARGS` 로 사유를 돌려준다 — 모델이 그 줄을 고쳐 부른다
- 지워진 줄만 있고 남은 줄이 없으면 `EVIDENCE_REQUIRED`. 근거 없는 정리를 내지 않는다
- 알레르기 · 만성질환 줄은 코드가 `state.safety_entries` 원문으로 붙인다. 모델은 쓰지 않는다.
  조회에 실패했거나 게이트가 비어 있으면 줄 대신 `safety.unavailable` 경고를 맨 위에 둔다 —
  읽지 못한 것을 "등록된 내용이 없어요" 로 쓰지 않는다
- 같은 run 에 다시 부르면(거절 뒤 고쳐 쓴 것) 앞의 모델 타임라인을 교체한다
"""

from collections.abc import Iterable
from datetime import date

from app.agents.common.readout import Readout
from app.agents.health.context import HealthContext
from app.agents.health.readouts import (
    CARD_ALLERGY,
    CARD_CHRONIC,
    CARD_EMPTY,
    READOUTS,
    SAFETY_UNAVAILABLE,
)
from app.agents.health.result import ErrorCode, ToolResult, fail, ok
from app.agents.health.review import review
from app.agents.health.schemas.records import ComposeTimelineArgs, TimelineLine

RESOURCE = "compose_symptom_timeline"
KIND = "symptom_timeline"


async def compose_symptom_timeline(context: HealthContext, args: ComposeTimelineArgs) -> ToolResult:
    result = review(args.lines, context)
    if result.rejected:
        return fail(
            "draft",
            RESOURCE,
            ErrorCode.INVALID_ARGS,
            "정리를 내지 않았다. 아래 줄을 고쳐 한 번 더 부른다. " + " / ".join(result.rejected),
        )
    if not result.kept:
        return fail(
            "draft",
            RESOURCE,
            ErrorCode.EVIDENCE_REQUIRED,
            "조회 결과의 ref 를 단 줄이 없다. 조회한 기록의 ref 를 그대로 달아 다시 부른다.",
        )

    state = context.state
    lines = sorted(result.kept, key=lambda line: line.date)  # 같은 날은 쓴 순서 그대로
    body = "\n".join([*_warning(context), *(_render(line) for line in lines), *_safety(context)])
    refs = tuple(dict.fromkeys(state.seen_refs[line.ref] for line in lines))
    state.readouts[:] = [
        r for r in state.readouts if not (r.kind == KIND and r.authored_by == "model")
    ]
    state.readouts.append(Readout(kind=KIND, body=body, authored_by="model", source_refs=refs))
    return ok("draft", RESOURCE, lines=len(lines), dropped=len(result.dropped))


def _render(line: TimelineLine) -> str:
    """줄 앞에 날짜를 붙인다. 모델이 이미 그 날짜로 시작했으면 겹쳐 쓰지 않는다."""
    day = _md(line.date)
    zero_padded = f"{line.date.month:02d}/{line.date.day:02d}"
    if line.text.startswith((day, zero_padded)):
        return line.text
    return f"{day} {line.text}"


def _md(day: date) -> str:
    return f"{day.month}/{day.day}"


def _unavailable(context: HealthContext) -> bool:
    """안전 정보를 못 읽었다. 게이트가 비어 있으면 읽었는지 모르는 것이라 같이 본다."""
    gate = context.state.gate
    return gate is None or not gate.safety_ok


def _warning(context: HealthContext) -> list[str]:
    # "아래 정리는 그것 없이 만든 거예요" — 그래서 맨 위다
    return [READOUTS.render(SAFETY_UNAVAILABLE).body] if _unavailable(context) else []


def _safety(context: HealthContext) -> list[str]:
    """등록된(active) 알레르기 · 만성질환 원문. 못 읽었으면 줄을 만들지 않는다."""
    if _unavailable(context):
        return []
    active = [entry for entry in context.state.safety_entries if entry.status == "active"]
    return [
        READOUTS.render(
            CARD_ALLERGY, items=_items(e.label for e in active if e.kind == "allergy")
        ).body,
        READOUTS.render(
            CARD_CHRONIC, items=_items(e.label for e in active if e.kind == "chronic_disease")
        ).body,
    ]


def _items(labels: Iterable[str]) -> str:
    """등록한 이름 원문을 이어 붙인다. 하나도 없으면 "등록된 내용이 없어요" — 사실이다."""
    items = list(labels)
    return ", ".join(items) if items else READOUTS.render(CARD_EMPTY).body
