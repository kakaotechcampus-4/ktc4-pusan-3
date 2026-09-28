"""고정 정책 Jev eval: tune 후보/순서/누락 → holdout 쌍/순차 연결.

실행 (apps/api 에서): uv run python -m tests.eval.agents.curator.exp3_flow --live
키: 환경변수 OPENROUTER_API_KEY. apps/api/.env 에 두면 서버 설정이 거부한다.
운영 DB 사용 없음. 후보 방해항은 해당 subject와 다르다고 라벨된 표현만 사용.
API 오류는 즉시 중단, 재시도 없음. 결과는 gitignored 파일에 즉시 기록.
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

from app.agents.curator.embedding.link_step import same_name_key
from tests.eval.agents.curator.datasets import (
    CHECK1_ORDERS_PATH,
    CHECK1_PAIRS_PATH,
    RESULTS_DIR,
    TUNE_PAIRS_PATH,
    load_pairs,
    load_scenarios,
)
from tests.eval.agents.curator.exp3_compare import INSTRUCTIONS

API_ENV = Path(__file__).resolve().parents[4] / ".env"  # apps/api/.env
MODEL = "typesafe/jev-1.13"
PROMPT = INSTRUCTIONS + (
    " subject와 동일한 후보 하나를 선택하세요. 후보 중 동일한 대상이 없으면 none, "
    "정보가 부족하거나 모호하면 uncertain. 후보 순서나 단순 관련성으로 선택하지 마세요."
)


def cases(pairs):
    """정답을 추측한 방해항은 넣지 않는다. 부정 라벨의 양방향 관계만 재사용."""
    negatives = {}
    for p in pairs:
        if p.label != "same":
            negatives.setdefault((p.domain, p.a), set()).add(p.b)
            negatives.setdefault((p.domain, p.b), set()).add(p.a)
    for index, p in enumerate(pairs):
        options = list(dict.fromkeys([p.b, *sorted(negatives.get((p.domain, p.a), set()))]))
        expected = p.b if p.label == "same" else "none"
        for variant, candidates, answer in (
            ("original", options, expected),
            ("reversed", list(reversed(options)), expected),
            ("absent", [s for s in options if s != p.b], "none"),
        ):
            if variant == "absent" and p.label != "same":
                continue
            yield index, p, variant, candidates, answer


async def run(live):
    tune = load_pairs(TUNE_PAIRS_PATH)
    print(f"선정용 {len(tune)}쌍. 고정 정책: exact 우선, 최고 선택값, confidence 게이트 없음.")
    if not live:
        sample = list(cases(tune))
        print(f"계획 {len(sample)}건, 다중후보 {sum(len(c[3]) > 1 for c in sample)}건")
        return
    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_values(API_ENV).get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY 미설정")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = RESULTS_DIR / f"exp3_flow_{stamp}_results.jsonl"
    report = RESULTS_DIR / f"exp3_flow_{stamp}_result.txt"
    records, calls, summaries = [], [], []
    semaphore = asyncio.Semaphore(4)
    with path.open("x", encoding="utf-8") as log:

        def save(row):
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()

        save(
            {
                "model": MODEL,
                "prompt": PROMPT,
                "policy": "exact-first; no confidence gate",
                "hashes": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (TUNE_PAIRS_PATH, CHECK1_PAIRS_PATH, CHECK1_ORDERS_PATH)
                },
            }
        )
        async with httpx.AsyncClient(timeout=45.0) as client:

            async def decide(subject, options, domain):
                for option in options:
                    if same_name_key(subject) == same_name_key(option):
                        return option, "exact"
                if not options:
                    return "none", "empty"
                async with semaphore:
                    start = time.perf_counter()
                    response = await client.post(
                        "https://openrouter.ai/api/alpha/decisions",
                        headers={"Authorization": f"Bearer {key.strip()}"},
                        json={
                            "model": MODEL,
                            "state": {"subject": subject, "domain": domain, "candidates": options},
                            "questions": {
                                "identity": {
                                    "type": "choice",
                                    "instructions": PROMPT,
                                    "criteria": {
                                        **{s: f"동일한 대상: {s}" for s in options},
                                        "none": "동일한 후보가 없다",
                                        "uncertain": "정보 부족 또는 모호하여 판단 불가",
                                    },
                                }
                            },
                        },
                    )
                    if response.status_code != 200:
                        save({"error_status": response.status_code})
                        raise RuntimeError(f"HTTP {response.status_code}; 중단")
                    result = response.json()
                    answer = result["answers"]["identity"]
                    if answer["type"] != "choice" or answer["choice"] not in [
                        *options,
                        "none",
                        "uncertain",
                    ]:
                        raise RuntimeError("invalid response")
                    call = {
                        "subject": subject,
                        "options": options,
                        "domain": domain,
                        "answer": answer,
                        "usage": result.get("usage", {}),
                        "model": result.get("model"),
                        "ms": (time.perf_counter() - start) * 1000,
                    }
                    calls.append(call)
                    save({"call": call})
                    return answer["choice"], "jev"

            async def evaluate(split, case):
                index, p, variant, options, expected = case
                choice, via = await decide(p.a, options, p.domain)
                row = {
                    "split": split,
                    "index": index,
                    "variant": variant,
                    "subject": p.a,
                    "options": options,
                    "expected": expected,
                    "choice": choice,
                    "via": via,
                    "label": p.label,
                }
                records.append(row)
                save(row)

            for split, pairs in (("tune", tune), ("holdout", load_pairs(CHECK1_PAIRS_PATH))):
                await asyncio.gather(*(evaluate(split, c) for c in cases(pairs)))
                for variant in ("original", "reversed", "absent"):
                    rows = [r for r in records if r["split"] == split and r["variant"] == variant]
                    fp = sum(r["choice"] not in (r["expected"], "none", "uncertain") for r in rows)
                    miss = sum(
                        r["expected"] != "none" and r["choice"] != r["expected"] for r in rows
                    )
                    summaries.append(
                        {
                            "split": split,
                            "variant": variant,
                            "n": len(rows),
                            "multi_candidate": sum(len(r["options"]) > 1 for r in rows),
                            "false_link": fp,
                            "miss": miss,
                            "held": sum(r["choice"] == "uncertain" for r in rows),
                            "correct": sum(r["choice"] == r["expected"] for r in rows),
                        }
                    )
                flips = sum(
                    next(
                        r
                        for r in records
                        if r["split"] == split and r["index"] == i and r["variant"] == "original"
                    )["choice"]
                    != next(
                        r
                        for r in records
                        if r["split"] == split and r["index"] == i and r["variant"] == "reversed"
                    )["choice"]
                    for i in range(len(pairs))
                )
                summaries.append({"split": split, "order_disagreements": flips})
                print(json.dumps(summaries[-4:], ensure_ascii=False), flush=True)

            for scenario in load_scenarios():
                n = len(scenario.steps)
                orders = list(
                    dict.fromkeys(
                        [
                            tuple(range(n)),
                            tuple(reversed(range(n))),
                            *(tuple([i, *[j for j in range(n) if j != i]]) for i in range(n)),
                        ]
                    )
                )
                for order in orders:
                    profiles, linked = [], {}
                    for i in order:
                        step = scenario.steps[i]
                        eligible = [p for p in profiles if p["polarity"] == step.polarity]
                        choice, via = await decide(
                            step.subject, [p["key"] for p in eligible], scenario.domain
                        )
                        if choice == "uncertain":
                            linked[i + 1] = None
                        elif choice == "none":
                            profiles.append(
                                {"id": i + 1, "key": step.subject, "polarity": step.polarity}
                            )
                            linked[i + 1] = i + 1
                        else:
                            linked[i + 1] = next(p["id"] for p in eligible if p["key"] == choice)
                    expected = {i: g for g, group in enumerate(scenario.groups) for i in group}
                    fp = fn = 0
                    for i in range(1, n + 1):
                        for j in range(i + 1, n + 1):
                            actual = linked[i] is not None and linked[i] == linked[j]
                            truth = expected[i] == expected[j]
                            fp += actual and not truth
                            fn += truth and not actual
                    row = {
                        "scenario": scenario.id,
                        "order": order,
                        "links": linked,
                        "false_merge_pairs": fp,
                        "miss_pairs": fn,
                        "held": sum(v is None for v in linked.values()),
                    }
                    row["passed"] = not (fp or fn or row["held"])
                    summaries.append(row)
                    save(row)
                print(f"시나리오 {scenario.id} 완료", flush=True)
        summaries.append(
            {
                "calls": len(calls),
                "cost_usd": sum(c["usage"].get("cost", 0) for c in calls),
                "median_ms": statistics.median(c["ms"] for c in calls),
            }
        )
        report.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in summaries) + "\n")
        print(f"결과: {report}\n상세: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    asyncio.run(run(parser.parse_args().live))
