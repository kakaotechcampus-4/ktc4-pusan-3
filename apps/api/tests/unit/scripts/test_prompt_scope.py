"""바뀐 파일 → 돌릴 테스트 매핑 (`make test-prompt` · pre-push 훅이 같이 쓴다, #770)."""

import pytest

from app.agents import prompt_registry
from scripts.prompt_scope import FINGERPRINT_TESTS, SCOPES, changed_files, select_tests

API = "apps/api/"


def test_프롬프트_10곳이_전부_범위를_가진다():
    assert {scope.name for scope in SCOPES} == set(prompt_registry.PROMPT_NAMES)


def test_범위가_가리키는_테스트_경로가_실제로_있다():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    targets = [target for scope in SCOPES for target in scope.tests]
    missing = [
        target
        for target in targets
        if not (list(root.glob(target)) if "*" in target else (root / target).exists())
    ]
    assert missing == []


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ("app/agents/supervisor/prompt.py", "tests/unit/agents/supervisor"),
        ("app/agents/supervisor/schemas.py", "tests/unit/agents/supervisor"),
        ("app/agents/memory/prompt.py", "tests/unit/agents/memory"),
        ("app/agents/memory/schemas/tool_defs.py", "tests/unit/agents/memory"),
        ("app/agents/memory/schemas/observation.py", "tests/unit/agents/memory"),
        ("app/agents/food/prompt.py", "tests/unit/agents/food"),
        ("app/agents/activity/prompt.py", "tests/unit/agents/activity"),
        (
            "app/agents/activity/event_schedule.py",
            "tests/unit/agents/activity/test_activity_event_schedule.py",
        ),
        ("app/agents/growth/schemas/books.py", "tests/unit/agents/growth"),
        ("app/agents/health/labels/medication.py", "tests/unit/agents/health"),
        ("app/agents/curator/embedding/judge.py", "tests/unit/agents/curator"),
        ("app/agents/curator/embedding/jev.py", "tests/unit/agents/curator"),
        ("app/agents/memory/agent.py", "tests/unit/agents/memory"),
        ("app/agents/common/tool_loop.py", "tests/unit/agents/food"),
        (
            "app/providers/meal_plan/image_ocr.py",
            "tests/unit/providers/test_meal_plan_image_ocr*.py",
        ),
        ("app/providers/meal_plan/schema.py", "tests/unit/providers/test_meal_plan_image_ocr*.py"),
        ("app/providers/notice/extractor.py", "tests/unit/providers/test_notice_*.py"),
        ("app/core/notice_extract.py", "tests/unit/providers/test_notice_*.py"),
        # 채점 판정 테스트도 같이 돈다 — 프롬프트 · 스키마가 바뀌면 판정이 읽는 칸이 바뀔 수 있다
        ("app/providers/meal_plan/schema.py", "tests/unit/scripts/test_meal_ocr_eval.py"),
        ("app/providers/notice/extractor.py", "tests/unit/scripts/test_notice_fixture_eval.py"),
    ],
)
def test_바뀐_파일에_맞는_범위_테스트가_고른다(changed, expected):
    assert expected in select_tests([API + changed])


def test_지문_고정_테스트는_항상_들어간다():
    assert set(FINGERPRINT_TESTS) <= set(select_tests([]))
    assert set(FINGERPRINT_TESTS) <= set(select_tests([API + "app/agents/food/prompt.py"]))


@pytest.mark.parametrize(
    "changed",
    [
        "docs/agents/eval/FOOD_EVAL.md",
        "apps/api/app/agents/common/prompt_fingerprint.py",
        "apps/api/app/agents/common/tool_schema.py",
        "apps/api/app/core/prompt_hash.py",
        "apps/api/app/agents/prompt_registry.py",
    ],
)
def test_지문_규칙이나_평가_문서가_바뀌어도_지문_테스트를_돈다(changed):
    assert set(FINGERPRINT_TESTS) <= set(select_tests([changed]))


def test_프롬프트와_상관없는_파일은_범위를_넓히지_않는다():
    assert select_tests(["apps/web/src/app/page.tsx", "README.md"]) == list(FINGERPRINT_TESTS)


def test_한_파일이_여러_범위에_걸리면_중복_없이_모은다():
    result = select_tests(
        [API + "app/agents/food/prompt.py", API + "app/agents/food/schemas/tool_defs.py"]
    )

    assert len(result) == len(set(result))


def test_Windows_경로_구분자도_같다():
    assert "tests/unit/agents/food" in select_tests([r"apps\api\app\agents\food\prompt.py"])


def test_변경_파일은_커밋_범위와_작업_트리를_합친다(monkeypatch):
    calls = []

    def fake_git(*args):
        calls.append(args)
        return {"diff": "a.py\nb.py", "work": "b.py\nc.py"}["diff" if "..." in args[-1] else "work"]

    monkeypatch.setattr("scripts.prompt_scope._git", fake_git)

    assert changed_files("origin/develop") == ["a.py", "b.py", "c.py"]


def test_git_을_못_읽으면_종료_코드_3_이다(monkeypatch):
    """pytest 는 수집 오류에 2 를 낸다.

    훅이 2 를 "못 읽음" 으로 보고 push 를 통과시키면 안 된다.
    """
    import subprocess

    from scripts import prompt_scope

    def broken(*_args):
        raise subprocess.CalledProcessError(128, ["git"], stderr="fatal: bad revision")

    monkeypatch.setattr(prompt_scope, "_git", broken)

    assert prompt_scope.main(["--run"]) == prompt_scope.EXIT_NO_DIFF == 3


def test_pytest_종료_코드는_그대로_돌려준다(monkeypatch):
    from scripts import prompt_scope

    monkeypatch.setattr(prompt_scope, "changed_files", lambda base: [])
    monkeypatch.setattr(prompt_scope.subprocess, "call", lambda *a, **k: 2)

    assert prompt_scope.main(["--run"]) == 2
