"""Food 메뉴 해석 라이브 eval — 실제 식품영양성분DB 로 `resolve_menu` 를 본다 (#321).

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/food/test_menu_resolution.py -m live

키: DATA_GO_KR_SERVICE_KEY(공공데이터포털). 급식표 표기만 FOOD_* 모델을 쓴다.

메뉴명 30개는 Food_Tool_명세 §0-7 의 점검 목록이다 — 테스트 · 목에 나오는 이름 25개와 소스를
비교할 때 더한 5개. 찾음 21 · 못 찾음 9 는 10-11 영양DB 기준이라, 영양DB 가 행을 더하거나 빼면
여기가 깨진다. 그때는 데이터를 확인하고 기대를 고친다.

- 하나씩: 이름이 정확히 같은 품목대표 행으로만 찾고, 못 찾는 이름을 비슷한 행으로 대신하지 않는다.
  찾은 행은 재료를 몰라서 어떤 알레르기를 등록한 아이에게도 안전 필터를 "통과" 하지 않는다.
  느린 응답과 데이터를 가르려고 호출당 30초를 준다.
- 판정 거듭하기: 실제 값(판정 한 번에 외부 조회 5회 · 호출당 8초)으로 판정을 거듭하면 시간 초과로
  빠진 메뉴도 다음 판정에서 다시 불러 결국 다 찾는지, 몇 번 걸리는지 본다(own_table FT-19).
- 급식표 표기: 모델이 쓴 급식표 표기(알레르기 번호 · 괄호 · 기호)가 원래 이름과 같은 `menu_key`
  로 모이는지 본다.

결과는 test_menu_resolution_results.jsonl 에 남긴다(키 · 주소는 남기지 않는다).
기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import json
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.agents.food.menu_resolver import (
    LOOKUP_TIMEOUT_S,
    MAX_LOOKUPS_PER_EVALUATION,
    MenuResolver,
    NotFoundMemory,
    Resolved,
    Unresolved,
)
from app.agents.food.store import InMemoryMenuCatalog
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.agents.food.tools.safety import filter_food_safety
from app.core.config import Settings
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, RawNutriRow
from app.integrations.mfds.client import MfdsNutriClient, mfds_nutri_http
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.menu_key import normalize_menu_key
from app.rules.term_match import normalize
from tests.eval.agents.food.generate import ask, require_food_model

pytestmark = pytest.mark.live

NOW = datetime(2026, 10, 11, tzinfo=UTC)
RESULTS = Path(__file__).with_name("test_menu_resolution_results.jsonl")
GENEROUS_TIMEOUT_S = 30.0  # 데이터로 찾는지만 본다 — 실제 값(8초)은 판정 거듭하기에서 본다
MAX_ROUNDS = 12  # 30개를 판정마다 5개씩 — 시간 초과가 없으면 6번이면 끝난다
VARIANTS_PER_NAME = 6
BATCH_SIZE = 10

# Food_Tool_명세 §0-7 의 점검 목록 (10-11 영양DB 기준)
FOUND = tuple(
    "현미밥 미역국 김치찌개 된장찌개 잡채 떡국 감자조림 두부조림 멸치볶음 카레라이스 김치볶음밥 "
    "우유 두유 닭곰탕 호박죽 닭강정 돈가스 단호박찜 백김치 동그랑땡 시금치나물".split()
)
_PRODUCT_ONLY = "정확히 같은 이름이 상용제품 행에만 있다"
_ABSENT = "영양DB 에 없다"
NOT_FOUND = {
    "백미밥": _PRODUCT_ONLY,
    "불고기": _PRODUCT_ONLY,
    "계란찜": _PRODUCT_ONLY,
    "두부구이": _PRODUCT_ONLY,
    "누룽지죽": _ABSENT,
    "콩나물죽": _ABSENT,
    "백깍두기": _ABSENT,
    "비빔만두": _ABSENT,
    "고구마찜": _ABSENT,
}
NAMES = (*FOUND, *NOT_FOUND)
BATCHES = [NAMES[start : start + BATCH_SIZE] for start in range(0, len(NAMES), BATCH_SIZE)]


def _service_key() -> str:
    key = Settings().DATA_GO_KR_SERVICE_KEY
    if not key:
        pytest.skip("DATA_GO_KR_SERVICE_KEY 가 없다 — apps/api/.env 에 넣고 돌린다")
    return key


def _record(kind: str, case: str, data: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"kind": kind, "case": case, **data}, ensure_ascii=False) + "\n")


def _verdict(row: MenuCatalogRow, entries: list[SafetyEntry]) -> str:
    result = filter_food_safety([row], entries, "preschool", months=60)
    return "blocked" if result.blocked else "unchecked" if result.unchecked else "passed"


class CountingSource:
    """실제 클라이언트를 감싸 이름마다 외부로 나간 횟수를 센다.

    답을 받은 이름(찾았든 못 찾았든)이 다시 나가면 `after_answer` 에 남는다 — 찾은 것은 카탈로그가,
    못 찾은 것은 기억이 걸러야 한다. 시간 초과로 끊긴 호출은 답을 받은 것으로 치지 않는다.
    """

    def __init__(self, client: MfdsNutriClient) -> None:
        self._client = client
        self.calls: Counter[str] = Counter()
        self.answered: set[str] = set()
        self.after_answer: Counter[str] = Counter()

    async def nutrition_rows(self, *, name: str) -> tuple[RawNutriRow, ...]:
        self.calls[name] += 1
        if name in self.answered:
            self.after_answer[name] += 1
        rows = await self._client.nutrition_rows(name=name)
        self.answered.add(name)
        return rows


@pytest.mark.parametrize("name", NAMES)
async def test_메뉴명_하나씩(name: str, expect: Callable[..., None]) -> None:
    if name in FOUND:
        expect(
            f"'{name}' 을 이름이 정확히 같은 영양DB 품목대표 행으로 찾는다 — "
            "핵심 영양 7칸이 다 있다",
            "찾은 행은 재료를 모른다 — 알레르기 19종 어느 것을 등록한 아이에게도, 아무것도 "
            "등록하지 않은 아이에게도 안전 필터가 '통과' 를 내지 않는다",
        )
    else:
        expect(f"'{name}' 은 찾지 못한다({NOT_FOUND[name]}) — 비슷한 행으로 대신하지 않는다")
    key = _service_key()
    timeout = httpx.Timeout(GENEROUS_TIMEOUT_S, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        resolver = MenuResolver(
            catalog=InMemoryMenuCatalog(),
            source=MfdsNutriClient(service_key=key, http=http),
            now=NOW,
            not_found=NotFoundMemory(),
            timeout_s=GENEROUS_TIMEOUT_S,
        )
        started = time.monotonic()
        result = await resolver.resolve(name)
        seconds = round(time.monotonic() - started, 1)

    if not isinstance(result, Resolved):
        _record("one", name, {"seconds": seconds, "reason": result.reason})
        assert result.reason == "not_found", f"조회 실패({result.reason}) — 데이터를 보지 못했다"
        assert name not in FOUND, f"'{name}' 을 찾지 못했다 — 영양DB 가 바뀌었는지 본다"
        return

    row = result.row
    verdicts = {"(등록 없음)": _verdict(row, [])}
    for label in ALLERGEN_NAMES.values():
        verdicts[label] = _verdict(row, [SafetyEntry(kind="allergy", label=label, status="active")])
    _record(
        "one",
        name,
        {
            "seconds": seconds,
            "display_name": row.display_name,
            "food_code": row.food_code,
            "serving_g": row.serving_g,
            "nutrients": row.nutrients,
            "blocked_by": [label for label, verdict in verdicts.items() if verdict == "blocked"],
        },
    )
    assert name in FOUND, f"'{name}' 을 찾았다({row.display_name}) — 영양DB 가 바뀌었는지 본다"
    assert normalize(row.display_name) == normalize_menu_key(name), (
        f"이름이 다른 행이다: {row.display_name}"
    )
    assert row.resolved and row.source == "mfds_nutri" and row.food_code
    missing = [nutrient for nutrient in CORE_NUTRIENT_KEYS if nutrient not in row.nutrients]
    assert not missing, f"핵심 영양 칸이 비었다: {missing}"
    passed = [label for label, verdict in verdicts.items() if verdict == "passed"]
    assert not passed, f"재료를 모르는 행이 '통과' 했다: {passed}"


async def test_실제_값으로_판정_거듭하기(expect: Callable[..., None]) -> None:
    expect(
        f"판정 한 번(외부 조회 {MAX_LOOKUPS_PER_EVALUATION}회 · 호출당 {LOOKUP_TIMEOUT_S:g}초)을 "
        f"{MAX_ROUNDS}번 안에서 거듭하면 찾을 수 있는 {len(FOUND)}개를 다 찾는다",
        "답을 받은 이름은 다시 묻지 않는다 — 찾은 것은 카탈로그가, 못 찾은 것은 기억이 거른다",
    )
    key = _service_key()
    catalog, memory = InMemoryMenuCatalog(), NotFoundMemory()
    rounds: list[dict[str, Any]] = []
    async with mfds_nutri_http() as http:
        source = CountingSource(MfdsNutriClient(service_key=key, http=http))
        for number in range(1, MAX_ROUNDS + 1):
            resolver = MenuResolver(catalog=catalog, source=source, now=NOW, not_found=memory)
            started = time.monotonic()
            got = await resolver.resolve_many(NAMES)
            reasons = Counter(r.reason for r in got.values() if isinstance(r, Unresolved))
            fetched = [r for r in got.values() if isinstance(r, Resolved) and r.origin == "fetched"]
            rounds.append(
                {
                    "round": number,
                    "seconds": round(time.monotonic() - started, 1),
                    "resolved": sum(isinstance(r, Resolved) for r in got.values()),
                    "fetched": len(fetched),
                    **{f"unresolved_{reason}": count for reason, count in reasons.items()},
                }
            )
            if not reasons["budget"] and not reasons["source_error"]:
                break

    wanted = {normalize_menu_key(name) for name in FOUND}
    resolved = {key for key, result in got.items() if isinstance(result, Resolved)}
    retried = {name: count for name, count in source.calls.items() if count > 1}
    _record(
        "rounds",
        f"{len(NAMES)}",
        {"rounds": rounds, "retried": retried, "after_answer": dict(source.after_answer)},
    )
    timeline = [(r["round"], r["resolved"], r.get("unresolved_source_error", 0)) for r in rounds]
    assert resolved == wanted, (
        f"판정 {len(rounds)}번 뒤에도 못 찾은 메뉴 {sorted(wanted - resolved)} "
        f"· (판정, 찾은 수, 시간 초과 · 장애) {timeline}"
    )
    assert not source.after_answer, f"답을 받은 이름을 다시 물었다: {dict(source.after_answer)}"


class Variant(BaseModel):
    text: str
    style: str  # 어떤 표기인지 — 실패를 읽을 때 쓴다


class MenuVariants(BaseModel):
    name: str
    variants: list[Variant]


class VariantBatch(BaseModel):
    menus: list[MenuVariants]


_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}|<[^>]*>|〈[^〉]*〉|《[^》]*》|【[^】]*】")


def _name_letters(text: str) -> str:
    """괄호 밖의 한글만. 모델이 메뉴 이름 글자를 바꾸지 않았는지 가르는 데 쓴다."""
    plain = _BRACKETED.sub("", unicodedata.normalize("NFKC", text))
    return "".join(ch for ch in plain if "가" <= ch <= "힣")


@pytest.mark.parametrize(
    "batch",
    range(len(BATCHES)),
    ids=[f"{index + 1}of{len(BATCHES)}" for index in range(len(BATCHES))],
)
async def test_급식표_표기(batch: int, expect: Callable[..., None]) -> None:
    names = BATCHES[batch]
    expect(
        f"모델이 쓴 급식표 표기(메뉴 {len(names)}개 × {VARIANTS_PER_NAME}개) 중 메뉴 이름 글자를 "
        "바꾸지 않은 것은 모두 원래 이름과 같은 menu_key 가 된다",
        "이름 글자가 바뀐 변형(괄호 밖에 낱말이 더해지거나 빠짐)은 판정하지 않고 기록만 한다",
    )
    require_food_model()
    data: VariantBatch = await ask(
        f"""아래 메뉴 이름마다, 한국 어린이집 · 유치원 월간 식단표(급식표) 칸에
실제로 적히는 모양으로 {VARIANTS_PER_NAME}개씩 바꿔 써라.

- 메뉴 이름 글자는 바꾸지 않는다 — 낱말을 더하거나 빼지 않는다(미역국 → 쇠고기미역국 은 안 된다).
- 바꾸는 것은 표기다: 알레르기 유발 식품 번호(괄호 · 원문자 · 쉼표 · 점 · 이름 뒤 숫자 · 위첨자 등),
  원산지 · 양 · 재료를 적은 괄호, 별표 · ※ 같은 기호, 띄어쓰기, 전각 문자.
- 실제 식단표에서 볼 법한 모양을 고루 섞고 같은 모양을 되풀이하지 않는다.
- name 에는 아래 이름을 그대로, style 에는 어떤 표기인지 짧게 적는다.

메뉴: {", ".join(names)}""",
        VariantBatch,
    )

    by_key = {normalize_menu_key(menu.name): menu for menu in data.menus}
    skipped = [name for name in names if normalize_menu_key(name) not in by_key]
    misses: list[str] = []
    changed: list[str] = []
    checked = 0
    for name in names:
        menu = by_key.get(normalize_menu_key(name))
        if menu is None:
            continue
        for variant in menu.variants:
            if _name_letters(variant.text) != _name_letters(name):
                changed.append(f"{variant.text} ({variant.style})")
                continue
            checked += 1
            key = normalize_menu_key(variant.text)
            if key != normalize_menu_key(name):
                misses.append(f"{variant.text} → {key!r} ({variant.style})")

    _record(
        "variants",
        f"{batch + 1}/{len(BATCHES)}",
        {"data": data.model_dump(), "misses": misses, "changed": changed, "skipped": skipped},
    )
    assert checked, f"판정할 변형이 없다 (모델이 빠뜨린 메뉴 {skipped})"
    assert not misses, f"같은 키로 모이지 않은 표기 {len(misses)}/{checked}개: {misses}"
