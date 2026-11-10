"""바뀐 프롬프트 파일에 맞춰 LLM 없는 테스트를 골라 돌린다 (#770).

    cd apps/api
    uv run python -m scripts.prompt_scope                       # 돌릴 테스트만 출력
    uv run python -m scripts.prompt_scope --run                 # 골라서 pytest 까지
    uv run python -m scripts.prompt_scope --base origin/develop # 기준 브랜치 (기본값)

`make test-prompt` 와 선택형 `.githooks/pre-push` 가 같은 매핑을 쓴다 (`SCOPES` 한 곳).
CI 는 이 스크립트를 쓰지 않는다 — PR 마다 전체 pytest(#753 `ci-api.yml`)가 돌고, 지문 고정
테스트도 그 안에 있다. 프롬프트 경로에만 반응하는 워크플로를 필수 체크로 두면, 그 파일을 안 건드린
PR 이 영원히 기다린다.

라이브 eval(`-m live`)은 돌리지 않는다. pytest 기본값(`-m 'not live'`)이 그대로 적용된다.
"""

import argparse
import fnmatch
import glob
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "origin/develop"
# 바뀐 파일을 못 읽었을 때의 종료 코드. pytest 가 쓰는 0~5 와 겹치지 않게 한다 — 훅은 이 값일 때만
# push 를 막지 않는다(수집 오류는 pytest 의 2 라 그대로 막힌다)
EXIT_NO_DIFF = 3

# 어떤 파일이 바뀌었든 늘 돈다 — 1초 남짓이고, 평가 문서 · 지문 규칙이 바뀐 경우도 여기서 잡힌다
FINGERPRINT_TESTS: tuple[str, ...] = (
    "tests/unit/agents/common/test_prompt_hash.py",
    "tests/unit/agents/common/test_prompt_fingerprint.py",
    "tests/unit/agents/common/test_prompt_history.py",
)


# tool 루프의 재시도 문구는 food · activity · growth · health 의 지문에 들어간다
_TOOL_LOOP = "apps/api/app/agents/common/tool_loop.py"


@dataclass(frozen=True)
class Scope:
    name: str  # `PROMPT_NAMES` 의 이름
    paths: tuple[
        str, ...
    ]  # 저장소 루트 기준 글롭 — 프롬프트 · tool spec · 응답 스키마를 이루는 파일
    tests: tuple[str, ...]  # apps/api 기준 pytest 경로 (글롭 가능)


def _agent(name: str, *extra_paths: str, tests: tuple[str, ...] | None = None) -> Scope:
    base = f"apps/api/app/agents/{name}"
    return Scope(
        name=name,
        paths=(f"{base}/prompt.py", f"{base}/schemas/*", *extra_paths),
        tests=tests or (f"tests/unit/agents/{name}", f"tests/eval/agents/{name}"),
    )


SCOPES: tuple[Scope, ...] = (
    Scope(
        name="supervisor",
        paths=(
            "apps/api/app/agents/supervisor/prompt.py",
            "apps/api/app/agents/supervisor/schemas.py",
        ),
        tests=("tests/unit/agents/supervisor", "tests/eval/agents/supervisor"),
    ),
    _agent("memory", "apps/api/app/agents/memory/agent.py"),
    _agent("food", _TOOL_LOOP),
    _agent("activity", _TOOL_LOOP),
    Scope(
        name="activity_event_schedule",
        paths=("apps/api/app/agents/activity/event_schedule.py",),
        tests=("tests/unit/agents/activity/test_activity_event_schedule.py",),
    ),
    _agent("growth", _TOOL_LOOP),
    # 일반 정보 답변 (#574) — 세 Agent 가 같이 쓰는 뼈대. 지금 쓰는 Agent 는 Growth 다
    Scope(
        name="doc_answer",
        paths=(
            "apps/api/app/agents/common/doc_answer.py",
            "apps/api/app/agents/common/doc_answer.readout.yaml",
        ),
        tests=(
            "tests/unit/agents/common/test_doc_answer.py",
            "tests/unit/agents/growth/test_growth_info.py",
        ),
    ),
    _agent("health", "apps/api/app/agents/health/labels/*", _TOOL_LOOP),
    Scope(
        name="curator_judge",
        paths=(
            "apps/api/app/agents/curator/embedding/judge.py",
            "apps/api/app/agents/curator/embedding/jev.py",
        ),
        tests=("tests/unit/agents/curator", "tests/eval/agents/curator/test_pairs_data.py"),
    ),
    Scope(
        name="meal_ocr",
        paths=(
            "apps/api/app/providers/meal_plan/image_ocr.py",
            "apps/api/app/providers/meal_plan/schema.py",
        ),
        tests=(
            "tests/unit/providers/test_meal_plan_image_ocr*.py",
            "tests/unit/scripts/test_meal_ocr_eval.py",
        ),
    ),
    Scope(
        name="notice_ocr",
        paths=(
            "apps/api/app/providers/notice/extractor.py",
            "apps/api/app/core/notice_extract.py",
        ),
        tests=("tests/unit/providers/test_notice_*.py",),
    ),
)


def _matches(changed: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(changed, pattern) for pattern in patterns)


def select_tests(changed: Iterable[str]) -> list[str]:
    """바뀐 파일(저장소 루트 기준)에 맞는 pytest 경로. 지문 고정 테스트는 항상 들어간다."""
    files = [path.replace("\\", "/") for path in changed]
    targets: list[str] = list(FINGERPRINT_TESTS)
    for scope in SCOPES:
        if any(_matches(path, scope.paths) for path in files):
            targets.extend(scope.tests)
    return list(dict.fromkeys(targets))  # 순서를 지키며 중복을 뺀다


def _git(*args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=API_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return done.stdout


def changed_files(base: str = DEFAULT_BASE) -> list[str]:
    """기준 브랜치와 갈라진 뒤 바뀐 파일(커밋된 것 + 작업 트리). 저장소 루트 기준 경로."""
    committed = _git("diff", "--name-only", f"{base}...HEAD").split()
    working = _git("diff", "--name-only", "HEAD").split()
    return list(dict.fromkeys([*committed, *working]))


def expand(targets: Sequence[str]) -> list[str]:
    """글롭을 풀어 실제 경로로. 셸 없이 pytest 를 부르므로 우리가 푼다."""
    expanded: list[str] = []
    for target in targets:
        matches = sorted(glob.glob(str(API_DIR / target)))
        expanded.extend(str(Path(match).relative_to(API_DIR)) for match in matches)
    return expanded


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=DEFAULT_BASE, help="비교할 기준 브랜치")
    parser.add_argument("--run", action="store_true", help="고른 테스트를 pytest 로 돌린다")
    args = parser.parse_args(argv)

    try:
        changed = changed_files(args.base)
    except subprocess.CalledProcessError as exc:
        print(f"git 으로 바뀐 파일을 못 읽었습니다 (기준 {args.base}): {exc.stderr.strip()}")
        print("기준 브랜치를 못 찾았으면 `git fetch origin develop` 뒤 다시 하세요.")
        return EXIT_NO_DIFF

    targets = expand(select_tests(changed))
    if not args.run:
        print("\n".join(targets))
        return 0

    print(f"바뀐 파일 {len(changed)}개 → 테스트 {len(targets)}곳")
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *targets], cwd=API_DIR)  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
