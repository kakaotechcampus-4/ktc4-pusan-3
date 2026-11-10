"""평가 문서의 측정 이력 — 프롬프트를 바꿨는데 행을 안 더했거나 C1 이 늘었으면 실패한다 (#770).

문서: `docs/agents/eval/<NAME>_EVAL.md` 11개. 마지막 행의 지문이 지금 프롬프트 지문(앞 12자)과
같아야 하고, 같은 `조건` 의 직전 행보다 C1 이 늘면 안 된다.
라이브 eval 은 돌리지 않는다 — 표만 읽는다.
"""

import pytest

from app.agents import prompt_registry
from tests.unit.agents.common.prompt_history import (
    DOC_DIR,
    check_history,
    doc_path,
    parse_history,
)

SHA = "0123456789ab"
OTHER = "ba9876543210"

HEADER = (
    "## 1. 측정 이력\n\n"
    "| 버전 | 커밋 | 지문 | 조건 | 무엇을 왜 바꿨나 | C1 | 안정 오답 | 불안정 |\n"
    "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
)


def _doc(*rows: str, before: str = "# 문서\n\n", after: str = "\n## 2. 다음 절\n") -> str:
    return before + HEADER + "\n".join(rows) + "\n" + after


def _row(version="v0", sha=SHA, cond="셋 · 모델/low · R5", c1="미측정", note="최초 기준선") -> str:
    return f"| {version} | `abc1234` | `{sha}` | {cond} | {note} | {c1} | 미측정 | 미측정 |"


# ── 지금 저장소의 문서 ────────────────────────────────────────────────


@pytest.mark.parametrize("name", prompt_registry.PROMPT_NAMES)
def test_문서가_있고_마지막_행_지문이_지금_프롬프트와_같다(name):
    path = doc_path(name)
    assert path.exists(), (
        f"{name}: {path} 가 없습니다. 프롬프트마다 평가 문서가 한 장 있어야 합니다."
    )

    current = prompt_registry.all_fingerprints()[name].short
    assert check_history(name, path.read_text(encoding="utf-8"), current) == []


def test_평가_문서는_프롬프트_수와_같다():
    docs = sorted(p.name for p in DOC_DIR.glob("*_EVAL.md"))
    assert docs == sorted(doc_path(name).name for name in prompt_registry.PROMPT_NAMES)


# ── 검사 규칙 ───────────────────────────────────────────────────────


def test_마지막_행_지문이_지금과_같으면_통과다():
    assert check_history("food", _doc(_row()), SHA) == []


def test_프롬프트가_바뀌었는데_행을_안_더하면_실패하고_현재_지문을_알려_준다():
    [error] = check_history("food", _doc(_row()), OTHER)

    assert error.startswith("food: 프롬프트가 마지막 측정 뒤 바뀌었습니다.")
    assert OTHER in error
    assert "FOOD_EVAL.md" in error


def test_새_지문_행을_더하면_통과다():
    doc = _doc(
        _row(),
        _row("v1", SHA, c1="0/10", note="바꾸기 전 프롬프트로 잼"),
        _row("v2", OTHER, c1="0/10", note="문구를 고침"),
    )

    assert check_history("food", doc, OTHER) == []


def test_문서_표가_깨졌으면_따로_실패한다():
    broken = _doc("| v0 | `abc1234` | `" + SHA + "` | 칸이 모자람 |")

    [error] = check_history("food", broken, SHA)
    assert "표가 깨졌습니다" in error


def test_측정_이력_절이_없으면_실패한다():
    [error] = check_history("food", "# 문서\n\n내용 없음\n", SHA)

    assert "측정 이력" in error


def test_측정_행이_하나도_없으면_실패한다():
    [error] = check_history("food", _doc(), SHA)

    assert "행이 없습니다" in error


def test_지문_칸이_12자_16진수가_아니면_실패한다():
    errors = check_history("food", _doc(_row(sha="not-a-hash")), "not-a-hash")

    assert any("지문" in error and "16진수" in error for error in errors)


def test_v0_다음_행의_C1_은_미측정일_수_없다():
    doc = _doc(_row(), _row("v1", SHA, c1="미측정"))

    [error] = check_history("food", doc, SHA)
    assert "v1" in error and "C1" in error


def test_C1_칸은_a_b_형식이어야_한다():
    doc = _doc(_row(), _row("v1", SHA, c1="많음"))

    [error] = check_history("food", doc, SHA)
    assert "a/b" in error


def test_같은_조건에서_C1_이_늘면_실패한다():
    doc = _doc(_row("v1", c1="1/10"), _row("v2", OTHER, c1="2/10"))

    [error] = check_history("food", doc, OTHER)
    assert "1/10 → 2/10" in error


def test_C1_은_비율로_비교한다_분모가_커져_건수만_늘면_통과다():
    doc = _doc(_row("v1", c1="2/10"), _row("v2", OTHER, c1="3/60"))

    assert check_history("food", doc, OTHER) == []


def test_C1_은_비율로_비교한다_분모가_작아져_비율이_늘면_실패다():
    doc = _doc(_row("v1", c1="2/10"), _row("v2", OTHER, c1="1/2"))

    [error] = check_history("food", doc, OTHER)
    assert "2/10 → 1/2" in error


def test_분모가_0_이면_형식_오류다():
    doc = _doc(_row(), _row("v1", SHA, c1="0/0"))

    [error] = check_history("food", doc, SHA)
    assert "a/b" in error


def test_같은_조건에서_C1_이_같거나_줄면_통과다():
    same = _doc(_row("v1", c1="1/10"), _row("v2", OTHER, c1="1/10"))
    fewer = _doc(_row("v1", c1="1/10"), _row("v2", OTHER, c1="0/10"))

    assert check_history("food", same, OTHER) == []
    assert check_history("food", fewer, OTHER) == []


def test_조건을_바꾸면_바꾸기_전_프롬프트를_새_조건으로_다시_잰_행과_비교한다():
    doc = _doc(
        _row("v1", c1="0/10", cond="셋 · 모델/low · R5"),
        _row("v2", SHA, c1="1/10", cond="셋 · 모델/high · R5", note="조건만 바꿔 다시 잼"),
        _row("v3", OTHER, c1="1/10", cond="셋 · 모델/high · R5"),
    )

    assert check_history("food", doc, OTHER) == []


def test_조건만_바꿔_같은_프롬프트를_다시_잰_행은_비교_대상이_없어도_통과다():
    doc = _doc(
        _row("v1", c1="0/10", cond="셋 · 모델/low · R5"),
        _row("v2", SHA, c1="3/10", cond="셋 · 모델/high · R5", note="조건만 바꿔 다시 잼"),
    )

    assert check_history("food", doc, SHA) == []


def test_기준선이_미측정이면_바꾸기_전_행_없이_새_행만_더하면_실패한다():
    doc = _doc(_row(), _row("v1", OTHER, c1="2/10"))

    [error] = check_history("food", doc, OTHER)
    assert "바꾸기 전" in error


def test_프롬프트를_바꾼_행의_조건이_이전_측정과_다르면_실패한다():
    doc = _doc(
        _row("v1", c1="0/10", cond="셋 · 모델/low · R5"),
        _row("v2", OTHER, c1="0/10", cond="셋 · 모델/high · R5"),
    )

    [error] = check_history("food", doc, OTHER)
    assert "바꾸기 전" in error


def test_C1_정의가_미결인_문서는_미결_로_적을_수_있다():
    doc = _doc(
        _row(),
        _row("v1", SHA, c1="미결", note="바꾸기 전 프롬프트로 잼"),
        _row("v2", OTHER, c1="미결", note="문구를 고침"),
        before="# 문서\n\n<!-- c1: 미결 -->\n\n",
    )

    assert check_history("meal_ocr", doc, OTHER) == []


def test_미결_문서도_프롬프트를_바꾼_행은_바꾸기_전_행이_있어야_한다():
    doc = _doc(
        _row(),
        _row("v1", OTHER, c1="미결", note="문구를 고침"),
        before="# 문서\n\n<!-- c1: 미결 -->\n\n",
    )

    [error] = check_history("meal_ocr", doc, OTHER)
    assert "바꾸기 전" in error


def test_미결_표시가_없는_문서는_미결_을_쓸_수_없다():
    doc = _doc(_row(), _row("v1", SHA, c1="미결"))

    [error] = check_history("food", doc, SHA)
    assert "미결" in error


def test_비교는_같은_조건의_가장_가까운_측정_행과_한다():
    doc = _doc(
        _row("v1", c1="0/10"),
        _row("v2", c1="9/10", cond="다른 조건"),  # 건너뛴다
        _row("v3", OTHER, c1="1/10"),
    )

    [error] = check_history("food", doc, OTHER)
    assert "0/10 → 1/10" in error and "v1 → v3" in error


def test_측정_이력_밖의_다른_표는_읽지_않는다():
    doc = _doc(_row(), after="\n## 2. 다음\n\n| v9 | `x` | `y` | z |\n")

    assert parse_history(doc)[-1].version == "v0"


def test_CRLF_문서도_읽는다():
    doc = _doc(_row()).replace("\n", "\r\n")

    assert check_history("food", doc, SHA) == []


@pytest.mark.parametrize("commit", ["nothing", "zzzzzzz", "abc12", "abc1234 깨끗"])
def test_커밋_칸은_커밋_해시여야_한다(commit):
    row = f"| v0 | `{commit}` | `{SHA}` | 셋 · 모델/low · R5 | 최초 | 미측정 | 미측정 | 미측정 |"

    [error] = check_history("food", _doc(row), SHA)
    assert "커밋" in error


@pytest.mark.parametrize("commit", ["`abc1234`", "`abc1234` dirty", "`" + "a" * 40 + "`"])
def test_커밋_칸은_해시와_dirty_표시를_받는다(commit):
    row = f"| v0 | {commit} | `{SHA}` | 셋 · 모델/low · R5 | 최초 | 미측정 | 미측정 | 미측정 |"

    assert check_history("food", _doc(row), SHA) == []


@pytest.mark.parametrize(("cond", "note"), [("", "메모"), ("조건", "")])
def test_조건과_메모는_비울_수_없다(cond, note):
    row = f"| v0 | `abc1234` | `{SHA}` | {cond} | {note} | 미측정 | 미측정 | 미측정 |"

    [error] = check_history("food", _doc(row), SHA)
    assert "비었습니다" in error
