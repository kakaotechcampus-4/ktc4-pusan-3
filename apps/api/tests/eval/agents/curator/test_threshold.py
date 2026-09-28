"""유사도 임계값 실험 (실험 1 · 2). 실제 임베딩 API 를 부른다 (-m live).

결론: 유사도만으로는 기준을 만족하는 임계값이 없다 (Notion 실험 1 · 2). 그래서 운영 연결은
임계값이 아니라 동일 대상 판정기(Jev)로 바뀌었다 — 이 파일은 그 근거를 다시 재현하는 용도다.

    선정   pytest tests/eval/agents/curator/test_threshold.py -m live -k select
           선정용 쌍으로 임계값을 쓸고 보고서를 남긴다. 판정하지 않는다
    검증   pytest tests/eval/agents/curator/test_threshold.py -m live -k holdout
           THRESHOLD 로 검증용 쌍을 평가한다. 검증용은 실험 3 에서 이미 썼다

보고서는 이 폴더의 threshold_*_result.txt 에 남는다 (gitignore 대상).

판정 기준
    - 잘못 합침: near · unrelated 쌍의 유사도가 임계값 이상 → 0건이어야 한다
    - 놓침: same 쌍의 유사도가 임계값 미만 → 비율이 MAX_MISS_RATE 이하여야 한다
    "잘못 합침 0건"은 이 데이터에서 통과했다는 뜻이지 서비스에서 오류가 없다는 보장이 아니다.
"""

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

import pytest

from app.agents.curator.embedding import Embedder
from app.agents.curator.embedding.link_step import same_name_key
from app.agents.curator.embedding.ports import CURATOR_DOMAINS
from tests.eval.agents.curator.pairs import (
    HOLDOUT_PATH,
    TUNE_PATH,
    AmbiguousPair,
    Pair,
    load_ambiguous,
    load_pairs,
)

pytestmark = pytest.mark.live

HERE = Path(__file__).parent
MAX_MISS_RATE = 0.30
GRID = [round(0.30 + i * 0.01, 2) for i in range(61)]  # 0.30 ~ 0.90
# 실험 1 · 2 당시 운영 코드의 임시 임계값. 운영 연결은 이제 판정기를 쓴다
THRESHOLD = 0.5

P = TypeVar("P", Pair, AmbiguousPair)


class CachedEmbedder:
    """같은 문자열은 한 번만 부른다. 시나리오를 여러 순서로 돌려도 같은 벡터를 쓴다."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._cache: dict[str, list[float]] = {}
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            self.calls += 1
            self._cache.update(zip(missing, await self._embedder.embed(missing), strict=True))
        return [self._cache[t] for t in texts]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def _texts(pairs: Iterable[Pair | AmbiguousPair]) -> list[str]:
    return sorted({s.strip() for p in pairs for s in (p.a, p.b)})


@dataclass(frozen=True)
class Scored(Generic[P]):
    pair: P
    sim: float  # 실제 벡터 유사도. exact 쌍도 벡터 값을 그대로 둔다
    exact: bool  # 이름 비교(same_name_key)로 연결되는 쌍 — 운영에서는 유사도를 보지 않는다

    def linked(self, threshold: float, *, vector_only: bool = False) -> bool:
        """운영 규칙(이름 비교 → 유사도)으로 연결되는가. vector_only 면 이름 비교를 뺀다."""
        return (self.exact and not vector_only) or self.sim >= threshold


async def _similarities(embedder: CachedEmbedder, pairs: list[P]) -> list[Scored[P]]:
    """운영과 같게 strip 한 subject 를 임베딩한다."""
    texts = _texts(pairs)
    vectors = dict(zip(texts, await embedder.embed(texts), strict=True))
    return [
        Scored(
            p,
            _cosine(vectors[p.a.strip()], vectors[p.b.strip()]),
            same_name_key(p.a) == same_name_key(p.b),
        )
        for p in pairs
    ]


@dataclass(frozen=True)
class Score:
    threshold: float
    false_merges: int
    negatives: int
    misses: int
    positives: int

    @property
    def miss_rate(self) -> float:
        return self.misses / self.positives if self.positives else 0.0

    @property
    def passes(self) -> bool:
        return self.false_merges == 0 and self.miss_rate <= MAX_MISS_RATE


def _score(rows: list[Scored[Pair]], threshold: float, *, vector_only: bool = False) -> Score:
    positives = [r for r in rows if r.pair.label == "same"]
    negatives = [r for r in rows if r.pair.label != "same"]
    return Score(
        threshold=threshold,
        false_merges=sum(r.linked(threshold, vector_only=vector_only) for r in negatives),
        negatives=len(negatives),
        misses=sum(not r.linked(threshold, vector_only=vector_only) for r in positives),
        positives=len(positives),
    )


def _exact_summary(rows: list[Scored[Pair]]) -> str:
    same = sum(r.exact for r in rows if r.pair.label == "same")
    other = sum(r.exact for r in rows if r.pair.label != "same")
    return f"exact 연결(이름 비교, 임계값과 무관): same {same}쌍 · 다름 {other}쌍"


def _line(row: Scored[Pair] | Scored[AmbiguousPair], *, label: bool = True) -> str:
    """보고서의 쌍 한 줄. exact 쌍은 표시한다."""
    kind = f"{row.pair.label:<9} " if label and isinstance(row.pair, Pair) else ""
    mark = " (exact)" if row.exact else ""
    return f"  {row.sim:.3f}  {row.pair.domain:<9} {kind}{row.pair.a} — {row.pair.b}{mark}"


def _spread(values: list[float]) -> str:
    if not values:
        return "-"
    return f"min {min(values):.3f} · 중앙 {statistics.median(values):.3f} · max {max(values):.3f}"


def _write(name: str, lines: list[str]) -> Path:
    path = HERE / f"threshold_{name}_result.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _in(domain: str, rows: list[Scored[Pair]]) -> list[Scored[Pair]]:
    return [r for r in rows if domain in ("전체", r.pair.domain)]


# 1. 선정 ──────────────────────────────────────────────────────────────────────


async def test_select_선정용으로_임계값을_쓴다() -> None:
    """판정하지 않는다. 보고서를 남기고, 기준을 만족하는 가장 낮은 임계값을 알려 준다."""
    embedder = CachedEmbedder(Embedder())
    tune = load_pairs(TUNE_PATH)
    scored = await _similarities(embedder, tune)

    # 같은 입력을 한 번 더 보내 값이 흔들리는지 본다 (캐시를 거치지 않는다)
    texts = _texts(tune)
    again = dict(zip(texts, await Embedder().embed(texts), strict=True))
    drift = max(
        abs(r.sim - _cosine(again[r.pair.a.strip()], again[r.pair.b.strip()])) for r in scored
    )

    lines = [
        "# 임계값 선정 (pairs_tune.txt)",
        f"기준: 잘못 합침 0건 · 놓침 비율 ≤ {MAX_MISS_RATE:.0%}",
        f"현재 THRESHOLD={THRESHOLD}",
        _exact_summary(scored),
        f"같은 입력 두 번의 유사도 차이 최대 {drift:.6f}",
        "",
        "## 정답별 벡터 유사도 분포 (exact 쌍도 실제 벡터 값)",
    ]
    for domain in (*CURATOR_DOMAINS, "전체"):
        rows = _in(domain, scored)
        lines.append(f"[{domain}]")
        for label in ("same", "near", "unrelated"):
            values = [r.sim for r in rows if r.pair.label == label]
            lines.append(f"  {label:<9} {_spread(values)}")
        same = [r.sim for r in rows if r.pair.label == "same"]
        other = [r.sim for r in rows if r.pair.label != "same"]
        lines.append(f"  간격(same 최소 − 다름 최대) {min(same) - max(other):+.3f}")

    lines += [
        "",
        "## 임계값별 (전체)",
        "운영 = 이름 비교 → 유사도 (link_step 과 같다) · 벡터만 = 이름 비교를 뺀 것",
        "임계값  운영: 잘못합침  놓침     놓침비율  기준  | 벡터만: 잘못합침  놓침",
    ]
    chosen: dict[str, Score | None] = {}
    for domain in (*CURATOR_DOMAINS, "전체"):
        rows = _in(domain, scored)
        scores = [_score(rows, t) for t in GRID]
        chosen[domain] = next((s for s in scores if s.passes), None)
        if domain == "전체":
            for s in scores:
                v = _score(rows, s.threshold, vector_only=True)
                mark = "통과" if s.passes else "    "
                lines.append(
                    f"{s.threshold:.2f}          {s.false_merges:>2}/{s.negatives:<4}  "
                    f"{s.misses:>2}/{s.positives:<4}  {s.miss_rate:6.1%}   {mark}"
                    f"  |         {v.false_merges:>2}/{v.negatives:<4}  {v.misses:>2}/{v.positives}"
                )

    lines += ["", "## 기준을 만족하는 가장 낮은 임계값 (운영 규칙)"]
    for domain, s in chosen.items():
        if s is None:
            lines.append(f"  {domain:<9} 없음 — 기준을 만족하는 임계값이 없다")
        else:
            miss = f"놓침 {s.misses}/{s.positives} = {s.miss_rate:.1%}"
            lines.append(f"  {domain:<9} {s.threshold:.2f} ({miss})")

    lines += ["", "## 경계 근처 — subject 품질을 볼 쌍 (벡터 유사도)"]
    lines.append("다름인데 유사도가 높은 쌍 (상위 10)")
    for r in sorted((r for r in scored if r.pair.label != "same"), key=lambda r: -r.sim)[:10]:
        lines.append(_line(r))
    lines.append("같음인데 유사도가 낮은 쌍 (하위 10, exact 제외)")
    for r in sorted(
        (r for r in scored if r.pair.label == "same" and not r.exact), key=lambda r: r.sim
    )[:10]:
        lines.append(_line(r, label=False))

    ambiguous = await _similarities(embedder, load_ambiguous())
    lines += ["", "## 판단이 갈리는 쌍 (점수에 넣지 않음)"]
    for kind in sorted({r.pair.kind for r in ambiguous}):
        rows_a = [r for r in ambiguous if r.pair.kind == kind]
        lines.append(f"[{kind}] {_spread([r.sim for r in rows_a])}")
        for r in sorted(rows_a, key=lambda r: -r.sim):
            lines.append(_line(r))

    lines.append(f"\n임베딩 호출 {embedder.calls + 1}회 (흔들림 확인 1회 포함)")
    path = _write("select", lines)
    print(f"\n보고서: {path}")


# 2. 검증 ──────────────────────────────────────────────────────────────────────


async def test_holdout_검증용에서_기준을_지킨다() -> None:
    embedder = CachedEmbedder(Embedder())
    scored = await _similarities(embedder, load_pairs(HOLDOUT_PATH))

    lines = [
        "# 임계값 검증 (pairs_holdout.txt)",
        f"THRESHOLD={THRESHOLD} · 기준: 잘못 합침 0건 · 놓침 ≤ {MAX_MISS_RATE:.0%}",
        "⚠️ same 은 오타 · 띄어쓰기 · 다른 이름이 대부분이다.",
        "   의미가 비슷한 표현을 얼마나 잘 잇는가 전체로 넓혀 읽지 않는다",
        _exact_summary(scored),
        "",
    ]
    results = {}
    for domain in (*CURATOR_DOMAINS, "전체"):
        rows = _in(domain, scored)
        results[domain] = score = _score(rows, THRESHOLD)
        lines.append(
            f"[{domain}] 잘못 합침 {score.false_merges}/{score.negatives} · "
            f"놓침 {score.misses}/{score.positives} ({score.miss_rate:.1%})"
        )
        for label in ("same", "near", "unrelated"):
            lines.append(f"  {label:<9} {_spread([r.sim for r in rows if r.pair.label == label])}")
    lines += ["", "틀린 쌍"]
    for r in scored:
        if (r.pair.label == "same") != r.linked(THRESHOLD):
            lines.append(_line(r))
    path = _write("holdout", lines)

    total = results["전체"]
    assert total.false_merges == 0, f"잘못 합침 {total.false_merges}건. 보고서: {path}"
    assert total.miss_rate <= MAX_MISS_RATE, f"놓침 {total.miss_rate:.1%}. 보고서: {path}"
