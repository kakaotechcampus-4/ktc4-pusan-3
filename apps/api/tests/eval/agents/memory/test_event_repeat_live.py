"""요일 반복 일정 라이브 eval (#738) — Memory 단독.

실제 모델이 반복 인자(`repeat_weekdays` · `repeat_until`)와 범위(`scope`)를 올바르게 고르는지 잰다.
틀리면 잘못된 날짜의 일정이 승인 앞에 놓이거나, 반복 일정 한 회차만 고쳐진다.

  uv run pytest tests/eval/agents/memory/test_event_repeat_live.py -m live -s

Memory 만 돈다 — 케이스가 routing 정답(`routing_cases`)과 짝이 아니라서 solo 모드에서만 잰다.
저장은 InMemoryStore 에 시드한 묶음(월 · 수 08:30, 오늘부터 28일)으로 한다.
"""

import asyncio
from collections.abc import Callable
from datetime import time, timedelta

import pytest

from app.agents.memory.context import AgentContext
from app.agents.memory.drafts import EventDraft, SeriesDraft
from tests.eval.agents.memory.test_memory import (
    CHILD,
    EVAL_MODELS,
    KST,
    MEMORY_MODE,
    RETRY_BUDGET,
    TODAY,
    EvalCase,
    _client,
    _judge,
    _snapshot,
    _weekday,
)

pytestmark = pytest.mark.live

_UNTIL = TODAY + timedelta(days=40)
_NEXT_MONDAY = _weekday("월", weeks=1)


async def _seed_commute_series(context: AgentContext) -> None:
    await context.store.create_series(
        child_id=CHILD,
        title="등원",
        weekdays=(1, 3),
        starts_on=TODAY,
        until=TODAY + timedelta(days=28),
        start_time=time(8, 30),
        end_time=None,
        tz=KST,
        fields={"event_type": "core", "category": "institution"},
        item_names=("물통",),
    )


def _series(snapshot) -> SeriesDraft:
    [draft] = snapshot.drafts
    assert isinstance(draft, SeriesDraft)
    return draft


CASES: list[EvalCase] = [
    EvalCase(
        "R1",
        f"다음 주 월요일부터 {_UNTIL.month}월 {_UNTIL.day}일까지 월요일 수요일마다 "
        "아침 8시 반에 등원이야. 물통 챙겨야 해.",
        # 날짜마다 따로 부르지 않고 한 번에, 준비물도 같은 호출의 items 로
        required_tools={"create_event": 1},
        forbidden_tools={"create_event_item"},
        checks=(
            ("초안이 하나다", lambda s: len(s.drafts) == 1),
            ("반복 초안이다", lambda s: getattr(s.drafts[0], "repeat", None) is not None),
            ("월 · 수 요일이다", lambda s: s.drafts[0].repeat.weekdays == (1, 3)),
            ("끝 날짜가 말한 날이다", lambda s: s.drafts[0].repeat.until == _UNTIL),
            (
                "첫 회차가 다음 주 월요일 08:30 이다",
                lambda s: s.starts_at().startswith(f"{_NEXT_MONDAY}T08:30"),
            ),
            ("core 일정이다", lambda s: s.drafts[0].event_type == "core"),
            ("물통이 있다", lambda s: any("물통" in name for name in s.item_names)),
        ),
    ),
    EvalCase(
        "R2",
        "다음 주부터 월요일 수요일마다 아침 8시 반에 등원 알림 만들어줘.",
        # 끝 날짜를 안 말했다 — 정해 넣지 않고 하나만 묻는다
        expect_clarification=True,
        checks=(
            ("초안을 만들지 않았다", lambda s: s.drafts == ()),
            (
                "끝 날짜를 물었다",
                lambda s: s.said(("언제까지", "끝나는", "마지막", "종료", "끝 날짜")),
            ),
        ),
    ),
    EvalCase(
        "R3",
        "등원 시간을 9시로 바꿔줘.",
        seed=_seed_commute_series,
        # 날짜 없이 그 반복 일정 자체를 말했다 — 묶음 전체
        required_tools={"query_event": 1, "update_event": 1},
        forbidden_tools={"create_event", "delete_event"},
        checks=(
            (
                "묶음 초안이 하나다",
                lambda s: len(s.drafts) == 1 and isinstance(s.drafts[0], SeriesDraft),
            ),
            ("시작 시각이 09:00 이다", lambda s: _series(s).starts_time == time(9, 0)),
            ("바뀐 필드가 starts_time 뿐이다", lambda s: _series(s).changed == ("starts_time",)),
        ),
    ),
    EvalCase(
        "R4",
        "수요일 등원은 9시로 바꿔줘.",
        seed=_seed_commute_series,
        # 날짜를 집어 말했다 — 그 회차 하나만. 묶음 초안이 아니라 보통 수정 초안이다
        required_tools={"query_event": 1, "update_event": 1},
        forbidden_tools={"create_event", "delete_event"},
        checks=(
            (
                "수정 초안이 하나다",
                lambda s: (
                    len(s.drafts) == 1
                    and isinstance(s.drafts[0], EventDraft)
                    and s.drafts[0].op == "update"
                ),
            ),
            (
                "그 수요일 09:00 이다",
                lambda s: s.starts_at().startswith(f"{_weekday('수')}T09:00"),
            ),
        ),
    ),
    EvalCase(
        "R6",
        "등원 준비물에 실내화도 추가해줘.",
        seed=_seed_commute_series,
        # 날짜 없이 반복 일정 자체를 말했다 — 묶음 초안에 준비물 이름을 더한다
        required_tools={"query_event": 1, "create_event_item": 1},
        forbidden_tools={"create_event"},
        checks=(
            (
                "묶음 초안이 하나다",
                lambda s: len(s.drafts) == 1 and isinstance(s.drafts[0], SeriesDraft),
            ),
            ("실내화가 더해졌다", lambda s: any("실내화" in name for name in _series(s).items)),
            ("물통이 남았다", lambda s: any("물통" in name for name in _series(s).items)),
        ),
    ),
]


def _expected_text(case: EvalCase) -> str:
    parts = []
    if case.required_tools:
        parts.append(
            " · ".join(f"{tool} {count}회 이상" for tool, count in case.required_tools.items())
        )
    if case.forbidden_tools:
        parts.append(f"금지 {' · '.join(sorted(case.forbidden_tools))}")
    if case.expect_clarification:
        parts.append("되묻는다")
    parts.extend(label for label, _ in case.checks)
    parts.append(f"tool 거절은 {RETRY_BUDGET}회까지 (고쳐 부르면 통과)")
    return " / ".join(parts)


@pytest.mark.skipif(MEMORY_MODE != "solo", reason="반복 일정 케이스는 solo 모드에서만 잰다")
@pytest.mark.parametrize("model", EVAL_MODELS or [None], ids=lambda m: m or "env")
@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_event_repeat_eval(case: EvalCase, model: str | None, expect: Callable[..., None]) -> None:
    expect(_expected_text(case))
    snapshot = asyncio.run(_snapshot(case, _client(model)))
    failures = _judge(case, snapshot)

    print(
        f"\n[{case.case_id}] {'PASS' if not failures else 'FAIL'} "
        f"steps={snapshot.result.steps} tools={snapshot.result.tool_names}"
    )
    print(f"    message={snapshot.result.final_message!r}")
    for failure in failures:
        print(f"    - {failure}")
    assert not failures, f"{case.case_id}: " + " / ".join(failures)
