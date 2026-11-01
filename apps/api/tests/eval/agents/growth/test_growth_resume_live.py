"""루틴 되묻기 → 답 → 추천 — 실제 모델로 세 번 이어 돈다 (#654).

    unset PYTEST_ADDOPTS   # PowerShell: Remove-Item Env:PYTEST_ADDOPTS
    TEST_REPEAT=3 uv run pytest tests/eval/agents/growth/test_growth_resume_live.py -m live -s

기록이 없는 아이(C48-empty)에게 루틴을 물으면 Growth 가 되묻고(1턴), 그 질문에 보호자가 답하면
Memory 가 물은 행동의 기록으로 저장한 뒤 Growth 가 같은 요청을 이어서 돌고(2턴), 같은 요청을
다시 보내면 같은 질문이 돌아오지 않는다(3턴).
운영 입구는 러너가 부르는 `entrypoint.handle_input` 이다. DB 에 닿는 포트만 InMemory 로 바꾼다.

보는 것은 코드로 판정되는 것뿐이다. 1턴은 답을 받는 질문(`awaits_reply`)이고, 2턴은 질문이 없고
추천이 나가거나(수준을 읽었다) 코드 문구로 닫히며(사슬 끝 · 수준을 못 읽음), 저장한 행동 이름이
물은 행동(양치)이다. 3턴은 질문이 없다. 답 문장은 전부 지어낸 것이다(실제 보호자 발화 없음).
"""

import os
import time
from datetime import datetime
from typing import Any

import pytest

from app.agents import entrypoint
from app.agents.growth.readouts import LEVEL_UNREAD, NEXT_STEP_CHAIN_END, READOUTS
from app.agents.growth.store.inmemory import InMemoryGrowthLog, InMemorySafety
from app.agents.memory.schemas.task import PendingMemoryContext
from app.agents.pipeline import AgentQuestion, AgentResult, PendingReply
from app.agents.suggestion_save import InMemorySuggestions
from tests.eval.agents.growth import world as world_module
from tests.eval.agents.growth.world import (
    CHILD,
    KST,
    PARENT,
    WORLDS,
    birth_date_for,
    measurements_for,
    seed_store,
)

TEST_REPEAT = int(os.getenv("TEST_REPEAT", "1"))
# 아이 기록이 섞이지 않은 순수 요청이다. "양치를 아직 잘 못해서 걱정이야" 가 들어가면
# Supervisor 가 기록 + 요청으로 나눠 Memory 가 먼저 저장하고, Growth 는 되묻지 않고 바로
# 추천한다(그것도 맞는 동작이다)
REQUEST = "양치 혼자 하게 하고 싶어"
AGAIN = "양치 가르치는 방법 알려줘"

ANSWERS = (
    "치약을 묻혀서 칫솔질을 하는데 하기 싫어해",
    "혼자 칫솔질은 하는데 마무리는 내가 해줘",
    "아직 내가 다 해줘",
    "말해주면 혼자 해",
    "시키지 않아도 혼자 해",
    "앞니만 대충 닦고 말아",
    "칫솔 물고 장난만 쳐",
    "손 잡아줘야 겨우 해",
)
CLOSINGS = {READOUTS.get(NEXT_STEP_CHAIN_END).template, READOUTS.get(LEVEL_UNREAD).template}


async def _no_commit() -> None:
    """InMemoryStore 라 확정할 것이 없다."""


async def _turn(
    raw: str, *, store: Any, spec: Any, run_id: str, continuation: PendingMemoryContext | None
) -> list[Any]:
    birth = birth_date_for(spec.months, datetime.now(KST).date())
    events: list[Any] = []
    await entrypoint.handle_input(
        child_id=CHILD,
        parent_id=PARENT,
        raw_text=raw,
        run_id=run_id,
        birth_date=birth,
        child_health_granted=spec.consent,
        emit=events.append,
        continuation=continuation,
        store=store,
        commit=_no_commit,
        save_suggestions=InMemorySuggestions(),
        growth_safety=InMemorySafety(spec.safety, fail=spec.safety_fails),
        growth_log=InMemoryGrowthLog(measurements_for(spec.measurements, birth=birth)),
    )
    return events


def _of(events: list[Any], kind: type) -> list[Any]:
    return [event for event in events if isinstance(event, kind)]


@pytest.mark.live
@pytest.mark.parametrize("answer", ANSWERS)
async def test_되묻기에_답하면_추천까지_잇고_같은_질문을_되풀이하지_않는다(
    answer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(KST)
    monkeypatch.setattr(world_module, "NOW", now)
    monkeypatch.setattr(world_module, "TODAY", now.date())
    spec = WORLDS["C48-empty"]
    broken: list[str] = []
    for attempt in range(1, TEST_REPEAT + 1):
        store = await seed_store(spec)
        failures: list[str] = []

        started = time.perf_counter()
        first = await _turn(
            REQUEST, store=store, spec=spec, run_id=f"r1-{attempt}", continuation=None
        )
        (question,) = _of(first, AgentQuestion) or [None]
        pending = _of(first, PendingReply)
        if question is None or not question.awaits_reply or len(pending) != 1:
            failures.append("1턴: 답을 받는 질문이 아니다")
            broken += [f"#{attempt} {item}" for item in failures]
            continue

        second = await _turn(
            answer,
            store=store,
            spec=spec,
            run_id=f"r2-{attempt}",
            continuation=pending[0].context,
        )
        cards = _of(second, AgentResult)
        if _of(second, AgentQuestion):
            failures.append("2턴: 질문이 또 나갔다")
        if not cards:
            failures.append("2턴: Growth 가 이어서 돌지 않았다(답이 저장되지 않았다)")
        else:
            card = cards[0]
            closed = {r.body for r in card.readouts} & CLOSINGS
            if not card.suggestion_ids and not closed:
                failures.append(f"2턴: 추천도 코드 문구도 없다 (status={card.status})")
        rows = await store.query_observations(domain="routine", child_id=CHILD)
        names = [row.fields.get("subject") for row in rows]
        if any(name != "양치" for name in names):
            failures.append(f"2턴: 저장한 행동 이름이 물은 행동이 아니다 {names}")

        third = await _turn(
            AGAIN, store=store, spec=spec, run_id=f"r3-{attempt}", continuation=None
        )
        asked_again = bool(_of(third, AgentQuestion))
        readable = bool(cards) and bool(
            cards[0].suggestion_ids
            or {r.body for r in cards[0].readouts} & {READOUTS.get(NEXT_STEP_CHAIN_END).template}
        )
        if readable and asked_again:
            failures.append("3턴: 같은 요청에 같은 질문이 또 나왔다")

        shown = [(c.status, len(c.suggestion_ids)) for c in cards]
        elapsed = int((time.perf_counter() - started) * 1000)
        print(
            f"\n[{answer[:16]} #{attempt}] {'FAIL' if failures else 'PASS'} {elapsed}ms "
            f"2턴 카드={shown} 3턴 되묻기={asked_again}"
        )
        for failure in failures:
            print(f"    실패: {failure}")
        broken += [f"#{attempt} {item}" for item in failures]
    assert not broken, f"{answer}: " + " / ".join(broken)
