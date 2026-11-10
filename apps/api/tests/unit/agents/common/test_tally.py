"""라이브 eval 회차 집계 (#816) — 측정 이력 칸 값과 CI 빨간불을 정한다.

LLM 은 부르지 않는다. 회차 기록(`Mark`)을 어떻게 입력별로 묶고 세는지만 본다.
"""

import json
from pathlib import Path

import pytest

from tests.eval.agents import tally
from tests.eval.agents.tally import (
    Mark,
    Tally,
    attempt_id,
    by_input,
    history_line,
    is_write_tool,
    load,
    main,
    mark,
    summarize,
    summary_markdown,
)

META = {
    "commit": "0123456789abcdef0123456789abcdef01234567",
    "dirty": False,
    "repetitions": 3,
    "models": {"food": {"model": "m-1", "reasoning_effort": "low"}},
    "prompts": {"food": "ab" * 32},
}


def M(suite, input_id, attempt, c1=(), general=(), tools=()):
    return Mark(suite, input_id, attempt, tuple(c1), tuple(general), tuple(tools))


def test_한_번이라도_C1_이면_그_입력은_C1_사례다():
    (t,) = summarize(
        [
            M("food", "T01", "1.1"),
            M("food", "T01", "2.1", c1=("공통2",)),
            M("food", "T02", "1.1"),
        ]
    )

    assert t.c1_cell == "1/2"


def test_전_회차_실패는_안정_오답_일부는_불안정():
    (t,) = summarize(
        [
            *(M("food", "T01", f"{i}.1", general=("x",)) for i in (1, 2, 3)),
            M("food", "T02", "1.1", general=("x",)),
            M("food", "T02", "2.1"),
            M("food", "T02", "3.1"),
        ]
    )

    assert (t.stable_wrong, t.unstable, t.repetitions) == (1, 1, 3)


def test_같은_suite_는_파일이_달라도_한_줄로_합친다():
    tallies = summarize(
        [M("growth", "A", "1.1"), M("growth", "B", "1.1"), M("health", "T01", "1.1")]
    )

    assert {t.suite: t.inputs for t in tallies} == {"growth": 2, "health": 1}


def test_같은_회차_id_가_다시_오면_나중_것으로_바꾼다(tmp_path):
    path = tmp_path / "results.jsonl"
    mark("food", "T01", c1=("옛",), path=path)
    mark("food", "T01", path=path)  # 같은 세션 번호 · 같은 안쪽 번호 — 다시 돌린 것

    (only,) = load(path)

    assert not only.failed


def test_측정_이력_줄은_8칸이고_사람_칸은_비워_둔다():
    line = history_line(Tally("food", 2, 1, 0, 1, 5), META)

    assert line.count("|") == 9
    assert "(무엇을 왜 바꿨나)" in line and "1/2" in line and "R5" in line
    assert "m-1/low" in line and "`" + "ab" * 6 + "`" in line


def test_dirty_면_커밋_옆에_dirty():
    clean = history_line(Tally("food", 1, 0, 0, 0, 3), META)
    dirty = history_line(Tally("food", 1, 0, 0, 0, 3), {**META, "dirty": True})

    assert "`01234567`" in clean and "dirty" not in clean
    assert "`01234567 dirty`" in dirty


def test_요약_표에_실패_문장이_없다():
    md = summary_markdown(
        by_input([M("food", "T01", "1.1", c1=("공통3 풀 밖 메뉴 (김치볶음밥)",), tools=("a",))]),
        META,
    )

    assert "김치볶음밥" not in md
    assert "C1" in md and "T01" in md


def test_요약_표는_통과한_회차와_판정과_tool_을_보인다():
    md = summary_markdown(
        by_input(
            [
                M("food", "T01", "1.1", tools=("lookup",)),
                M("food", "T01", "2.1", general=("x",), tools=("lookup", "delete_daycare_meal")),
            ]
        ),
        META,
    )

    assert "1/2" in md and "불안정" in md and "delete_daycare_meal" in md


def test_안정_오답이_있으면_fail_on_stable_은_1(tmp_path, monkeypatch):
    path = tmp_path / "results.jsonl"
    monkeypatch.setattr(tally, "RESULTS", path)
    for i in (1, 2, 3):
        mark("food", "T01", inner=1, general=("x",), path=path, session=i)

    assert main(["--fail-on", "stable"]) == 1


def test_불안정만_있으면_fail_on_stable_은_0(tmp_path, monkeypatch):
    path = tmp_path / "results.jsonl"
    monkeypatch.setattr(tally, "RESULTS", path)
    mark("food", "T01", general=("x",), path=path, session=1)
    mark("food", "T01", path=path, session=2)

    assert main(["--fail-on", "stable"]) == 0


def test_reset_은_결과_파일을_지운다(tmp_path, monkeypatch):
    path = tmp_path / "results.jsonl"
    monkeypatch.setattr(tally, "RESULTS", path)
    mark("food", "T01", path=path)

    assert main(["--reset"]) == 0
    assert not path.exists()


def test_결과_줄에는_요청도_답도_없다(tmp_path):
    path = tmp_path / "results.jsonl"
    mark("food", "T01", general=("x",), tools=("lookup",), path=path)

    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        "suite",
        "input_id",
        "attempt",
        "c1",
        "general",
        "tools",
    }


def test_stop_도_쓰기_tool_이다():
    assert is_write_tool("stop_medication_schedule")
    assert is_write_tool("propose_delete")
    assert not is_write_tool("show_dose_status")


@pytest.mark.parametrize(
    ("session", "inner", "expected"),
    [(None, 1, "1.1"), (2, 3, "2.3")],
)
def test_회차_id_는_세션_번호와_안쪽_번호를_합친다(monkeypatch, session, inner, expected):
    if session is None:
        monkeypatch.delenv("EVAL_ATTEMPT", raising=False)
    else:
        monkeypatch.setenv("EVAL_ATTEMPT", str(session))

    assert attempt_id(inner) == expected


def test_요약_파일에_덧붙인다(tmp_path, monkeypatch):
    path = tmp_path / "results.jsonl"
    monkeypatch.setattr(tally, "RESULTS", path)
    mark("food", "T01", path=path)
    out = Path(tmp_path / "summary.md")
    out.write_text("앞\n", encoding="utf-8")

    assert main(["--summary", str(out)]) == 0

    text = out.read_text(encoding="utf-8")
    assert text.startswith("앞\n") and "T01" in text
