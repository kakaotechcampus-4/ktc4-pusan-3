"""Growth 라이브 eval 결과를 사람이 읽는 글과 드라이런 지표로 바꾼다 (growth_plan §11-1 · §11-2).

    uv run python -m tests.eval.agents.growth.report                    # 기본 결과 파일
    uv run python -m tests.eval.agents.growth.report a.jsonl b.jsonl    # 여러 번 돌린 것을 합쳐서

입력은 test_growth_live_results.jsonl(케이스 · 반복마다 한 줄). 출력은 같은 폴더의
report_result.txt(.gitignore 의 *_result.txt)와 화면 요약이다. 모델을 부르지 않는다.

지표 (드라이런 — TEST_REPEAT 로 모델 run 20건 이상)
  문서 행 적중률  교육 · 루틴 run 중 일반 템플릿(doc.no_row · rhythm.no_row)으로
                  안 내려간 비율. ≥ 0.7
  평가 표현       fail 의 평가 표현(공통1) 0건 · 넓은 낱말 note 건수(사람 확인)
  p95            모델을 부른 run 의 걸린 시간 95 분위. < 15초
  골든           FAIL 0 (GAP 은 다른 plan 몫이라 통과로 센다) — G1
도서 연령 필터 정확도는 도서 eval(plan2)이 같은 자리를 채운다. 종료 코드는 게이트를 넘으면 0.
"""

import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
DEFAULT = HERE / "test_growth_live_results.jsonl"
OUT = HERE / "report_result.txt"
DOC_HIT_TARGET = 0.7
P95_TARGET_MS = 15_000


def load(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    hits = [r["doc_hit"] for r in records if r.get("doc_hit") is not None]
    model_runs = [r for r in records if r["model_calls"] > 0]
    golden = [r for r in records if r["golden"]]
    return {
        "runs": len(records),
        "outcomes": dict(Counter(r["outcome"] for r in records)),
        "doc_hit_rate": (sum(hits) / len(hits)) if hits else None,
        "doc_hit_n": len(hits),
        "evaluative_fail": sum(1 for r in records for f in r["failures"] if f.startswith("공통1")),
        "wide_notes": sum(1 for r in records for n in r["notes"] if n.startswith("넓은 목록")),
        "p95_ms": p95([r["ms"] for r in model_runs]),
        "model_runs": len(model_runs),
        "recall_rate": (
            sum(1 for r in model_runs if r["model_calls"] == 2) / len(model_runs)
            if model_runs
            else None
        ),
        "golden_pass": sum(1 for r in golden if r["outcome"] != "FAIL"),
        "golden_n": len(golden),
        "chat_calls": sum(r["chat_calls"] for r in records),
        "tokens": {
            key: sum(r["tokens"].get(key, 0) for r in records)
            for key in ("prompt_tokens", "completion_tokens")
        },
    }


def passed(m: dict[str, Any]) -> bool:
    return (
        m["evaluative_fail"] == 0
        and m["golden_pass"] == m["golden_n"]
        and (m["doc_hit_rate"] is None or m["doc_hit_rate"] >= DOC_HIT_TARGET)
        and (m["p95_ms"] is None or m["p95_ms"] < P95_TARGET_MS)
    )


def summary(m: dict[str, Any]) -> list[str]:
    def rate(value: float | None) -> str:
        return "-" if value is None else f"{value:.2f}"

    outcomes = " · ".join(f"{k} {v}" for k, v in sorted(m["outcomes"].items()))
    return [
        f"run {m['runs']}건 ({outcomes}) · 모델 run {m['model_runs']}건",
        f"골든 {m['golden_pass']}/{m['golden_n']} (FAIL 아님)",
        f"평가 표현(공통1) {m['evaluative_fail']}건 · "
        f"넓은 낱말 note {m['wide_notes']}건(사람 확인)",
        f"문서 행 적중률 {rate(m['doc_hit_rate'])} ({m['doc_hit_n']}건, 목표 ≥ {DOC_HIT_TARGET})",
        f"p95 {m['p95_ms'] if m['p95_ms'] is not None else '-'}ms (목표 < {P95_TARGET_MS})",
        f"재호출 비율 {rate(m['recall_rate'])} · 모델 대화 {m['chat_calls']}번 · 토큰 "
        f"입력 {m['tokens']['prompt_tokens']} 출력 {m['tokens']['completion_tokens']}",
        f"게이트 {'통과' if passed(m) else '못 넘음'}",
    ]


def render(records: list[dict[str, Any]], m: dict[str, Any]) -> str:
    lines = ["Growth 라이브 eval 리포트", "", *summary(m), "", "── 골든 ──"]
    lines += [
        f"  {r['outcome']:4}  {r['case']} #{r['attempt']}  모델 {r['model_calls']}회 {r['ms']}ms"
        for r in records
        if r["golden"]
    ]
    lines += ["", "── 케이스 ──"]
    for r in records:
        lines.append(f"[{r['case']} #{r['attempt']}] {r['outcome']}  {r['request']}")
        lines.append(f"    기대  {r['expect']}")
        tools = " · ".join(r["tools"]) or "-"
        lines.append(
            f"    status={r['status']} 모델 {r['model_calls']}회 · 대화 {r['chat_calls']}번 · "
            f"{r['ms']}ms · tool {tools}"
        )
        for s in r["suggestions"]:
            lines.append(f"    · [{s['kind']}] {s['content'].replace(chr(10), ' / ')}")
            lines.append(f"      이유  {s['reason']}")
            lines += [f"      근거  {c['kind']} {c['label']} — {c['note']}" for c in s["citations"]]
            if s["items"]:
                lines.append(f"      준비물  {', '.join(s['items'])}")
        lines += [f"    ▸ {x['body'].replace(chr(10), ' / ')}" for x in r["readouts"]]
        if r["question"]:
            lines.append(f"    ? {r['question']}")
        for label, key in (("실패", "failures"), ("미구현", "gaps"), ("참고", "notes")):
            lines += [f"    {label}: {item}" for item in r[key]]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    paths = [Path(p) for p in (sys.argv[1:] if argv is None else argv)] or [DEFAULT]
    records = load(paths)
    m = metrics(records)
    OUT.write_text(render(records, m), encoding="utf-8")
    encoding = sys.stdout.encoding or "utf-8"
    for line in summary(m):
        print(line.encode(encoding, "replace").decode(encoding))
    print(f"→ {OUT}")
    return 0 if passed(m) else 1


if __name__ == "__main__":
    raise SystemExit(main())
