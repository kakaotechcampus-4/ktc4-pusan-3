"""진료 준비 출력 tool — `compose_symptom_timeline`.

모델이 고른 key 의 줄(조회 tool 이 코드로 그려 둔 `state.visit_lines`)을 날짜순으로 묶어
`Readout(model, kind="symptom_timeline")` 을 state 에 둔다. 아무것도 저장하지 않는다.

- 모델은 문장을 쓰지 않는다 (#551). 그래서 해석 표현 · 병 이름 · 약 이름 · 용량을 사후에 거를 일이
  없다 — 줄의 글은 기록 원문 · 등록값이고 원문은 그릴 때 이미 걸렀다(`visit_lines.py`)
- 이번 run 이 조회하지 않은 key 는 조용히 지운다. 남은 줄이 없으면 `EVIDENCE_REQUIRED` —
  근거 없는 정리를 내지 않는다
- 정렬은 코드 몫이다 — 날 → 구획(증상 · 먹인 약 · 복약 체크 · 식사) → 조회한 순서
- 알레르기 · 만성질환 줄은 코드가 `state.safety_entries` 원문으로 붙인다. 조회에 실패했거나
  게이트가 비어 있으면 줄 대신 `safety.unavailable` 경고를 맨 위에 둔다 — 읽지 못한 것을
  "등록된 내용이 없어요" 로 쓰지 않는다
- 같은 run 에 다시 부르면 앞의 정리를 교체한다
"""

from collections.abc import Iterable

from app.agents.common.readout import Readout
from app.agents.health.context import HealthContext, VisitLine, VisitSection
from app.agents.health.readouts import (
    CARD_ALLERGY,
    CARD_CHRONIC,
    CARD_EMPTY,
    READOUTS,
    SAFETY_UNAVAILABLE,
)
from app.agents.health.result import ErrorCode, ToolResult, fail, ok
from app.agents.health.schemas.records import ComposeTimelineArgs

RESOURCE = "compose_symptom_timeline"
KIND = "symptom_timeline"

# 같은 날 안의 순서
_SECTION_ORDER: dict[VisitSection, int] = {"symptom": 0, "given": 1, "medication": 2, "meal": 3}


async def compose_symptom_timeline(context: HealthContext, args: ComposeTimelineArgs) -> ToolResult:
    state = context.state
    order = {key: index for index, key in enumerate(state.visit_lines)}  # 조회한 순서
    picked = [key for key in dict.fromkeys(args.keys) if key in state.visit_lines]
    dropped = len(dict.fromkeys(args.keys)) - len(picked)
    if not picked:
        return fail(
            "draft",
            RESOURCE,
            ErrorCode.EVIDENCE_REQUIRED,
            "조회 결과의 key 가 하나도 없다. 조회 tool 을 먼저 부르고 "
            "그 결과의 key 를 그대로 넘긴다.",
        )

    def position(key: str) -> tuple:
        line = state.visit_lines[key]
        return line.day, _SECTION_ORDER[line.section], order[key]

    lines: list[VisitLine] = [state.visit_lines[key] for key in sorted(picked, key=position)]
    body = "\n".join([*_warning(context), *(line.body for line in lines), *_safety(context)])
    refs = tuple(dict.fromkeys(ref for line in lines for ref in line.refs))
    state.readouts[:] = [
        r for r in state.readouts if not (r.kind == KIND and r.authored_by == "model")
    ]
    state.readouts.append(Readout(kind=KIND, body=body, authored_by="model", source_refs=refs))
    return ok("draft", RESOURCE, lines=len(lines), dropped=dropped)


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
