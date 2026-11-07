"""W · 일주일 시나리오 — 실제 모델로 앱을 API 로 돌린다 (docs/eval/eval-v1.md §5).

    EVAL_ONLY=W-C make eval            # 가끔 쓰는 집 (4 입력)
    make eval                          # P 와 함께 A · C 를 돈다 (W-A ≈ 16 입력)

케이스마다 1회다 — 반복(k)은 아이 k 명을 같은 시각에 나란히 돌리는 방식인데 이번엔 k=1 이다.
`EVAL_TIER=smoke` 는 W 를 건너뛴다(EVAL_ONLY 로 케이스를 지정하면 돈다).
통합 테스트 DB(icatch_it)를 쓴다 — 메인 .env 를 환경변수로만 올리고 DB_NAME 만 바꿔 돌릴 것.

🚨 Curator 는 가짜 임베딩 · 가짜 판정기다 — 실제 묶음 품질은 보지 않는다 (eval/README.md).
결과: eval/results/raw/week-<tier>-<시각>.jsonl (gitignore). 한 줄 = 단계 하나(끝 상태 점검 포함).
토큰 합이 EVAL_MAX_TOKENS 를 넘으면 남은 단계를 건너뛴다.
"""

import json
import logging
import os
from collections.abc import Iterator
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.activity.store.inmemory import InMemoryWeather
from app.agents.activity.store.ports import RawForecast
from app.api.runs import runner
from app.core import llm_usage
from app.main import app
from tests.eval.suite.report import RAW_DIR
from tests.eval.suite.schema import WeekCase, load_weeks
from tests.eval.suite.test_probes_live import GIT
from tests.eval.suite.verdict import judge_check, judge_step, record_week
from tests.eval.suite.week_runner import (
    ContainsJudge,
    HashEmbedder,
    WeekDriver,
    reset_process_state,
    seed_world,
    simulation_monday,
    step_day,
)
from tests.eval.suite.world import KST

pytestmark = pytest.mark.live

TIER = os.getenv("EVAL_TIER", "full")
ONLY = os.getenv("EVAL_ONLY")
MAX_TOKENS = int(os.getenv("EVAL_MAX_TOKENS", "3000000"))
RAW_PATH = RAW_DIR / f"week-{TIER}-{datetime.now(KST):%Y%m%d-%H%M%S}.jsonl"
_HEAVY_RAIN = RawForecast(sky="4", pcp="30.0~50.0mm", pop="90")
_spent = {"tokens": 0}


def _selected() -> list[WeekCase]:
    weeks = load_weeks()
    if ONLY:
        picked = [w for w in weeks if w.id == ONLY]
        if not picked and not ONLY.startswith("P-"):
            raise ValueError(f"EVAL_ONLY={ONLY!r} 는 없는 W 케이스 id 다")
        return picked
    return [] if TIER == "smoke" else weeks


def _append(row: dict[str, object]) -> None:
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RAW_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps({**row, "git": GIT}, ensure_ascii=False) + "\n")


@pytest.fixture(scope="module", autouse=True)
def _usage_capture() -> Iterator[None]:
    llm_usage.enable()
    llm_usage.drain(limit=100_000)
    yield
    llm_usage.disable()


@pytest.mark.parametrize("case", _selected(), ids=lambda case: case.id)
async def test_week(
    case: WeekCase, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agents import entrypoint

    monkeypatch.setattr(entrypoint, "curator_deps", lambda: (HashEmbedder(), ContainsJudge()))

    original = runner.agent_job

    def attributed(**kwargs):  # type: ignore[no-untyped-def]
        job = original(**kwargs)
        parent_id = kwargs["parent_id"]

        async def run(channel):  # type: ignore[no-untyped-def]
            with llm_usage.attribute(parent_id=parent_id, run_id=channel.run_id, source="input"):
                await job(channel)

        return run

    monkeypatch.setattr(runner, "agent_job", attributed)

    caplog.set_level(logging.INFO)
    reset_process_state()
    monday = simulation_monday(datetime.now(KST).date())
    rainy = {step_day(monday, f"{day} 00:00"): _HEAVY_RAIN for day in case.world.rain_days}
    old_state = {
        k: getattr(app.state, k, None)
        for k in ("activity_weather", "activity_events", "health_places")
    }
    app.state.activity_weather = InMemoryWeather(forecasts=rainy, uv="4")
    app.state.activity_events = None
    app.state.health_places = None
    failures: list[str] = []
    try:
        child_id, logins, _seed_ids, stale = await seed_world(case, monday)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            driver = WeekDriver(
                case=case,
                client=client,
                monday=monday,
                child_id=child_id,
                logins=logins,
                stale_obs=stale,
                logs=lambda: [caplog.handler.format(r) for r in caplog.records],
            )
            for step in case.steps:
                if _spent["tokens"] >= MAX_TOKENS:
                    _append({"case": f"{case.id}:{step.tag}", "skipped": "token_cap", "repeat": 1})
                    failures.append(f"{step.tag}: 토큰 상한")
                    continue
                caplog.clear()
                outcome = await driver.run(step)
                trace = outcome.trace
                day = step_day(monday, step.at)
                if step.kind == "check":
                    verdict = judge_check(trace, misses=outcome.extra_misses)
                else:
                    verdict = judge_step(
                        trace,
                        step.expect,
                        allergies=case.world.allergies,
                        today=day,
                        extra_misses=outcome.extra_misses,
                    )
                _spent["tokens"] += trace.tokens
                same_day = sum(
                    1
                    for c, by, d in driver.inputs
                    if c == case.id and by == step.by and d == day.isoformat()
                )
                _append(
                    record_week(
                        case.id,
                        step,
                        trace,
                        verdict,
                        persona=case.persona,
                        day=day,
                        input_count=same_day,
                    )
                )
                if not verdict.passed:
                    reason = [str(v) for v in verdict.violations] + list(verdict.misses)
                    failures.append(f"{step.tag}: {reason[0]}")
    finally:
        for key, value in old_state.items():
            setattr(app.state, key, value)
    assert not failures, f"{case.id} 실패 {len(failures)}단계 — {failures[:5]}"
