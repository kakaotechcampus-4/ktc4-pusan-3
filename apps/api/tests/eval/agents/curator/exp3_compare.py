"""선정용 + 판단이 갈리는 쌍 비교. holdout은 읽지 않는다. --live로 유료 API 실행.

실행 (apps/api 에서): uv run python -m tests.eval.agents.curator.exp3_compare --live
키: 환경변수 OPENROUTER_API_KEY. apps/api/.env 에 두면 서버 설정이 거부한다.

쌍당 독립 요청, 최대 동시 4개, 자동 재시도 없음. 정답은 모델에 보내지 않는다.
모델 선택값 그대로/이름 일치 우선 두 정책을 보고한다. uncertain은 보류이며,
same에서 보류되면 미연결(놓침)에 포함한다. API 실패는 정답으로 세지 않는다.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

from app.agents.curator.embedding import Embedder
from app.agents.curator.embedding.link_step import same_name_key
from tests.eval.agents.curator.datasets import (
    DISPUTED_PAIRS_PATH,
    RESULTS_DIR,
    TUNE_PAIRS_PATH,
    load_disputed,
    load_pairs,
)

API_ENV = Path(__file__).resolve().parents[4] / ".env"  # apps/api/.env
MODEL = "typesafe/jev-1.13"
INSTRUCTIONS = (
    "subject와 candidate가 동일한 대상의 다른 표현인지 판정하세요. "
    "같은 대상에 대한 반복 관찰로 집계할 수 있어야 합니다. "
    "단순 관련성, 상위/하위 범주, 원재료/가공품은 동일하지 않습니다. "
    "확실한 동의어, 오타, 띄어쓰기 차이는 같은 대상입니다. "
    "대상의 범위를 바꾸는 수식어를 임의로 무시하지 마세요. "
    "정보가 부족하거나 모호하면 uncertain을 선택하세요."
)
CRITERIA = {
    "same": "동일한 대상의 다른 표현이다",
    "different": "관련성이 있더라도 서로 다른 대상이다",
    "uncertain": "정보가 부족하거나 모호하여 판단할 수 없다",
}


def metrics(rows, mode):
    usable = [r for r in rows if "error" not in r]
    positives = [r for r in usable if r["label"] == "same"]
    negatives = [r for r in usable if r["label"] != "same"]

    def linked(row):
        if isinstance(mode, float):
            return row["exact"] or row["cosine"] >= mode
        return (mode == "jev_exact" and row["exact"]) or row["choice"] == "same"

    fp = sum(linked(r) for r in negatives)
    fn = sum(not linked(r) for r in positives)
    held = (
        sum(r["choice"] == "uncertain" and not (mode == "jev_exact" and r["exact"]) for r in usable)
        if isinstance(mode, str)
        else 0
    )
    return (
        f"{mode}: 잘못합침 {fp}/{len(negatives)}, 미연결 {fn}/{len(positives)}, "
        f"보류 {held}, API실패 {len(rows) - len(usable)}"
    )


async def run(live):
    pairs = load_pairs(TUNE_PAIRS_PATH)
    ambiguous = load_disputed()
    print(
        f"선정용 {len(pairs)}쌍 + 판단이 갈리는 쌍 {len(ambiguous)}쌍; holdout 미사용", flush=True
    )
    if not live:
        print("DRY RUN. --live를 지정해야 API를 호출합니다.")
        return
    key = os.environ.get("OPENROUTER_API_KEY") or dotenv_values(API_ENV).get("OPENROUTER_API_KEY")
    if not key or not key.strip():
        raise SystemExit("OPENROUTER_API_KEY 미설정")
    embedder = Embedder()
    all_pairs = [*pairs, *ambiguous]
    texts = list(dict.fromkeys(s.strip() for p in all_pairs for s in (p.a, p.b)))
    vectors = dict(zip(texts, await embedder.embed(texts), strict=True))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = RESULTS_DIR / f"exp3_compare_{timestamp}_results.jsonl"
    report = RESULTS_DIR / f"exp3_compare_{timestamp}_result.txt"
    semaphore = asyncio.Semaphore(4)
    rows = []
    metadata = {
        "model": MODEL,
        "instructions": INSTRUCTIONS,
        "criteria": CRITERIA,
        "started_at": timestamp,
        "data_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (TUNE_PAIRS_PATH, DISPUTED_PAIRS_PATH)
        },
        "policy": "pairwise; raw choice (no confidence threshold); exact override separately",
    }
    with output.open("x", encoding="utf-8") as log:
        log.write(json.dumps({"metadata": metadata}, ensure_ascii=False) + "\n")
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=False) as client:

            async def evaluate(index, pair):
                a, b = vectors[pair.a.strip()], vectors[pair.b.strip()]
                row = {
                    "index": index,
                    "domain": pair.domain,
                    "a": pair.a,
                    "b": pair.b,
                    "label": getattr(pair, "label", "ambiguous"),
                    "kind": getattr(pair, "kind", None),
                    "exact": same_name_key(pair.a) == same_name_key(pair.b),
                    "cosine": sum(x * y for x, y in zip(a, b, strict=True))
                    / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))),
                }
                async with semaphore:
                    start = time.perf_counter()
                    try:
                        response = await client.post(
                            "https://openrouter.ai/api/alpha/decisions",
                            headers={"Authorization": f"Bearer {key.strip()}"},
                            json={
                                "model": MODEL,
                                "state": {
                                    "domain": pair.domain,
                                    "subject": pair.a,
                                    "candidate": pair.b,
                                },
                                "questions": {
                                    "identity": {
                                        "type": "choice",
                                        "instructions": INSTRUCTIONS,
                                        "criteria": CRITERIA,
                                    }
                                },
                            },
                        )
                        response.raise_for_status()
                        result = response.json()
                        answer = result["answers"]["identity"]
                        assert answer["type"] == "choice"
                        assert answer["choice"] in CRITERIA
                        probs = answer["probabilities"]
                        assert set(probs) == set(CRITERIA)
                        assert all(
                            isinstance(v, (float, int)) and 0 <= v <= 1 for v in probs.values()
                        )
                        assert abs(sum(probs.values()) - 1) <= 0.03
                        assert 0 <= answer["confidence"] <= 1
                        row.update(answer)
                        row.update(model=result.get("model"), usage=result.get("usage", {}))
                    except (
                        httpx.HTTPError,
                        ValueError,
                        KeyError,
                        TypeError,
                        AssertionError,
                    ) as exc:
                        row["error"] = type(exc).__name__
                        if isinstance(exc, httpx.HTTPStatusError):
                            row["http_status"] = exc.response.status_code
                    row["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
                    rows.append(row)
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                    log.flush()
                    if len(rows) % 20 == 0 or len(rows) == len(all_pairs):
                        print(f"완료 {len(rows)}/{len(all_pairs)}", flush=True)

            await asyncio.gather(*(evaluate(i, p) for i, p in enumerate(all_pairs)))
    rows.sort(key=lambda r: r["index"])
    tune = [r for r in rows if r["label"] != "ambiguous"]
    lines = ["Jev vs embedding — 선정용 (holdout 미사용)", json.dumps(metadata, ensure_ascii=False)]
    for domain in ("전체", "food", "activity", "education"):
        subset = [r for r in tune if domain == "전체" or r["domain"] == domain]
        lines.append(f"\n[{domain}]")
        lines.extend(metrics(subset, mode) for mode in (0.5, 0.77, "jev", "jev_exact"))
    successful = [r for r in rows if "error" not in r]
    latency = sorted(r["latency_ms"] for r in successful)
    cost = sum(r["usage"].get("cost", 0) for r in successful)
    lines.append(f"\nJev 성공 {len(successful)}/{len(rows)}, 비용 ${cost:.8f} (임베딩 비용 별도)")
    if latency:
        lines.append(
            f"왕복 median {statistics.median(latency):.0f}ms, "
            f"p95 {latency[math.ceil(len(latency) * 0.95) - 1]:.0f}ms (동시성 4)"
        )
    lines.append("\n선정용 오답/보류/API실패 (Jev 단독):")
    for r in tune:
        if (
            "error" in r
            or (r["choice"] == "same") != (r["label"] == "same")
            or r["choice"] == "uncertain"
        ):
            lines.append(json.dumps(r, ensure_ascii=False))
    lines.append("\n판단이 갈리는 쌍 — 점수 제외:")
    lines.extend(json.dumps(r, ensure_ascii=False) for r in rows if r["label"] == "ambiguous")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:22]))
    print(f"\n보고서: {report}\n상세: {output}")
    if len(successful) != len(rows):
        raise SystemExit("일부 API 실패: 보고서를 완전한 평가로 해석하지 마세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    asyncio.run(run(parser.parse_args().live))
