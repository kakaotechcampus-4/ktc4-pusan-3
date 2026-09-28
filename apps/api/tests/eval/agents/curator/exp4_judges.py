"""동일 대상 판정기 비교 (실험 4). 선정용만 쓴다 — 검증용은 실험 3 에서 이미 썼다.

실행 (apps/api 에서, 유료):
    uv run python -m tests.eval.agents.curator.exp4_judges --judge gemini-flash-lite --live
    uv run python -m tests.eval.agents.curator.exp4_judges --judge jev --live   # OPENROUTER_API_KEY

조건은 실험 3 의 exp3_flow.py 후보 선택 평가와 같다.
    - 과제: exp3_flow.cases — 선정용 쌍마다 원래 순서 · 후보 역순 · 정답 후보 제거
    - 판정 규칙 문장: exp4_judge_models.PROMPTS[--prompt] (v1 = exp3_flow.PROMPT)
    - 이름이 같은 후보 → exact, 후보 없음 → none 은 판정기를 부르지 않고 코드가 처리한다
    - confidence 게이트 없음
실험 3 과 다른 점: 같은 과제를 --repeat 번 반복해 호출 변동을 따로 본다.
API 오류는 중단하지 않고 형식 오류로 센다 (정답으로 세지 않는다).

결과는 이 폴더의 exp4_*_result.txt · exp4_*_results.jsonl (gitignore 대상).
"""

import argparse
import asyncio
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime

import httpx

from app.agents.curator.embedding.link_step import same_name_key
from tests.eval.agents.curator.datasets import RESULTS_DIR, TUNE_PAIRS_PATH, load_pairs
from tests.eval.agents.curator.exp3_flow import cases
from tests.eval.agents.curator.exp4_judge_models import (
    NONE,
    PROMPTS,
    UNCERTAIN,
    Decision,
    Judge,
    make_judge,
)

VARIANTS = ("original", "reversed", "absent")


async def _decide(judge: Judge, client, subject, domain, options) -> tuple[Decision, str]:
    for option in options:
        if same_name_key(subject) == same_name_key(option):
            return Decision(option), "exact"
    if not options:
        return Decision(NONE), "empty"
    return await judge.decide(client, subject=subject, domain=domain, candidates=options), "judge"


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * q) - 1)]


async def run(args: argparse.Namespace) -> None:
    tune = load_pairs(TUNE_PAIRS_PATH)
    tasks = list(cases(tune))
    print(f"선정용 {len(tune)}쌍 · 과제 {len(tasks)}건 × 반복 {args.repeat} · 검증용 미사용")
    if not args.live:
        print("DRY RUN. --live 를 지정해야 API 를 호출한다.")
        return

    judge = make_judge(args.judge, prompt=args.prompt, reasoning_effort=args.reasoning)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    tag = f"{args.judge}-{args.prompt}" + (f"-{args.reasoning}" if args.reasoning else "")
    log_path = RESULTS_DIR / f"exp4_{tag}_{stamp}_results.jsonl"
    report_path = RESULTS_DIR / f"exp4_{tag}_{stamp}_result.txt"
    semaphore = asyncio.Semaphore(args.concurrency)
    rows: list[dict] = []

    with log_path.open("x", encoding="utf-8") as log:
        log.write(
            json.dumps(
                {
                    "judge": judge.name,
                    "prompt_version": args.prompt,
                    "prompt": PROMPTS[args.prompt],
                    "repeat": args.repeat,
                    "tune_sha256": hashlib.sha256(TUNE_PAIRS_PATH.read_bytes()).hexdigest(),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        async with httpx.AsyncClient(timeout=60.0) as client:

            async def one(repeat: int, task) -> None:
                index, pair, variant, options, expected = task
                async with semaphore:
                    decision, via = await _decide(judge, client, pair.a, pair.domain, options)
                row = {
                    "repeat": repeat,
                    "index": index,
                    "variant": variant,
                    "domain": pair.domain,
                    "label": pair.label,
                    "subject": pair.a,
                    "options": options,
                    "expected": expected,
                    "choice": decision.choice,
                    "via": via,
                    "error": decision.error,
                    "model": decision.model,
                    "input_tokens": decision.input_tokens,
                    "output_tokens": decision.output_tokens,
                    "cost_usd": decision.cost_usd,
                    "latency_ms": round(decision.latency_ms, 1),
                }
                rows.append(row)
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
                log.flush()

            for repeat in range(args.repeat):
                await asyncio.gather(*(one(repeat, t) for t in tasks))
                print(f"반복 {repeat + 1}/{args.repeat} 완료", flush=True)

    lines = [
        f"# 동일 대상 판정 — {judge.name}",
        f"선정용 {len(tune)}쌍 · 과제 {len(tasks)}건 × 반복 {args.repeat} · 검증용 미사용",
        f"조건: 실험 3(exp3_flow) 과 같은 과제 · 판정 규칙 {args.prompt}",
        "exact · 빈 후보는 판정기를 부르지 않고 코드가 처리",
        "",
        "## 반복별 · 과제 종류별",
        "반복  종류      n    다중후보  잘못연결  놓침       보류  형식오류  정답",
    ]
    for repeat in range(args.repeat):
        for variant in VARIANTS:
            rs = [r for r in rows if r["repeat"] == repeat and r["variant"] == variant]
            positives = [r for r in rs if r["expected"] != NONE]
            false_link = sum(r["choice"] not in (r["expected"], NONE, UNCERTAIN, None) for r in rs)
            miss = sum(r["choice"] != r["expected"] for r in positives)
            multi = sum(len(r["options"]) > 1 for r in rs)
            lines.append(
                f"{repeat + 1:>4}  {variant:<8} {len(rs):>3}  {multi:>7}"
                f"  {false_link:>7}  {miss:>3}/{len(positives):<4}"
                f"  {sum(r['choice'] == UNCERTAIN for r in rs):>4}"
                f"  {sum(r['error'] is not None for r in rs):>7}"
                f"  {sum(r['choice'] == r['expected'] for r in rs):>3}/{len(rs)}"
            )

    # 같은 과제의 반복 사이 선택이 달라진 수 — 호출 자체의 변동
    by_task: dict[tuple, set] = defaultdict(set)
    for r in rows:
        if r["via"] == "judge":
            by_task[(r["index"], r["variant"])].add(r["choice"])
    unstable = [key for key, choices in by_task.items() if len(choices) > 1]
    # 같은 반복 안에서 원래 순서와 역순의 선택이 달라진 수 — 후보 순서의 영향
    order_flips = []
    for repeat in range(args.repeat):
        pick = {
            (r["index"], r["variant"]): r["choice"]
            for r in rows
            if r["repeat"] == repeat and r["variant"] in ("original", "reversed")
        }
        order_flips.append(
            sum(pick[(i, "original")] != pick[(i, "reversed")] for i in range(len(tune)))
        )
    lines += [
        "",
        "## 흔들림",
        f"반복 사이 선택이 달라진 판정 과제: {len(unstable)}/{len(by_task)}",
        f"원래 순서 ↔ 역순 선택이 다른 쌍 (반복별): {order_flips}",
    ]

    lines += ["", "## 틀린 판정 (반복 1)"]
    for r in rows:
        if r["repeat"] == 0 and r["choice"] != r["expected"]:
            lines.append(
                f"  {r['variant']:<8} {r['domain']:<9} {r['label']:<9} {r['subject']} → "
                f"{r['choice']} (기대 {r['expected']}) 후보 {r['options']}"
                + (f" 오류 {r['error']}" if r["error"] else "")
            )

    judged = [r for r in rows if r["via"] == "judge"]
    latency = [r["latency_ms"] for r in judged if r["error"] is None]
    tokens_in = sum(r["input_tokens"] for r in judged)
    tokens_out = sum(r["output_tokens"] for r in judged)
    reported = [r["cost_usd"] for r in judged if r["cost_usd"] is not None]
    lines += [
        "",
        "## 비용 · 속도",
        f"판정기 호출 {len(judged)}회 (exact · 빈 후보 {len(rows) - len(judged)}건은 호출 없음)",
        f"토큰 입력 {tokens_in} · 출력 {tokens_out} · 호출당 평균 "
        f"{tokens_in / max(len(judged), 1):.0f} / {tokens_out / max(len(judged), 1):.0f}",
    ]
    if reported:
        lines.append(f"응답이 보고한 비용 USD {sum(reported):.8f}")
    if args.price_in is not None and args.price_out is not None:
        cost = tokens_in / 1e6 * args.price_in + tokens_out / 1e6 * args.price_out
        price = f"입력 ${args.price_in} · 출력 ${args.price_out} / 1M"
        lines.append(f"단가로 계산한 비용 USD {cost:.8f} ({price})")
    if latency:
        lines.append(
            f"왕복 중앙값 {statistics.median(latency):.0f}ms · "
            f"p95 {_percentile(latency, 0.95):.0f}ms"
            f" (동시 {args.concurrency})"
        )
    errors = Counter(r["error"] for r in judged if r["error"])
    if errors:
        lines.append(f"오류: {dict(errors)}")
    models = Counter(r["model"] for r in judged if r["model"])
    lines.append(f"응답 모델: {dict(models)}")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n보고서: {report_path}\n상세: {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judge", required=True, choices=["gemini-flash-lite", "jev"])
    parser.add_argument("--prompt", default="v1", choices=["v1", "v2"])
    parser.add_argument(
        "--reasoning", default=None, help="ChatJudge reasoning_effort (예: minimal)"
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--price-in", type=float, default=None, help="USD / 1M 입력 토큰")
    parser.add_argument("--price-out", type=float, default=None, help="USD / 1M 출력 토큰")
    parser.add_argument("--live", action="store_true")
    asyncio.run(run(parser.parse_args()))
