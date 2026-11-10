"""`reference/weaning_menu.yaml` — 월령별 이유식 식단 표 로더 (#399).

이유기(4–11개월) 아이의 식단 추천은 레시피북(`recipe_catalog`)이 아니라 이 표에서 그 달 단계 행만
후보로 삼는다. 레시피 단계 태깅이 없어 이유기 풀이 늘 비던 문제를 지우는 자리다.

- 후보는 이유식 메뉴("쇠고기애호박죽") 한 개가 한 행이다. `weaning_menu_rows` 가 `MenuCatalogRow` 로
  바꿔 주고, 그 뒤는 레시피 행과 같은 길(안전 필터 → 가중치 → 샘플링 → 모델 선택 → 사후 필터)이다.
- 아이 기록을 인용하지 않은 추천은 이 표의 행이 `food_doc` 문서 행 근거다
  (`weaning_menu_citation`). id 는 `doc_row_id("food_doc", key)` — 문서 행이라 루트 §2
  "일반은 문서 행" 그대로이고, 새 근거 종류 · 마이그레이션이 없다. 저장 검사는 `food_doc` 근거의
  종류만 본다.
- 출처 문자열(`source_org`)은 "아이캐치 서비스 일반추천", 제목(`source_title`)은
  "월령별 이유식 추천 식단" 이다. 05 문서 카드(`entrypoint._doc_index`)가 그대로 쓴다.
  기관 자료가 아니라 팀이 직접 정리한 식단이라, 사람의 검수가 유일한 안전장치다.
- 행마다 `status` · `authored_by` · `reviewed_by` · `reviewed_at` 을 둔다(`food_doc` 과 같은 규칙).
  **approved 만** 풀과 근거 카드에 쓴다(`WeaningMenuTable.approved`). 작성자와 검수자는 달라야 한다.

모양이 틀리면 읽을 때 바로 `ValueError` 로 실패한다(`doc_seed._parse_entry` 와 같은 방식). 내용 검사
(코드가 이름 · 재료를 덮는지 · 월령 범위가 age_gates 와 같은지)는
`tests/unit/agents/food/test_weaning_menu.py` 가 한다.

🚨 이 표의 행도 `filter_food_safety` 를 통과한 것만 풀에 든다. 알레르기 · 연령 판정은 여기서
하지 않는다.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from typing import Any, Final, get_args
from uuid import UUID

from app.agents.common.reference import load_reference
from app.agents.common.reference_docs import DocStatus, doc_row_id
from app.agents.common.refs import EvidenceCitation, Ref
from app.agents.food.doc_seed import FOOD_GROUPS, TEXTURES
from app.agents.food.store.ports import MenuCatalogRow
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.menu_key import normalize_menu_key

SEED_FILE: Final = "weaning_menu.yaml"
KEY_PREFIX: Final = "food.weaning_menu."
# `MenuCatalogRow.source` 의 값. 레시피 · 생성(`manual`)과 갈라야 근거 · 출처 줄을 고른다
WEANING_MENU_SOURCE: Final = "weaning_menu"
_DOC_KIND: Final = "food_doc"
_STATUSES: Final = frozenset(get_args(DocStatus))


@dataclass(frozen=True)
class WeaningMenuEntry:
    """표 한 행. `ingredients` 는 쌀을 포함한 전체, `main_ingredients` 는 쌀을 뺀 주재료다."""

    key: str
    name: str
    min_month: int  # 이 월령부터
    max_month: int  # 이 월령 전까지 (min_month <= 월령 < max_month)
    texture: str
    ingredients: tuple[str, ...]
    main_ingredients: tuple[str, ...]
    food_groups: frozenset[str]
    allergen_codes: frozenset[int]
    status: DocStatus
    authored_by: str
    reviewed_by: str | None
    reviewed_at: date | None


@dataclass(frozen=True)
class WeaningMenuTable:
    source_org: str
    source_title: str
    note: str  # 근거 카드의 한 줄 (`RECIPE_SOURCE_INFO.note` 와 같은 자리)
    entries: tuple[WeaningMenuEntry, ...]

    @property
    def approved(self) -> tuple[WeaningMenuEntry, ...]:
        """검수를 마친 행. 풀과 근거 카드는 이것만 쓴다."""
        return tuple(entry for entry in self.entries if entry.status == "approved")


@cache
def weaning_menu_table() -> WeaningMenuTable:
    return parse_weaning_menu(load_reference(SEED_FILE))


def parse_weaning_menu(data: Mapping[str, Any]) -> WeaningMenuTable:
    source_org = _text(data, "source", SEED_FILE)
    source_title = _text(data, "title", SEED_FILE)
    note = _text(data, "note", SEED_FILE)
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{SEED_FILE} 에 rows 가 없다")
    entries = tuple(_parse_entry(raw) for raw in rows)
    seen: set[str] = set()
    for entry in entries:
        if entry.key in seen:
            raise ValueError(f"{SEED_FILE} 에 key 가 겹친다: {entry.key!r}")
        seen.add(entry.key)
    return WeaningMenuTable(source_org, source_title, note, entries)


def weaning_menu_rows(months: int, *, synced_at: datetime) -> tuple[MenuCatalogRow, ...]:
    """그 달 단계의 approved 행만, 표 순서대로. 이유기 밖 월령이면 비어 있다."""
    return tuple(
        MenuCatalogRow(
            menu_key=normalize_menu_key(entry.name),
            display_name=entry.name,
            source=WEANING_MENU_SOURCE,
            resolved=True,
            synced_at=synced_at,
            ingredients=entry.ingredients,
            main_ingredients=entry.main_ingredients,
            allergen_codes=entry.allergen_codes,
            food_groups=entry.food_groups,
            stage_min="infant_weaning",
            source_id=entry.key,
        )
        for entry in weaning_menu_table().approved
        if entry.min_month <= months < entry.max_month
    )


def weaning_menu_id(key: str) -> UUID:
    """행 키에서 늘 같게 만드는 `food_doc` 근거 id. 05 문서 조회가 같은 식을 쓴다."""
    return doc_row_id(_DOC_KIND, key)


def weaning_menu_citation(row: MenuCatalogRow) -> EvidenceCitation:
    """고른 이유식 메뉴를 일반 추천의 근거로. 개인화 추천이면 `build()` 가 버린다."""
    if row.source != WEANING_MENU_SOURCE or not row.source_id:
        raise ValueError("이유식 식단 표 행이 아니다")
    return EvidenceCitation(
        ref=Ref(kind=_DOC_KIND, id=weaning_menu_id(row.source_id)),
        note=weaning_menu_table().note,
        label=row.display_name,
    )


def _parse_entry(raw: Any) -> WeaningMenuEntry:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{SEED_FILE} 의 행은 매핑이어야 한다: {raw!r}")
    key = _text(raw, "key", SEED_FILE)
    where = f"{SEED_FILE} 행 {key!r}"
    if not key.startswith(KEY_PREFIX):
        raise ValueError(f"{where} 의 key 는 {KEY_PREFIX!r} 로 시작해야 한다")
    min_month = _month(raw, "min_month", where)
    max_month = _month(raw, "max_month", where)
    if min_month >= max_month:
        raise ValueError(f"{where} 의 min_month 가 max_month 보다 작아야 한다")
    texture = raw.get("texture")
    if texture not in TEXTURES:
        raise ValueError(f"{where} 의 texture {texture!r} 는 정해진 값이 아니다")
    ingredients = _strings(raw, "ingredients", where, required=True)
    main_ingredients = _strings(raw, "main_ingredients", where, required=True)
    food_groups = frozenset(_strings(raw, "food_groups", where))
    if unknown := sorted(food_groups - FOOD_GROUPS):
        raise ValueError(f"{where} 의 food_groups 에 정해지지 않은 값이 있다: {unknown}")
    status = raw.get("status")
    if status not in _STATUSES:
        raise ValueError(f"{where} 의 status {status!r} 는 정해진 값이 아니다")
    authored_by = _text(raw, "authored_by", where).strip()
    reviewed_by = _reviewer(raw, where)
    reviewed_at = raw.get("reviewed_at")
    if reviewed_by is not None and reviewed_by == authored_by:
        raise ValueError(f"{where} 는 작성자와 검수자가 같다")
    if status == "approved" and not (reviewed_by and isinstance(reviewed_at, date)):
        raise ValueError(f"{where} 는 approved 인데 검수자 · 검수일이 없다")
    return WeaningMenuEntry(
        key=key,
        name=_text(raw, "name", where),
        min_month=min_month,
        max_month=max_month,
        texture=texture,
        ingredients=ingredients,
        main_ingredients=main_ingredients,
        food_groups=food_groups,
        allergen_codes=_codes(raw, where),
        status=status,
        authored_by=authored_by,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at if isinstance(reviewed_at, date) else None,
    )


def _text(raw: Mapping[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} 의 {key} 는 비지 않은 문자열이어야 한다")
    return value


def _reviewer(raw: Mapping[str, Any], where: str) -> str | None:
    value = raw.get("reviewed_by")
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{where} 의 reviewed_by 는 GitHub 아이디(문자열)여야 한다")
    return value.strip() or None


def _month(raw: Mapping[str, Any], key: str, where: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{where} 의 {key} 는 0 이상의 정수여야 한다")
    return value


def _strings(
    raw: Mapping[str, Any], key: str, where: str, *, required: bool = False
) -> tuple[str, ...]:
    values = raw.get(key, [])
    if (
        not isinstance(values, list)
        or not all(isinstance(v, str) and v for v in values)
        or (required and not values)
    ):
        raise ValueError(
            f"{where} 의 {key} 는 {'비지 않은 ' if required else ''}문자열 목록이어야 한다"
        )
    return tuple(values)


def _codes(raw: Mapping[str, Any], where: str) -> frozenset[int]:
    values = raw.get("allergen_codes", [])
    if not isinstance(values, list) or not all(
        isinstance(v, int) and not isinstance(v, bool) and v in ALLERGEN_NAMES for v in values
    ):
        raise ValueError(f"{where} 의 allergen_codes 는 19종 코드(1~19) 목록이어야 한다")
    return frozenset(values)
