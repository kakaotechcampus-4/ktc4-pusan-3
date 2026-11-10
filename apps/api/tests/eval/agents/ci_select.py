"""CI 가 돌릴 라이브 eval Agent 를 고른다 (#816) — LLM 은 부르지 않는다.

워크플로(`.github/workflows/ci-agent-live.yml`)의 `select` job 이 부른다:

    uv run python -m tests.eval.agents.ci_select --base-prints base.json --base-sha <sha>

base(PR 이 갈라진 쪽)의 프롬프트 지문과 지금 지문을 견줘서 바뀐 Agent 를 고른다. 지문은 같아도 그
Agent 의 eval 파일(케이스 · 판정)이 바뀌었으면 고른다 — 지문만 보면 새 금지 tool 이 한 번도 안 돈다.
캐시 키는 `(Agent, 지문, eval 파일 해시)` 다 (PM 11-09) — 같은 값이면 이미 통과한 것이라 건너뛴다.

base 지문을 못 구하면(`--base-prints` 가 없거나 JSON 이 아니면) 다섯 Agent 를 다 고른다.
조용히 아무것도 안 고르면 초록불이 되기 때문이다.
"""

import argparse
import hashlib
import json
import os
import subprocess
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parents[4]
EVAL_PREFIX = "apps/api/tests/eval/agents"

AGENTS: tuple[str, ...] = ("memory", "food", "activity", "growth", "health")

# `apps/api` 기준. CI 가 돌릴 pytest 파일 — `-m "live and tool_ci"` 로 그 안의 CI 케이스만 돈다
TEST_FILES: dict[str, str] = {
    "memory": "tests/eval/agents/memory/test_memory.py",
    "food": "tests/eval/agents/food/test_food_run.py",
    "activity": "tests/eval/agents/activity/test_activity_live.py",
    "growth": "tests/eval/agents/growth/test_growth_live.py",
    "health": "tests/eval/agents/health/test_health_run.py",
}

# 모든 Agent 의 회차 · 판정에 걸리는 공용 파일 — 바뀌면 다섯 Agent 를 다 돌린다
SHARED_FILES: tuple[str, ...] = ("tally.py", "conftest.py", "run_meta.py", "tool_rules.py")
# 한 Agent 만 쓰는 공용 파일
OWNED_FILES: dict[str, str] = {"routing_cases.py": "memory"}
# eval 폴더 밖에 있지만 그 Agent 가 채점받는 입력을 정하는 파일 (저장소 루트 기준)
EXTRA_FILES: dict[str, tuple[str, ...]] = {
    "memory": (f"{EVAL_PREFIX}/test_input.txt",),
    "growth": (
        "apps/api/tests/unit/agents/growth/support.py",
        "docs/agents/growth/test_input_growth.txt",
    ),
    "health": ("docs/agents/health/test_input_health.txt",),
}


def eval_hash(agent: str, root: Path = EVAL_DIR) -> str:
    """그 Agent 의 eval 파일 해시(앞 12자) — `<agent>/**/*.py` 와 공용 파일을 경로순으로 이어서."""
    names = [root / name for name in SHARED_FILES]
    names += [
        path for path, owner in ((root / n, o) for n, o in OWNED_FILES.items()) if owner == agent
    ]
    names += sorted((root / agent).rglob("*.py"))
    digest = hashlib.sha256()
    for path in sorted(set(names), key=lambda p: p.relative_to(root).as_posix()):
        if not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    for extra in EXTRA_FILES.get(agent, ()):
        path = REPO_ROOT / extra
        if path.is_file():
            digest.update(extra.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def touched_agents(changed_paths: Iterable[str]) -> set[str]:
    """바뀐 파일(저장소 루트 기준)이 가리키는 Agent. 공용 파일이면 전부."""
    found: set[str] = set()
    for raw in changed_paths:
        path = raw.replace("\\", "/")
        for agent, extras in EXTRA_FILES.items():
            if path in extras:
                found.add(agent)
        if not path.startswith(f"{EVAL_PREFIX}/"):
            continue
        rest = path.removeprefix(f"{EVAL_PREFIX}/")
        if rest in SHARED_FILES:
            return set(AGENTS)
        if rest in OWNED_FILES:
            found.add(OWNED_FILES[rest])
        elif rest.split("/", 1)[0] in AGENTS:
            found.add(rest.split("/", 1)[0])
    return found


def changed_agents(
    head: Mapping[str, str], base: Mapping[str, str] | None, *, touched: Collection[str]
) -> list[str]:
    """지문이 바뀌었거나 eval 이 바뀐 Agent(`AGENTS` 순서). base 를 모르면 전부."""
    if base is None:
        return list(AGENTS)
    return [a for a in AGENTS if head.get(a) != base.get(a) or a in touched]


def cache_key(agent: str, sha256: str, hashed: str, *, condition: str = "") -> str:
    """캐시 키. `condition` 은 모델/effort — repo `vars` 를 바꾸면 같은 지문이어도 다시 돈다."""
    key = f"tool-calls-{agent}-{sha256[:12]}-{hashed}"
    if condition:
        key += "-" + hashlib.sha256(condition.encode()).hexdigest()[:8]
    return key


def _condition(agent: str) -> str:
    """그 Agent 역할의 `<ROLE>_MODEL` · `<ROLE>_REASONING_EFFORT` (워크플로가 vars 를 넘긴다)."""
    role = agent.upper()
    model, effort = os.getenv(f"{role}_MODEL", ""), os.getenv(f"{role}_REASONING_EFFORT", "")
    return f"{model}/{effort}" if model or effort else ""


def _load_base(path: Path | None) -> dict[str, str] | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _diff_paths() -> list[str]:
    """`git diff` 가 볼 경로 — eval 폴더와, 그 밖에 있는 입력 파일(`EXTRA_FILES`)."""
    extras = {path for paths in EXTRA_FILES.values() for path in paths}
    return [EVAL_PREFIX, *sorted(extras)]


def _git_touched(base_sha: str) -> set[str]:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", "diff", "--name-only", base_sha, "HEAD", "--", *_diff_paths()],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return set(AGENTS)  # 못 구하면 전부 — 조용히 건너뛰지 않는다
    return touched_agents(done.stdout.splitlines())


def main(argv: Sequence[str] | None = None) -> int:
    from app.agents.prompt_registry import all_fingerprints

    parser = argparse.ArgumentParser(description="CI 가 돌릴 라이브 eval Agent 고르기 (#816)")
    parser.add_argument("--base-prints", type=Path, help="base 의 프롬프트 지문 JSON")
    parser.add_argument("--base-sha", help="eval 파일 변경을 볼 base 커밋")
    args = parser.parse_args(argv)

    prints = {name: print_.sha256 for name, print_ in all_fingerprints().items()}
    touched = _git_touched(args.base_sha) if args.base_sha else set()
    agents = changed_agents(prints, _load_base(args.base_prints), touched=touched)
    matrix = [
        {
            "agent": agent,
            "key": cache_key(agent, prints[agent], eval_hash(agent), condition=_condition(agent)),
            "tests": TEST_FILES[agent],
        }
        for agent in agents
    ]
    print(json.dumps({"agents": agents, "matrix": matrix}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
