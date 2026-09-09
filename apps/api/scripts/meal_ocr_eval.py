"""급식표 OCR 채점 — 이미지 1장을 MLAPI 로 읽고 정답셋(tests/fixtures)과 비교한다.

    uv run python -m scripts.meal_ocr_eval --check          # 접속·모델 목록만 (과금 없음)
    uv run python -m scripts.meal_ocr_eval tests/fixtures/meal_plan_2025_01.png
    uv run python -m scripts.meal_ocr_eval IMAGE --model claude-sonnet-5 --effort medium

모델 출력은 --out (기본: 시스템 임시 폴더) 에 저장한다. 레포 안에 저장하지 않는다.
"""

import argparse
import json
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from app.core.config import settings
from app.providers.meal_ocr.mlapi import DEFAULT_DAY_RANGES, MlapiMealOcr
from app.providers.meal_ocr.schema import MealPlanJSON
from app.rules.allergen import parse_allergens

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "meal_plan_2025_01.json"
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def load_fixture(path: Path) -> MealPlanJSON:
    data = {
        k: v
        for k, v in json.loads(path.read_text(encoding="utf-8")).items()
        if not k.startswith("_")
    }
    return MealPlanJSON.model_validate(data)


def _by_meal(plan: MealPlanJSON) -> dict[tuple[int, str], list[str]]:
    return {(d.day, m.meal_type): [it.raw for it in m.items] for d in plan.days for m in d.meals}


def _codes(raws: list[str]) -> set[int]:
    return set().union(*(parse_allergens(r).codes for r in raws))


_NOISE = re.compile(r"[\s★()（）]")


def normalize_raw(raw: str) -> str:
    """채점용 정규화 — 공백·★·괄호 제거, 잼/쨈 통일. 알레르기 번호에는 영향 없는 차이만 지운다."""
    return _NOISE.sub("", raw).replace("쨈", "잼")


def _item_codes(raws: list[str]) -> Counter:
    """메뉴별 번호 묶음의 다중집합. 끼니 합집합이 놓치는 '한 메뉴에서 번호 빠짐' 을 잡는다."""
    return Counter(parse_allergens(r).codes for r in raws)


def grade(expected: MealPlanJSON, actual: MealPlanJSON) -> dict:
    exp, act = _by_meal(expected), _by_meal(actual)
    raw_hits = norm_hits = item_hits = raw_total = meal_code_hits = 0
    raw_misses: list[tuple[tuple[int, str], str, list[str]]] = []
    code_misses: list[tuple[tuple[int, str], set[int], set[int]]] = []
    item_misses: list[tuple[tuple[int, str], list[str], list[str]]] = []
    for key, exp_raws in exp.items():
        act_raws = act.get(key, [])
        raw_total += len(exp_raws)
        act_set = set(act_raws)
        act_norm = Counter(normalize_raw(r) for r in act_raws)
        for r in exp_raws:
            if r in act_set:
                raw_hits += 1
            else:
                raw_misses.append((key, r, act_raws))
            n = normalize_raw(r)
            if act_norm[n] > 0:
                norm_hits += 1
                act_norm[n] -= 1
        matched = sum((_item_codes(exp_raws) & _item_codes(act_raws)).values())
        item_hits += matched
        if matched < len(exp_raws):
            item_misses.append((key, exp_raws, act_raws))
        e_codes, a_codes = _codes(exp_raws), _codes(act_raws)
        if e_codes == a_codes:
            meal_code_hits += 1
        else:
            code_misses.append((key, e_codes, a_codes))
    unknown = [
        (r, parse_allergens(r).unknown)
        for raws in act.values()
        for r in raws
        if parse_allergens(r).unknown
    ]
    return {
        "raw_exact": (raw_hits, raw_total),
        "raw_normalized": (norm_hits, raw_total),
        "item_codes": (item_hits, raw_total),
        "meal_codes_exact": (meal_code_hits, len(exp)),
        "missing_meals": sorted(k for k in exp if k not in act),
        "extra_meals": sorted(k for k in act if k not in exp),
        "raw_misses": raw_misses,
        "code_misses": code_misses,
        "item_misses": item_misses,
        "unknown_numbers": unknown,
        "unparsed": [u.model_dump() for u in actual.unparsed],
    }


def print_report(g: dict) -> None:
    rh, rt = g["raw_exact"]
    nh, _ = g["raw_normalized"]
    ih, _ = g["item_codes"]
    ch, ct = g["meal_codes_exact"]
    print(f"메뉴 단위 알레르기 번호 일치 : {ih}/{rt} ({ih / rt:.0%})   ← DB 1행 기준. 제일 중요")
    print(f"끼니별 번호 합집합 일치     : {ch}/{ct} ({ch / ct:.0%})")
    print(f"원문 글자 그대로 일치       : {rh}/{rt} ({rh / rt:.0%})")
    print(f"원문 정규화 일치 (공백·★·괄호·잼 무시) : {nh}/{rt} ({nh / rt:.0%})")
    print(
        f"빠진 끼니 {len(g['missing_meals'])} · 남는 끼니 {len(g['extra_meals'])}"
        f" · 범위 밖 번호 {len(g['unknown_numbers'])} · unparsed {len(g['unparsed'])}"
    )
    if g["code_misses"]:
        print("\n알레르기 번호가 어긋난 끼니 (정답 → 모델):")
        for (day, mt), e, a in g["code_misses"]:
            print(f"  {day:>2}일 {mt:<8} {sorted(e)} → {sorted(a)}")
    if g["raw_misses"]:
        print(f"\n원문이 다른 항목 {len(g['raw_misses'])}개 (정답 | 모델이 낸 같은 끼니):")
        for (day, mt), r, act_raws in g["raw_misses"][:40]:
            print(f"  {day:>2}일 {mt:<8} {r!r:<32} | {act_raws}")
    if g["item_misses"]:
        print("\n메뉴 단위로 번호가 어긋난 끼니 (정답 | 모델):")
        for (day, mt), e, a in g["item_misses"]:
            print(f"  {day:>2}일 {mt:<8} {e} | {a}")
    if g["unknown_numbers"]:
        print("\n범위 밖 번호 (검수 플래그):", g["unknown_numbers"])
    if g["unparsed"]:
        print("\n모델이 못 읽었다고 한 칸:", json.dumps(g["unparsed"], ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("image", nargs="?", type=Path)
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--model", default=settings.MEAL_OCR_MODEL)
    ap.add_argument(
        "--base-url", default=settings.MLAPI_BASE_URL, help="모델마다 엔드포인트가 다름"
    )
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument(
        "--whole",
        action="store_true",
        help="날짜를 나누지 않고 한 번에 (출력 상한 6,000 에 걸릴 수 있음)",
    )
    ap.add_argument("--cache-write", default=None, choices=[None, "off", "5m", "1h"])
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--check", action="store_true", help="접속·모델 목록만 확인 (과금 없음)")
    args = ap.parse_args()

    if not args.base_url or not settings.MLAPI_API_KEY:
        print(
            "apps/api/.env 에 MLAPI_BASE_URL 과 MLAPI_API_KEY 가 필요합니다 (.env.example 참고)",
            file=sys.stderr,
        )
        return 2
    day_ranges = None if args.whole else DEFAULT_DAY_RANGES
    ocr = MlapiMealOcr(
        args.base_url,
        settings.MLAPI_API_KEY,
        model=args.model,
        reasoning_effort=args.effort,
        max_calls=len(day_ranges) if day_ranges else 1,
        cache_write=args.cache_write,
        day_ranges=day_ranges,
    )

    if args.check:
        models = ocr.list_models()
        print(f"접속 OK · 모델 {len(models)}개")
        for m in models:
            print("  ", m)
        return 0
    if args.image is None:
        ap.error("이미지 경로가 필요합니다 (또는 --check)")

    mime = MIME.get(args.image.suffix.lower())
    if mime is None:
        ap.error(f"지원하지 않는 확장자: {args.image.suffix}")
    image_bytes = args.image.read_bytes()
    print(
        f"이미지 {args.image.name} ({len(image_bytes) / 1024:.0f} KB)"
        f" · 모델 {args.model} · effort {args.effort}"
        f" · 호출 {1 if args.whole else len(DEFAULT_DAY_RANGES)}회"
    )

    t0 = time.perf_counter()
    plan = ocr.extract(image_bytes, mime)
    elapsed = time.perf_counter() - t0

    out = (
        args.out
        or Path(tempfile.gettempdir())
        / f"meal_ocr_{args.model.replace('/', '_')}_{args.effort}.json"
    )
    out.write_text(plan.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")
    u = ocr.total_usage
    usage_line = f"{elapsed:.1f}초 · {ocr.calls}회 호출"
    if u.prompt_tokens:
        usage_line += f" · 입력 {u.prompt_tokens} (캐시 {u.cached_prompt_tokens})"
        usage_line += f" · 출력 {u.completion_tokens} 토큰"
        cost = u.cost_usd(args.model)
        if cost is not None:
            usage_line += f" · 추정 ${cost:.3f} (약 {cost * 1400:,.0f}원)"
    print(usage_line)
    print(f"모델 출력 저장: {out}\n")

    print_report(grade(load_fixture(args.fixture), plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
