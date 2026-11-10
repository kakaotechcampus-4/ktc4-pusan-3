"""CI 가 돌릴 Agent 고르기 (#816) — 지문 · eval 파일이 바뀐 Agent 만, LLM 은 부르지 않는다."""

import json

from tests.eval.agents.ci_select import (
    AGENTS,
    TEST_FILES,
    cache_key,
    changed_agents,
    eval_hash,
    main,
    touched_agents,
)

HEAD = {a: a * 16 for a in AGENTS}


def test_지문이_바뀐_Agent_만_고른다():
    assert changed_agents(HEAD, {**HEAD, "food": "old"}, touched=()) == ["food"]


def test_지문이_다_같고_eval_도_그대로면_아무것도_안_고른다():
    assert changed_agents(HEAD, HEAD, touched=()) == []


def test_그_Agent_eval_파일이_바뀌면_고른다():
    assert touched_agents(["apps/api/tests/eval/agents/health/cases.py"]) == {"health"}
    assert changed_agents(HEAD, HEAD, touched={"health"}) == ["health"]


def test_공용_파일이_바뀌면_전부():
    assert touched_agents(["apps/api/tests/eval/agents/tally.py"]) == set(AGENTS)


def test_Agent_와_무관한_파일은_고르지_않는다():
    assert touched_agents(["apps/api/app/api/v1/routers/auth.py", "README.md"]) == set()


def test_base_지문이_없으면_다섯_Agent_다_고른다():
    assert changed_agents(HEAD, None, touched=()) == list(AGENTS)


def test_캐시_키():
    assert cache_key("food", "ab" * 32, "c" * 12) == f"tool-calls-food-{'ab' * 6}-{'c' * 12}"


def test_eval_해시는_그_Agent_파일이_바뀌면_달라진다(tmp_path):
    (tmp_path / "food").mkdir()
    (tmp_path / "health").mkdir()
    for name in ("tally.py", "conftest.py", "run_meta.py"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "food" / "a.py").write_text("1", encoding="utf-8")
    (tmp_path / "health" / "a.py").write_text("1", encoding="utf-8")
    before_food, before_health = eval_hash("food", tmp_path), eval_hash("health", tmp_path)

    (tmp_path / "food" / "a.py").write_text("2", encoding="utf-8")

    assert eval_hash("food", tmp_path) != before_food
    assert eval_hash("health", tmp_path) == before_health
    assert len(before_food) == 12


def test_테스트_파일은_Agent_마다_하나씩이다():
    assert set(TEST_FILES) == set(AGENTS)


def test_CLI_는_base_지문_파일이_없으면_다섯_Agent_를_낸다(tmp_path, capsys):
    assert main(["--base-prints", str(tmp_path / "없는파일.json")]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["agents"] == list(AGENTS)
    assert [row["agent"] for row in out["matrix"]] == list(AGENTS)
    assert all(row["key"].startswith("tool-calls-") and row["tests"] for row in out["matrix"])


def test_CLI_는_지문이_같으면_빈_목록을_낸다(tmp_path, capsys):
    from app.agents.prompt_registry import all_fingerprints

    base = tmp_path / "base.json"
    base.write_text(
        json.dumps({k: v.sha256 for k, v in all_fingerprints().items()}), encoding="utf-8"
    )

    assert main(["--base-prints", str(base)]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out == {"agents": [], "matrix": []}
