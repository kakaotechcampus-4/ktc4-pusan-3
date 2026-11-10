"""Curator 판정기 라이브 측정 — 측정 이력(`CURATOR_JUDGE_EVAL.md` §1) 칸 값을 낸다 (#816). 유료.

    uv run python -m tests.eval.agents.tally --reset     # 앞 측정이 남아 있으면 먼저
    TEST_REPEAT=5 uv run pytest tests/eval/agents/curator/test_judge_live.py -m live -s

측정셋(`judge_cases.py`)의 물음마다 회차 수만큼 판정기를 부르고 `tally` 에 C1 · 일반으로 남긴다.
끝나면 conftest 가 측정 이력에 옮길 한 줄과 실행 메타(`live_run_meta.json`)를 남긴다.
틀린 물음의 후보 · 기대 · 회차별 답은 `results/judge_live/` 에 적는다(gitignore). 이름은 모두
가상 데이터라 적어도 된다. tally 기록에는 입력 id 와 실패 종류만 넣는다.

🚨 한 세션은 전부 남기거나 하나도 안 남긴다. 판정기 호출이 하나라도 끝내 실패하면 회차를 남기지 않고
실패한다 — 일부만 남긴 세션에서도 측정 이력 한 줄이 나오는데, 그 줄은 분모가 다르다.
일시 오류(시간 초과 · rate limit · 서버 오류)는 몇 번 다시 묻고,
계정 오류(`STOP_ERRORS`) · 예상하지 못한 예외는 남은 물음을 부르지 않고 멈춘다.

🚨 회차는 한 세션 안에서 `TEST_REPEAT` 로 돈다. 앞 측정의 판정기 회차가 결과 파일에 남아
있으면 부르기 전에 실패한다 — tally 는 같은 회차 번호면 나중 것이 이겨서, 남은 회차와 섞이면
다른 프롬프트의 회차가 이번 줄에 들어가거나, C1 이 난 회차를 다시 돌려 C1 을 지울 수 있다
(§2 "일부 실행에서만 나온 오류도 빼지 않는다"). 다른 세션 번호(`EVAL_ATTEMPT`)로 이어 붙이는
것도 막는다 — 어느 프롬프트로 잰 회차인지 결과 파일이 기억하지 않기 때문이다.
"""

import asyncio
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agents.common.config import get_agent_settings
from app.agents.common.llm_client import (
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.agents.curator.embedding.jev import JevJudge, missing_settings
from app.agents.curator.embedding.judge import STOP_ERRORS
from tests.eval.agents import tally
from tests.eval.agents.curator.datasets import results_dir
from tests.eval.agents.curator.judge_cases import (
    JudgeCase,
    Verdict,
    condition,
    grade,
    load_cases,
)

pytestmark = pytest.mark.live

SUITE = "curator_judge"  # 프롬프트 이름과 같아야 실행 메타에서 지문 · 모델을 찾는다
TEST_REPEAT = int(os.getenv("TEST_REPEAT", "1"))
CONCURRENCY = 4  # 실험 3 과 같다
RETRIES = 2  # 일시 오류일 때 더 묻는 횟수
RETRY_WAIT_S = 2.0
TRANSIENT = (LLMTimeoutError, LLMRateLimitError, LLMUnavailableError)

Answer = tuple[JudgeCase, int, str | None, str | None]  # (물음, 회차, 답, 오류 종류)


async def _ask(
    judge: JevJudge, case: JudgeCase, stopped: list[str]
) -> tuple[str | None, str | None]:
    for attempt in range(RETRIES + 1):
        if stopped:
            return None, f"멈춤({stopped[0]})"
        try:
            answer = await judge.judge(
                subject=case.subject, domain=case.domain, candidates=case.candidates
            )
        except STOP_ERRORS as exc:
            stopped.append(type(exc).__name__)
            return None, type(exc).__name__
        except TRANSIENT as exc:
            if attempt == RETRIES:
                return None, type(exc).__name__
            await asyncio.sleep(RETRY_WAIT_S * (attempt + 1))
        except LLMError as exc:  # 형식 오류 · 요청 거절 — 다시 물어도 같다
            return None, type(exc).__name__
        except Exception as exc:  # 코드 문제일 수 있다 — 유료 호출을 더 이어가지 않는다
            stopped.append(type(exc).__name__)
            return None, type(exc).__name__
        else:
            return answer.choice, None
    raise AssertionError("unreachable")


async def test_판정기_측정셋() -> None:
    settings = get_agent_settings()
    if missing := missing_settings(settings):
        pytest.skip(f"{' / '.join(missing)} 가 필요합니다. apps/api/.env 를 확인하세요.")
    left = sorted({row.attempt for row in tally.load() if row.suite == SUITE})
    if left:
        pytest.fail(
            f"앞 측정의 판정기 회차 {left} 가 결과 파일에 남아 있다. 이어 붙이면 회차가 섞인다 — "
            "`uv run python -m tests.eval.agents.tally --reset` 뒤 다시 돌린다."
        )
    judge = JevJudge(settings)
    cases = load_cases()
    gate = asyncio.Semaphore(CONCURRENCY)
    stopped: list[str] = []

    async def one(case: JudgeCase, inner: int) -> Answer:
        async with gate:
            choice, error = await _ask(judge, case, stopped)
        return case, inner, choice, error

    answers = await asyncio.gather(
        *(one(case, inner) for inner in range(1, TEST_REPEAT + 1) for case in cases)
    )
    errors = Counter(error for *_, error in answers if error)
    if errors:
        pytest.fail(
            f"판정기 호출 {sum(errors.values())}건이 실패해 이번 세션은 회차를 남기지 않았다 "
            f"({dict(errors)}). 다시 돌린다."
        )

    graded: list[tuple[JudgeCase, int, str, Verdict, str]] = []
    for case, inner, choice, _ in answers:
        assert choice is not None
        verdict, reason = grade(case, choice)
        graded.append((case, inner, choice, verdict, reason))
        tally.mark(
            SUITE,
            case.id,
            inner=inner,
            c1=[reason] if verdict == "c1" else [],
            general=[reason] if verdict == "general" else [],
        )
    report = _report(graded)
    print(f"\n판정기 측정 · 틀린 물음 {report}")
    print(
        f"측정 이력 조건 칸: {condition(cases, TEST_REPEAT, settings.CURATOR_JUDGE_MODEL.strip())}"
    )

    c1 = sorted({case.id for case, *_, verdict, _ in graded if verdict == "c1"})
    assert not c1, f"다른 대상을 같다고 판정한 물음 {len(c1)}개 ({report}): {c1}"


def _report(graded: list[tuple[JudgeCase, int, str, Verdict, str]]) -> Path:
    """틀린 회차가 있는 물음마다 이름 · 후보 · 기대 · 회차별 답을 적는다."""
    by_case: dict[str, list[tuple[JudgeCase, int, str, Verdict, str]]] = {}
    for row in graded:
        by_case.setdefault(row[0].id, []).append(row)
    lines = [f"# 판정기 측정 · {len(by_case)}물음 × {TEST_REPEAT}회", ""]
    for case_id, rows in by_case.items():
        if all(verdict == "pass" for *_, verdict, _ in rows):
            continue
        case = rows[0][0]
        c1 = sum(verdict == "c1" for *_, verdict, _ in rows)
        general = sum(verdict == "general" for *_, verdict, _ in rows)
        said = " · ".join(
            f"{inner}:{choice}" for _, inner, choice, *_ in sorted(rows, key=lambda r: r[1])
        )
        lines.append(
            f"{case_id} [{case.domain}] {case.subject} → {list(case.candidates)}"
            f" 기대 {case.expected}"
            f" | C1 {c1} · 일반 {general} / {len(rows)} | 답 {said}"
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = results_dir("judge_live") / f"judge_live_{stamp}_result.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
