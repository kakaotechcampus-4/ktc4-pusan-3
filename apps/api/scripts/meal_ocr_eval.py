"""급식표 사진 OCR 채점 — 이미지 1장을 MLAPI 로 읽고 정답셋(tests/fixtures)과 비교한다.

    uv run python -m scripts.meal_ocr_eval --check          # 접속·모델 목록만 (과금 없음)
    uv run python -m scripts.meal_ocr_eval tests/fixtures/meal_plan_2025_01.jpg --repeat 5
    uv run python -m scripts.meal_ocr_eval IMAGE --model claude-sonnet-5 --effort medium

모델 출력은 --out (기본: 시스템 임시 폴더) 에 저장한다. 레포 안에 저장하지 않는다.

C1 (docs/agents/eval/MEAL_OCR_EVAL.md §2, 11-09 PM) — 정답에 없는 메뉴가 그 날짜 · 끼니의
급식으로 읽힌 끼니. 입력 하나 = 끼니 하나(날짜 × 끼니). 메뉴를 빼먹기만 한 끼니는 일반 실패다.
알레르기 번호 · 공백 차이는 실패로 세지 않는다 (10-09 부터 서비스가 번호를 읽지 않는다).
운영이 화면 · 저장에 싣는 것을 잰다 (§3) — 이름은 운영처럼 원문에서 만든 것(`names_from_raw`)을
보고, 운영이 버리는 끼니(아침 · 저녁)는 보지 않는다. 모델이 낸 이름이 깨진 횟수는 참고로만 센다.
반복이 끝나면 측정 이력에 적을 C1 `a/b` · 안정 오답 · 불안정과 실행 메타
(`tests/eval/agents/live_run_meta.json`)를 남긴다. 한 번도 못 읽으면 남기지 않고 종료 코드 1.
"""

import argparse
import json
import re
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.notice_ocr import names_from_raw
from app.core.config import settings
from app.domains.daycare_meal.models import DaycareSlot
from app.providers.meal_plan.image_ocr import (
    DEFAULT_DAY_RANGES,
    ImageOcrMealPlanReader,
    Usage,
)
from app.providers.meal_plan.schema import MealPlanJSON, MealPlanSource, MenuItem
from app.rules.allergen import parse_allergens, strip_allergen_marks
from app.rules.menu_key import menu_display_name

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "meal_plan_2025_01.json"
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
# 한 번 실행의 총 호출 상한. 측정 이력은 5회 × 2회 = 10회다 — `--repeat 50` 같은 오타를 막는다
DEFAULT_MAX_CALLS = 20

# 100만 토큰당 USD (입력, 출력). 냅킨 계산용 추정치 — 실제 과금은 MLAPI 기준.
PRICE_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "anthropic/claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "anthropic/claude-sonnet-5": (2.0, 10.0),
    "gemini-4-argon": (2.0, 10.0),  # 2026-11-30 까지의 입문가. 12월부터 구글은 (4.0, 20.0)
    "google/gemini-4-argon": (2.0, 10.0),
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "google/gemini-3.1-pro-preview": (2.0, 12.0),
}


def cost_usd(usage: Usage, model: str) -> float | None:
    """모델 단가표에 있으면 추정 비용(USD), 없으면 None. 캐시 읽기는 입력 단가의 1/10."""
    price = PRICE_USD_PER_MILLION.get(model)
    if price is None:
        return None
    in_price, out_price = price
    fresh = usage.prompt_tokens - usage.cached_prompt_tokens
    cost = fresh * in_price + usage.cached_prompt_tokens * in_price / 10
    cost += usage.completion_tokens * out_price
    return cost / 1_000_000


def load_fixture(path: Path) -> MealPlanJSON:
    """정답셋 JSON → MealPlanJSON. `_` 로 시작하는 키(메모)는 뺀다."""
    data = {
        k: v
        for k, v in json.loads(path.read_text(encoding="utf-8")).items()
        if not k.startswith("_")
    }
    data.setdefault("source", MealPlanSource.IMAGE.value)
    return MealPlanJSON.model_validate(data)


def _meals(plan: MealPlanJSON) -> dict[tuple[int, str], list[MenuItem]]:
    """(날짜, 끼니) → 항목들. 한 날짜에 같은 끼니가 두 번 나오면 이어 붙인다.

    스키마는 같은 날짜가 두 번 나오는 것만 거절하고, 운영은 두 끼니를 다 화면에 싣는다 —
    하나로 덮으면 앞 끼니의 틀린 메뉴가 채점에서 사라진다.
    """
    meals: dict[tuple[int, str], list[MenuItem]] = {}
    for d in plan.days:
        for m in d.meals:
            meals.setdefault((d.day, m.meal_type.value), []).extend(m.items)
    return meals


def _by_meal(plan: MealPlanJSON) -> dict[tuple[int, str], list[str]]:
    return {key: [it.raw for it in items] for key, items in _meals(plan).items()}


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


Slot = tuple[int, str]  # (날짜, 끼니)


_PARENS = re.compile(r"[()（）]")
_EDGE = ",.·/"

# 같은 음식의 표기 차이 (틀린 표기 → 바른 표기). 사람이 사진을 보고 같은 메뉴라고 확인한 것만 둔다.
# 글자 거리로 묶지 않는 이유 — 현미밥 / 흑미밥 처럼 한 글자만 달라도 다른 메뉴다.
SAME_MENU_SPELLING: dict[str, str] = {
    "샤인머스켓": "샤인머스캣",  # 11-09 첫 측정
    "물만두국": "물만둣국",  # 11-09 첫 측정
}


def menu_key(raw: str) -> str:
    """C1 채점용 메뉴 이름. 같은 음식이면 같은 값이 나온다.

    알레르기 번호를 떼고, 괄호로 나눈 조각마다 공백 · ★ · 칸 끝 쉼표를 지운 뒤 순서 없이 묶는다
    — `백김치(백깍두기)` 와 `백깍두기(백김치)` 는 같은 메뉴다. 표기 차이는 `SAME_MENU_SPELLING` 만.
    """
    parts = []
    for part in _PARENS.split(strip_allergen_marks(raw)):
        text = normalize_raw(part).strip(_EDGE)
        for wrong, right in SAME_MENU_SPELLING.items():
            text = text.replace(wrong, right)
        if text:
            parts.append(text)
    return "|".join(sorted(parts))


@dataclass(frozen=True)
class SlotGrade:
    """끼니 하나의 채점. 원문은 사람이 읽으라고 남긴다."""

    wrong: list[str] = field(default_factory=list)  # 정답에 없는 메뉴 (모델 원문)
    missing: list[str] = field(default_factory=list)  # 모델이 빼먹은 메뉴 (정답 원문)
    garbled: list[str] = field(default_factory=list)  # 맞는 메뉴인데 이름에 찌꺼기가 붙음

    @property
    def c1(self) -> bool:
        return bool(self.wrong)

    @property
    def failed(self) -> bool:
        return self.c1 or bool(self.missing) or bool(self.garbled)


_MENU_SEP = re.compile(r",|또는")


def menu_keys(raw: str) -> set[str]:
    """항목 하나에 든 메뉴들의 이름. 쉼표 · `또는` 으로 나눈다.

    정답셋은 "…,우유2" 를 항목 2개로 나누고 "A 또는 B" 는 한 항목으로 두는데, 모델이 붙이거나
    나눠도 메뉴가 같으면 같게 본다. 알레르기 번호 사이 쉼표는 번호를 먼저 떼서 나누지 않는다.
    번호를 떼는 것과 기호 정리(끝의 ` · 겹친 && 등)는 운영이 이름을 만들 때와 같은 함수
    (`menu_display_name`)로 한다 — 운영 화면에서 같은 이름이면 같은 메뉴다. 글자가 다르면 다르다.
    """
    keys = {menu_key(part) for part in _MENU_SEP.split(menu_display_name(raw))}
    return keys - {""}


_WORD_SEP = re.compile(r"또는|[^가-힣A-Za-z]+")


def _letters(text: str) -> str:
    """글자만 남긴다 — 번호 · 기호 · 공백 · 괄호를 빼고 표기 차이를 맞춘다."""
    text = _WORD_SEP.sub("", text).replace("쨈", "잼")
    for wrong, right in SAME_MENU_SPELLING.items():
        text = text.replace(wrong, right)
    return text


_WORD = re.compile(r"[가-힣]+|[A-Za-z]+")
_HANGUL = re.compile(r"[가-힣]")


def stray_words(name: str, raw: str) -> list[str]:
    """이름(`name`)의 낱말 가운데 제 원문(`raw`)에 없는 것.

    운영은 이름을 원문에서 만들어(`names_from_raw`) 이 값이 늘 비지만, 채점은 받은 이름 그대로
    본다 — 모델 이름의 깨짐(`model_name_breaks`)을 세는 데도 쓴다. 이름은 원문에서 번호 · 분량 ·
    대체 메뉴를 뺀 것이라 원문의 일부여도 맞다 — `백김치9(백깍두기9)` 의 이름 `백김치`.
    낱말은 한글 · 영문이 이어진 덩어리로 끊는다 (`증편Riot` → `증편` · `Riot`).
    """
    whole = _letters(raw)
    return [word for word in _WORD.findall(name) if _letters(word) not in whole]


def is_another_menu(stray: list[str]) -> bool:
    """원문에 없는 낱말이 다른 음식 이름인가 (C1), 깨진 출력의 찌꺼기인가 (일반 실패).

    한글 낱말이면 다른 음식으로 본다 — 원문 `현미밥` 에 이름 `흑미밥`. 한글이 아닌 것뿐이면
    찌꺼기다 — `백김치(백깍두기 Riot`. 찌꺼기가 한글이면 다른 음식으로 잡힌다 — 헷갈릴 때 엄하게
    세는 쪽이다. 운영은 이 이름을 쓰지 않는다(#908) — 모델 이름 깨짐(참고)의 종류를 가르는 데 쓴다.
    """
    return any(_HANGUL.search(word) for word in stray)


# 운영이 화면 · 저장에 싣는 끼니. 아침 · 저녁은 `daycare_meal` 에 자리가 없어 버린다
SERVED = frozenset(slot.value for slot in DaycareSlot)


def grade_slots(expected: MealPlanJSON, actual: MealPlanJSON) -> dict[Slot, SlotGrade]:
    """정답 끼니 전부 + 정답에 없는데 모델이 메뉴를 낸 끼니를 채점한다.

    모델 출력은 운영이 싣는 끼니(`SERVED`)만 본다 — 저녁으로 간 메뉴는 저장되지 않으니 틀린 메뉴가
    남지 않고, 제 끼니에서 빼먹은 것으로 잡힌다. 메뉴 이름이 없는 끼니(번호뿐)는 세지 않는다.
    항목은 원문과 이름이 둘 다 맞아야 한다. 이름이 다른 음식을 말하면 틀린 메뉴(C1), 맞는 음식에
    찌꺼기만 붙었으면 이름 깨짐(일반 실패)이다 (`stray_words` · `is_another_menu`).
    """
    exp = _by_meal(expected)
    act = {key: items for key, items in _meals(actual).items() if key[1] in SERVED}
    named = {key for key, items in act.items() if any(menu_keys(it.raw) for it in items)}
    graded: dict[Slot, SlotGrade] = {}
    for key in sorted(set(exp) | named):
        exp_raws, items = exp.get(key, []), act.get(key, [])
        exp_keys = set().union(*(menu_keys(r) for r in exp_raws))
        act_keys = set().union(*(menu_keys(it.raw) for it in items))
        wrong = [it.raw for it in items if menu_keys(it.raw) - exp_keys]
        garbled: list[str] = []
        for it in items:
            stray = stray_words(it.name, it.raw)
            if stray:
                bucket = wrong if is_another_menu(stray) else garbled
                bucket.append(f"{it.raw} (이름: {it.name})")
        graded[key] = SlotGrade(
            wrong=wrong,
            missing=[r for r in exp_raws if menu_keys(r) - act_keys],
            garbled=garbled,
        )
    return graded


def model_name_breaks(plan: MealPlanJSON) -> list[str]:
    """모델이 낸 이름(`name`) 중 제 원문에 없는 낱말이 든 항목 — 참고 지표.

    운영은 이 이름을 쓰지 않고 원문에서 만든다(`names_from_raw`). C1 · 실패에 넣지 않고 프롬프트가
    바뀔 때 모델 출력이 나빠졌는지만 본다. 운영이 싣는 끼니(`SERVED`)만 센다.
    원문에 없는 낱말(`stray_words`)과 원문에 없는 기호(`}{"` 같은 것, 11-11 측정)를 다 센다.
    """
    breaks = []
    for key, items in _meals(plan).items():
        if key[1] not in SERVED:
            continue
        for it in items:
            stray = stray_words(it.name, it.raw)
            symbols = {ch for ch in it.name if not (ch.isalnum() or ch.isspace())} - set(it.raw)
            if stray or symbols:
                kind = " · 다른 음식 낱말" if is_another_menu(stray) else ""
                breaks.append(f"{it.raw} (이름: {it.name}){kind}")
    return breaks


def failed_run(expected: MealPlanJSON) -> MealPlanJSON:
    """읽기에 실패한 회차 — 아무것도 저장되지 않으니 모든 끼니를 빼먹은 것(일반 실패)으로 센다."""
    return MealPlanJSON(source=expected.source, year_month=expected.year_month, days=[])


@dataclass(frozen=True)
class RepeatSummary:
    """같은 사진을 R번 읽은 결과. 측정 이력 행의 C1 · 안정 오답 · 불안정 칸."""

    runs: int
    c1_runs: dict[Slot, int]  # 끼니마다 C1 이 나온 횟수
    fail_runs: dict[Slot, int]  # 끼니마다 실패(C1 · 빼먹음 · 이름 깨짐)한 횟수
    # 끼니마다 출력할 채점 — C1 이 있었으면 마지막 C1, 없으면 마지막 실패
    last: dict[Slot, SlotGrade]

    @property
    def total(self) -> int:
        return len(self.fail_runs)

    @property
    def c1_cases(self) -> int:
        """한 번이라도 C1 이 나온 끼니 수 — 일부 실행에서만 나와도 빼지 않는다."""
        return sum(1 for n in self.c1_runs.values() if n)

    @property
    def stable_wrong(self) -> list[Slot]:
        return sorted(k for k, n in self.fail_runs.items() if n == self.runs)

    @property
    def unstable(self) -> list[Slot]:
        return sorted(k for k, n in self.fail_runs.items() if 0 < n < self.runs)


def summarize_runs(expected: MealPlanJSON, runs: Sequence[MealPlanJSON]) -> RepeatSummary:
    """끼니 = 정답 끼니 + 어느 실행에서든 정답에 없는데 생긴 끼니.

    그 끼니가 안 생긴 실행은 맞은 것으로 센다.
    """
    c1_runs: Counter[Slot] = Counter()
    fail_runs: Counter[Slot] = Counter()
    last: dict[Slot, SlotGrade] = {}
    slots: set[Slot] = set()
    for actual in runs:
        for key, g in grade_slots(expected, actual).items():
            slots.add(key)
            c1_runs[key] += g.c1
            fail_runs[key] += g.failed
            # C1 채점은 뒤 회차의 빼먹음 · 읽기 실패로 덮지 않는다 — 무엇이 틀렸는지 남아야 한다
            if g.c1 or (g.failed and not (key in last and last[key].c1)):
                last[key] = g
    return RepeatSummary(
        runs=len(runs),
        c1_runs={k: c1_runs[k] for k in sorted(slots)},
        fail_runs={k: fail_runs[k] for k in sorted(slots)},
        last=last,
    )


def _slot(key: Slot) -> str:
    day, meal = key
    return f"{day:>2}일 {meal:<8}"


def print_c1_report(s: RepeatSummary) -> None:
    print(f"C1 (정답에 없는 메뉴가 그 끼니로 읽힘) : {s.c1_cases}/{s.total} 끼니 · 반복 {s.runs}회")
    stable, unstable = len(s.stable_wrong), len(s.unstable)
    print(f"안정 오답 {stable} · 불안정 {unstable}  (실패 = C1 · 메뉴 빼먹음 · 이름 깨짐)")
    for key, n in s.fail_runs.items():
        if not n:
            continue
        g = s.last[key]
        if s.c1_runs[key]:
            mark = f"C1 {s.c1_runs[key]}/{s.runs}"
        else:
            kinds = (("빼먹음", g.missing), ("이름 깨짐", g.garbled))
            mark = " · ".join(name for name, hit in kinds if hit)
        line = f"  {_slot(key)} 실패 {n}/{s.runs} · {mark} · 틀림 {g.wrong} · 빠짐 {g.missing}"
        print(line + (f" · 깨짐 {g.garbled}" if g.garbled else ""))


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


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("image", nargs="?", type=Path)
    ap.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    ap.add_argument("--model", default=settings.MEAL_OCR_MODEL)
    ap.add_argument(
        "--base-url", default=settings.MEAL_OCR_BASE_URL, help="모델마다 엔드포인트가 다름"
    )
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument(
        "--whole",
        action="store_true",
        help="날짜를 나누지 않고 한 번에 (출력 상한 6,000 에 걸릴 수 있음)",
    )
    ap.add_argument("--cache-write", default=None, choices=[None, "off", "5m", "1h"])
    ap.add_argument("--out", type=Path, default=None, help="모델 출력 폴더 (기본: 임시 폴더)")
    ap.add_argument("--repeat", type=int, default=1, help="같은 사진을 몇 번 읽나 (측정 이력은 5)")
    ap.add_argument(
        "--max-calls",
        type=int,
        default=DEFAULT_MAX_CALLS,
        help="한 번 실행의 총 호출 상한 — 예산을 넘기면 팀 키가 삭제된다",
    )
    ap.add_argument("--check", action="store_true", help="접속·모델 목록만 확인 (과금 없음)")
    args = ap.parse_args(argv)
    if args.repeat < 1:
        ap.error("--repeat 는 1 이상")
    calls_per_run = 1 if args.whole else len(DEFAULT_DAY_RANGES)
    # reader 의 호출 상한은 회차마다 새로 만들어져 실행 전체를 막지 못한다 — 여기서 한 번 더 막는다
    if args.repeat * calls_per_run > args.max_calls:
        ap.error(
            f"호출 {args.repeat * calls_per_run}회(반복 {args.repeat} × {calls_per_run})가"
            f" --max-calls {args.max_calls} 를 넘는다. 일부러 늘리려면 --max-calls 를 같이 준다"
        )

    if not args.base_url or not settings.MEAL_OCR_API_KEY:
        print(
            "apps/api/.env 에 MEAL_OCR_BASE_URL 과 MEAL_OCR_API_KEY 가 필요합니다"
            " (.env.example 참고)",
            file=sys.stderr,
        )
        return 2
    day_ranges = None if args.whole else DEFAULT_DAY_RANGES

    def new_reader() -> ImageOcrMealPlanReader:
        # 호출 상한(max_calls)이 reader 마다 있어 반복할 때마다 새로 만든다
        return ImageOcrMealPlanReader(
            args.base_url,
            settings.MEAL_OCR_API_KEY,
            model=args.model,
            reasoning_effort=args.effort,
            max_calls=len(day_ranges) if day_ranges else 1,
            cache_write=args.cache_write,
            day_ranges=day_ranges,
        )

    if args.check:
        models = new_reader().list_models()
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
        f" · 반복 {args.repeat}회 × 호출 {calls_per_run}회"
    )

    expected = load_fixture(args.fixture)
    out_dir = args.out or Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"meal_ocr_{args.model.replace('/', '_')}_{args.effort}"
    plans: list[MealPlanJSON] = []  # 채점용 — 읽기 실패 회차는 `failed_run` 으로 채운다
    read: list[MealPlanJSON] = []  # 실제로 읽힌 회차만
    name_breaks: list[int] = []  # 읽힌 회차마다 모델 이름 깨짐(참고) 항목 수
    total_cost = 0.0
    for n in range(1, args.repeat + 1):
        reader = new_reader()
        t0 = time.perf_counter()
        plan, error = None, ""
        try:
            plan = reader.read(image_bytes, mime_type=mime)
        except Exception as err:  # 출력 상한 · 호출 실패 — 앞 회차 비용을 버리지 않고 계속 잰다
            error = f"{type(err).__name__}: {str(err)[:150]}"
        elapsed = time.perf_counter() - t0

        # 사용량은 실패한 회차도 센다 — 잘리거나 거절된 응답도 과금된다 (`image_ocr._call`)
        u = reader.total_usage
        usage_line = f"[{n}/{args.repeat}] {elapsed:.1f}초 · {reader.calls}회 호출"
        if u.prompt_tokens:
            usage_line += f" · 입력 {u.prompt_tokens} (캐시 {u.cached_prompt_tokens})"
            usage_line += f" · 출력 {u.completion_tokens} 토큰"
            cost = cost_usd(u, args.model)
            if cost is not None:
                total_cost += cost
                usage_line += f" · 추정 ${cost:.3f}"
        if plan is None:
            plans.append(failed_run(expected))
            print(f"{usage_line} · 읽기 실패 {error}")
            continue
        # 저장 파일은 모델 출력 그대로다 — 다시 채점할 때 모델 이름 깨짐도 다시 셀 수 있다
        name_breaks.append(len(model_name_breaks(plan)))
        served = names_from_raw(plan)  # 운영과 같은 이름 (notice_ocr.read_photo)
        plans.append(served)
        read.append(served)
        out = out_dir / f"{stem}_run{n}.json"
        out.write_text(plan.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")
        print(f"{usage_line} · 저장 {out}")
    if total_cost:
        print(f"추정 합계 ${total_cost:.3f} (약 {total_cost * 1400:,.0f}원)")

    failures = len(plans) - len(read)
    if not read:
        # 읽기 실패는 틀린 메뉴를 남기지 않아 C1 0 으로 나온다. 한 번도 못 읽었으면 잰 것이 아니다
        print(
            "\n읽기가 전부 실패했다 — 잰 것이 없어 측정 이력 칸 · 실행 메타를 남기지 않는다",
            file=sys.stderr,
        )
        return 1
    print("\n── 참고 지표 (마지막으로 읽힌 실행) ──")
    print_report(grade(expected, read[-1]))
    print("\n── 측정 이력 칸 ──")
    print_c1_report(summarize_runs(expected, plans))
    print(
        f"모델 이름 깨짐(참고) : {sum(name_breaks)}항목 · 회차별 {name_breaks}"
        "  (운영은 원문에서 이름을 만들어 화면 · 저장에 가지 않는다)"
    )
    if failures:
        print(
            f"읽기 실패 {failures}/{args.repeat}회 — 그 회차는 모든 끼니를 빼먹은 것으로 셌다."
            " 측정 이력 §3 에 같이 적는다"
        )
    _write_meta(args)
    return 0


def _write_meta(args: argparse.Namespace) -> None:
    """측정 이력 행에 적을 커밋 · 지문 · 조건. 반복 횟수와 effort 는 이 스크립트 값이다."""
    from tests.eval.agents.run_meta import META_PATH, build_meta

    meta = build_meta(
        evalset=[str(Path(__file__).relative_to(ROOT)), str(args.fixture)],
        ocr_model=args.model,
        repetitions=args.repeat,
        ocr_efforts={"meal_ocr": args.effort},
    )
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"\n실행 메타: {META_PATH.relative_to(ROOT)} — commit {meta['commit']}"
        f" · dirty {meta['dirty']} · 지문 {meta['prompts']['meal_ocr'][:12]}"
    )


if __name__ == "__main__":
    sys.exit(main())
