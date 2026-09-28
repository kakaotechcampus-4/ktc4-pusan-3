"""실험 5 — 운영 코드(link_pending + JevJudge + Embedder)로 판정 방식을 고르고 최종 확인한다.

실행 (apps/api 에서, 유료):
    5a 고르기  uv run python -m tests.eval.agents.curator.exp5_scale_final --live
    5b 최종    uv run python -m tests.eval.agents.curator.exp5_scale_final --final --arm X --live

키: .env 의 CURATOR_JUDGE_API_KEY. CURATOR_JUDGE_BASE_URL · _MODEL 이 없으면 환경변수로 넘긴다.
    CURATOR_JUDGE_BASE_URL=https://openrouter.ai/api/alpha CURATOR_JUDGE_MODEL=typesafe/jev-1.13 ...

조건 (5a)
    v1        운영 그대로 — 판단 기준 문장 v1
    v1_back   v1 + "없음"이면 가까운 후보 3개에 방향을 바꿔 한 번 더 묻는다
              (실험 3 의 순서 실패 대응)
    v3        판단 기준을 명시한 문장 (B · C · E · F 를 예시와 함께 적음)

과제
    scale      tune_scale.txt — Profile 이 쌓인 상태에서 새 관찰 하나 (5a)
    pairs      tune_pairs.txt (5a) / check2_pairs.txt (5b) — 쌍마다 원래 · 역순 · 정답 제거
    orders     check1_orders.txt (5a) / check2_orders.txt (5b) — 여러 순서로 관찰을 쌓는다

5b 는 조건 하나만, 한 번만 돌린다. 5b 결과를 보고 조건을 다시 고르지 않는다.
결과는 이 폴더의 exp5_*_result.txt · exp5_*_results.jsonl (gitignore 대상).
"""

import argparse
import asyncio
import json
import math
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from app.agents.curator.embedding import Embedder, JevJudge
from app.agents.curator.embedding.inmemory import InMemoryCuratorStore
from app.agents.curator.embedding.judge import NONE, PROMPT, IdentityJudge, JudgeAnswer
from app.agents.curator.embedding.link_step import (
    MAX_CANDIDATES,
    LinkOutcome,
    link_pending,
    same_name_key,
)
from app.agents.curator.embedding.ports import CuratorDomain
from tests.eval.agents.curator.datasets import (
    CHECK1_ORDERS_PATH,
    CHECK2_ORDERS_PATH,
    CHECK2_PAIRS_PATH,
    RESULTS_DIR,
    TUNE_PAIRS_PATH,
    Scenario,
    load_pairs,
    load_scale,
    load_scenarios,
)
from tests.eval.agents.curator.exp3_flow import cases

HERE = RESULTS_DIR
CHILD = UUID("00000000-0000-0000-0000-0000000e0005")
REVERSE_TOP = 3  # v1_back 이 방향을 바꿔 다시 물을 후보 수

# v1 에 "단순 관련성"으로만 기대던 기준(B · C · E · F)을 예시와 함께 적었다.
# 예시는 실험 데이터 어디에도 없는 이름으로 골랐다 — 데이터에 있는 이름이면 v3 만 정답 힌트를
# 보고 푸는 셈이 된다 (test_pairs_data.py 가 확인)
V3_EXAMPLES = ("사이다", "콜라", "타요", "폴리", "붓", "서예", "종이비행기", "연날리기")
PROMPT_V3 = PROMPT + (
    " 다음도 동일하지 않습니다: 같은 음식군의 다른 음식(예: 사이다와 콜라), "
    "서로 다른 캐릭터·제품(예: 타요와 폴리), 도구와 활동(예: 붓과 서예), "
    "비슷하지만 다른 활동(예: 종이비행기와 연날리기)."
)


# ── 판정기 ──────────────────────────────────────────────────────────────────


@dataclass
class Counted:
    """부른 횟수 · 시간을 센다. 비용은 Jev 가 로그로만 남겨서 여기서는 횟수만 본다."""

    inner: IdentityJudge
    calls: int = 0
    latency_ms: list[float] = field(default_factory=list)

    async def judge(
        self, *, subject: str, domain: CuratorDomain, candidates: Sequence[str]
    ) -> JudgeAnswer:
        self.calls += 1
        started = time.perf_counter()
        try:
            return await self.inner.judge(subject=subject, domain=domain, candidates=candidates)
        finally:
            self.latency_ms.append((time.perf_counter() - started) * 1000)


@dataclass
class ReverseCheck:
    """ "없음"이면 가까운 후보 몇 개에 방향을 바꿔 묻는다.

    후보를 subject 로, 원래 subject 를 후보로 넣는다.
    """

    inner: IdentityJudge
    vectors: dict[str, list[float]]

    async def judge(
        self, *, subject: str, domain: CuratorDomain, candidates: Sequence[str]
    ) -> JudgeAnswer:
        answer = await self.inner.judge(subject=subject, domain=domain, candidates=candidates)
        if answer.choice != NONE:
            return answer
        nearest = sorted(candidates, key=lambda c: -_cosine(self.vectors[subject], self.vectors[c]))
        for candidate in nearest[:REVERSE_TOP]:
            back = await self.inner.judge(subject=candidate, domain=domain, candidates=[subject])
            if back.choice == subject:
                return JudgeAnswer(candidate, model=back.model, confidence=back.confidence)
        return answer


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def make_arm(arm: str, vectors: dict[str, list[float]]) -> tuple[IdentityJudge, Counted]:
    """(연결 단계에 넣을 판정기, 실제 Jev 호출을 세는 판정기). 세는 쪽이 가장 안쪽이다 —
    v1_back 이 방향을 바꿔 추가로 부르는 호출까지 센다."""
    if arm == "v1":
        counted = Counted(JevJudge())
        return counted, counted
    if arm == "v1_back":
        counted = Counted(JevJudge())
        return ReverseCheck(counted, vectors), counted
    if arm == "v3":
        counted = Counted(JevJudge(prompt=PROMPT_V3))
        return counted, counted
    raise SystemExit(f"알 수 없는 조건: {arm}")


# ── 과제 ────────────────────────────────────────────────────────────────────


def _grade(outcome: LinkOutcome, expected: str, names: dict[str, str]) -> str:
    """correct · false_link · missed · held"""
    if outcome.status == "held":
        return "held"
    chosen = NONE if outcome.status == "created" else names[outcome.affinity_id or ""]
    if same_name_key(chosen) == same_name_key(expected):
        return "correct"
    return "missed" if chosen == NONE else "false_link"


async def _one(
    judge: IdentityJudge,
    vectors: dict[str, list[float]],
    *,
    domain: CuratorDomain,
    profiles: Sequence[str],
    subject: str,
    expected: str,
) -> dict[str, Any]:
    store = InMemoryCuratorStore()
    names: dict[str, str] = {}
    for name in profiles:
        created = store.add_profile(
            child_id=CHILD,
            domain=domain,
            merge_key=name,
            polarity=1,
            embedding=vectors[name.strip()],
        )
        names[created.id] = name
    store.add_observation(
        child_id=CHILD,
        domain=domain,
        id="new",
        subject=subject,
        polarity=1,
        embedding=vectors[subject.strip()],
        observed_on=date(2026, 9, 27),
    )
    [outcome] = (await link_pending(store, judge, child_id=CHILD)).outcomes
    shown = _shown(profiles, vectors, subject)
    return {
        "subject": subject,
        "expected": expected,
        "profiles": len(profiles),
        "status": outcome.status,
        "match": outcome.match,
        "reason": outcome.reason,
        "chosen": names.get(outcome.affinity_id or "", NONE if outcome.created else None),
        "grade": _grade(outcome, expected, names),
        # 후보를 추렸다면, 정답 후보가 추린 목록에 남았는가 (None 이면 추리지 않았다)
        "expected_shown": None if shown is None or expected == NONE else expected in shown,
    }


def _shown(profiles: Sequence[str], vectors: dict[str, list[float]], subject: str):
    if len(profiles) <= MAX_CANDIDATES:
        return None
    ranked = sorted(profiles, key=lambda p: -_cosine(vectors[subject.strip()], vectors[p]))
    return set(ranked[:MAX_CANDIDATES])


async def run_scale(judge: IdentityJudge, vectors: dict[str, list[float]]) -> list[dict[str, Any]]:
    rows = []
    for sc in load_scale():
        for ask in sc.asks:
            row = await _one(
                judge,
                vectors,
                domain=sc.domain,
                profiles=sc.profiles,
                subject=ask.subject,
                expected=ask.expected,
            )
            rows.append({"task": "scale", "scenario": sc.id, **row})
    return rows


async def run_pairs(judge: IdentityJudge, vectors, path) -> list[dict[str, Any]]:
    rows = []
    for index, pair, variant, options, expected in cases(load_pairs(path)):
        row = await _one(
            judge,
            vectors,
            domain=pair.domain,
            profiles=options,
            subject=pair.a,
            expected=expected,
        )
        rows.append(
            {"task": "pairs", "index": index, "variant": variant, "label": pair.label, **row}
        )
    return rows


def _orders(scenario: Scenario) -> list[tuple[int, ...]]:
    """적힌 순서 · 뒤집은 순서 · 각 관찰이 먼저 오는 순서 (실험 3 과 같다)."""
    n = len(scenario.steps)
    written = tuple(range(1, n + 1))
    orders = [written, written[::-1], *((i, *(j for j in written if j != i)) for i in written)]
    return list(dict.fromkeys(orders))


async def run_orders(judge: IdentityJudge, vectors, path) -> list[dict[str, Any]]:
    rows = []
    for scenario in load_scenarios(path):
        expected = {frozenset(g) for g in scenario.groups}
        for order in _orders(scenario):
            store = InMemoryCuratorStore()
            for n in order:
                step = scenario.steps[n - 1]
                store.add_observation(
                    child_id=CHILD,
                    domain=scenario.domain,
                    id=str(n),
                    subject=step.subject,
                    polarity=step.polarity,
                    embedding=vectors[step.subject.strip()],
                )
            outcomes = (await link_pending(store, judge, child_id=CHILD)).outcomes
            held = [o for o in outcomes if o.status == "held"]
            groups: dict[str, set[int]] = {}
            for o in outcomes:
                if o.affinity_id:
                    groups.setdefault(o.affinity_id, set()).add(int(o.key[1]))
            got = {frozenset(g) for g in groups.values()}
            merged = sum(
                1
                for g in got
                for e in expected
                if len(g & e) and not g <= e  # 기대 묶음 밖의 관찰이 섞였다 → 잘못된 연결
            )
            rows.append(
                {
                    "task": "orders",
                    "scenario": scenario.id,
                    "order": order,
                    "passed": got == expected and not held,
                    "false_merge": merged > 0,
                    "held": len(held),
                    "groups": sorted(sorted(g) for g in got),
                }
            )
    return rows


# ── 실행 · 보고 ─────────────────────────────────────────────────────────────


def _names(final: bool) -> set[str]:
    names: set[str] = set()
    pair_path = CHECK2_PAIRS_PATH if final else TUNE_PAIRS_PATH
    order_path = CHECK2_ORDERS_PATH if final else CHECK1_ORDERS_PATH
    for p in load_pairs(pair_path):
        names |= {p.a.strip(), p.b.strip()}
    for sc in load_scenarios(order_path):
        names |= {s.subject.strip() for s in sc.steps}
    if not final:
        for sc in load_scale():
            names |= {p.strip() for p in sc.profiles} | {a.subject.strip() for a in sc.asks}
    return names


def _summary(rows: list[dict[str, Any]]) -> list[str]:
    lines = []
    for task in ("scale", "pairs"):
        rs = [r for r in rows if r["task"] == task]
        if not rs:
            continue
        grades = Counter(r["grade"] for r in rs)
        positives = [r for r in rs if r["expected"] != NONE]
        missed = sum(r["grade"] in ("missed", "held") for r in positives)
        lines.append(
            f"  [{task}] {len(rs)}건 · 잘못된 연결 {grades['false_link']} · "
            f"놓친 연결 {missed}/{len(positives)} ({missed / max(len(positives), 1):.0%}) · "
            f"보류 {grades['held']} · 정답 {grades['correct']}/{len(rs)}"
        )
        shown = [r["expected_shown"] for r in rs if r["expected_shown"] is not None]
        if shown:
            lines.append(f"    추린 목록에 정답 후보가 남음 {sum(shown)}/{len(shown)}")
    orders = [r for r in rows if r["task"] == "orders"]
    if orders:
        lines.append(
            f"  [orders] {len(orders)}회 · 통과 {sum(r['passed'] for r in orders)} · "
            f"잘못된 연결이 섞인 회 {sum(r['false_merge'] for r in orders)} · "
            f"보류 {sum(r['held'] for r in orders)}"
        )
    return lines


async def run(args: argparse.Namespace) -> None:
    arms = [args.arm] if args.arm else ["v1", "v1_back", "v3"]
    if args.final and len(arms) != 1:
        raise SystemExit("5b 는 조건 하나만 돌린다: --final --arm <조건>")
    names = _names(args.final)
    stage = "5b 최종" if args.final else "5a 고르기"
    print(f"{stage} · 조건 {arms} · 이름 {len(names)}개")
    if not args.live:
        print("DRY RUN. --live 를 지정해야 API 를 호출한다.")
        return

    embedder = Embedder()
    ordered = sorted(names)
    vectors = dict(zip(ordered, await embedder.embed(ordered), strict=True))

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    tag = "final" if args.final else "select"
    log_path = HERE / f"exp5_{tag}_{stamp}_results.jsonl"
    report_path = HERE / f"exp5_{tag}_{stamp}_result.txt"
    lines = [
        f"# 실험 5 {stage}",
        f"MAX_CANDIDATES={MAX_CANDIDATES} · REVERSE_TOP={REVERSE_TOP}",
        "",
    ]
    with log_path.open("x", encoding="utf-8") as log:
        log.write(json.dumps({"stage": stage, "arms": arms, "prompt_v3": PROMPT_V3}) + "\n")
        for arm in arms:
            judge, counted = make_arm(arm, vectors)
            rows = []
            if not args.final:
                rows += await run_scale(judge, vectors)
            rows += await run_pairs(
                judge, vectors, CHECK2_PAIRS_PATH if args.final else TUNE_PAIRS_PATH
            )
            rows += await run_orders(
                judge, vectors, CHECK2_ORDERS_PATH if args.final else CHECK1_ORDERS_PATH
            )
            for row in rows:
                log.write(json.dumps({"arm": arm, **row}, ensure_ascii=False, default=list) + "\n")
            lines.append(f"## {arm}")
            lines += _summary(rows)
            median = statistics.median(counted.latency_ms) if counted.latency_ms else 0
            lines.append(f"  Jev 호출 {counted.calls}회 · 응답 중앙값 {median:.0f}ms")
            lines.append("  틀린 것")
            for r in rows:
                if r["task"] != "orders" and r["grade"] != "correct":
                    lines.append(
                        f"    {r['task']:<6} {r['subject']} → {r['chosen']} (기대 {r['expected']}) "
                        f"{r['grade']}" + (f" {r['reason']}" if r["reason"] else "")
                    )
                if r["task"] == "orders" and not r["passed"]:
                    lines.append(f"    orders {r['scenario']} 순서 {r['order']} → {r['groups']}")
            lines.append("")
            print(f"{arm} 완료", flush=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"보고서: {report_path}\n상세: {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=["v1", "v1_back", "v3"])
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--live", action="store_true")
    asyncio.run(run(parser.parse_args()))
