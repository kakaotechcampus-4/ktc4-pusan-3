"""`reference/growth_doc.yaml` — growth_doc 시드 로더 (own_table §1 · RAG_plan Growth 절).

YAML 이 원본이다. DB 가 생기면 Alembic 이 이 파일을 `doc_key` 기준으로 올리고(upsert),
그 전까지는 `InMemoryGrowthDocs.from_seed()` 가 읽어 eval · 개발에 쓴다.

모양이 틀리면 읽을 때 바로 실패한다. 공통 칸(출처 · 월령 · 상태 · 작성자/검수자)은
`common/reference_docs.parse_doc_meta` 가, Growth 칸은 여기서 본다. DB 의 CHECK 가 막는 것
(36개월 미만 습관 교정)과 `next_step_of` 가 가리킬 곳이 있는지도 여기서 막는다 —
적재 스크립트가 DB 에서 처음 터지게 두지 않는다.
내용 검사(평가 표현 · 위험 용어 · 음식 용어 · 사슬 · 월령 구간)는 테스트가 한다 —
`tests/unit/agents/growth/test_growth_doc_seed.py`.

`next_step_of` 는 YAML 에서 **앞 단계 행의 doc_key** 로 적는다. 행의 id 는 doc_key 에서 늘 같게
만들어지므로 (`doc_id`) 읽는 쪽이 같은 값으로 이어 붙인다 — DB 의 uuidv7 과는 다르다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, get_args
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agents.common.reference import load_reference
from app.agents.common.reference_docs import DocMeta, check_unique_doc_keys, parse_doc_meta
from app.agents.growth.gating import HABIT_MIN_MONTH
from app.agents.growth.store.ports import GrowthDocRow, GrowthDocType

SEED_FILE = "growth_doc.yaml"
KEY_PREFIX = "growth."
ROW_TYPES: frozenset[str] = frozenset(get_args(GrowthDocType))

# 고시 영역. 표준보육과정(0–2세)은 기본생활을 포함한 6개, 누리과정(3–5세)은 5개다
AREAS = frozenset({"basic_life", "physical_health", "communication", "social", "art", "nature"})
# `observation_routine.routine_category` 와 같은 값 집합 (DDL CHECK 와도 같다).
# 에이전트끼리 import 하지 않아서 복사했다 — 테스트가 Memory 의 enum 과 같은지 본다
ROUTINE_CATEGORIES = frozenset(
    {"self_care", "mealtime", "household_task", "social_manner", "habit", "transition"}
)
SETTINGS = frozenset({"indoor", "outdoor", "either"})

_DOMAIN_KEYS = frozenset(
    {"area", "routine_category", "trigger_tags", "materials", "setting", "next_step_of"}
)
_SEQUENCE_STEP = "routine_step"  # next_step_of 로 이어지는 것은 자립 단계뿐이다


def doc_id(doc_key: str) -> UUID:
    """`doc_key` 에서 늘 같게 만드는 id. 포트가 돌려주는 행의 id 이자 `next_step_of` 의 값."""
    return uuid5(NAMESPACE_URL, f"growth_doc:{doc_key}")


@dataclass(frozen=True)
class GrowthDocEntry:
    """시드 한 행. 공통 칸(`meta`) + Growth 칸."""

    meta: DocMeta
    area: str | None
    routine_category: str | None
    trigger_tags: tuple[str, ...]
    materials: tuple[str, ...]
    setting: Literal["indoor", "outdoor", "either"] | None
    next_step_of: str | None  # 앞 단계 행의 doc_key

    def to_row(self) -> GrowthDocRow:
        """포트가 돌려주는 모양."""
        return GrowthDocRow(
            id=doc_id(self.meta.doc_key),
            doc_key=self.meta.doc_key,
            row_type=self.meta.row_type,  # type: ignore[arg-type]  # parse 가 ROW_TYPES 로 막았다
            title=self.meta.title,
            body=self.meta.body,
            min_month=self.meta.min_month,
            max_month=self.meta.max_month,
            area=self.area,
            routine_category=self.routine_category,
            trigger_tags=self.trigger_tags,
            materials=self.materials,
            setting=self.setting,
            next_step_of=doc_id(self.next_step_of) if self.next_step_of else None,
            tags=self.meta.tags,
        )


@cache
def growth_doc_seed() -> tuple[GrowthDocEntry, ...]:
    """시드 전체(상태 무관). 서비스는 `approved` 만 사용"""
    return parse_growth_doc(load_reference(SEED_FILE))


def parse_growth_doc(data: Mapping[str, Any]) -> tuple[GrowthDocEntry, ...]:
    """TODO: 행은 별도 PR 로 채우기"""
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{SEED_FILE} 에 rows 가 없다 (아직 행이 없으면 rows: [])")
    entries = tuple(_parse_entry(raw) for raw in rows)
    check_unique_doc_keys([entry.meta for entry in entries], file=SEED_FILE)
    _check_next_steps(entries)
    return entries


def _parse_entry(raw: Mapping[str, Any]) -> GrowthDocEntry:
    meta = parse_doc_meta(
        raw, file=SEED_FILE, key_prefix=KEY_PREFIX, row_types=ROW_TYPES, domain_keys=_DOMAIN_KEYS
    )
    where = f"{SEED_FILE} 행 {meta.doc_key!r}"
    if meta.row_type == "habit_strategy" and meta.min_month < HABIT_MIN_MONTH:
        raise ValueError(
            f"{where} 의 habit_strategy 는 min_month 가 {HABIT_MIN_MONTH} 이상이어야 한다 "
            f"(DB 의 CHECK 와 같다): {meta.min_month}"
        )
    area = _optional_choice(raw, "area", AREAS, where)
    category = _optional_choice(raw, "routine_category", ROUTINE_CATEGORIES, where)
    setting = _optional_choice(raw, "setting", SETTINGS, where)
    next_step_of = raw.get("next_step_of")
    if next_step_of is not None and not (isinstance(next_step_of, str) and next_step_of):
        raise ValueError(f"{where} 의 next_step_of 는 앞 단계 행의 doc_key 여야 한다")
    return GrowthDocEntry(
        meta=meta,
        area=area,
        routine_category=category,
        trigger_tags=_string_list(raw, "trigger_tags", where),
        materials=_string_list(raw, "materials", where),
        setting=setting,  # type: ignore[arg-type]  # SETTINGS 안의 값이다
        next_step_of=next_step_of,
    )


def _optional_choice(raw: Mapping[str, Any], key: str, allowed: frozenset[str], where: str):
    value = raw.get(key)
    if value is not None and value not in allowed:
        raise ValueError(f"{where} 의 {key} {value!r} 는 정해진 값이 아니다")
    return value


def _string_list(raw: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    values = raw.get(key, [])
    if not isinstance(values, list) or not all(isinstance(v, str) and v for v in values):
        raise ValueError(f"{where} 의 {key} 는 문자열 목록이어야 한다 (없으면 [])")
    return tuple(values)


def _check_next_steps(entries: Sequence[GrowthDocEntry]) -> None:
    """앞 단계는 같은 파일 안의 다른 자립 단계 행이어야 한다. 사슬 모양은 테스트가 본다."""
    by_key = {entry.meta.doc_key: entry for entry in entries}
    for entry in entries:
        if entry.next_step_of is None:
            continue
        where = f"{SEED_FILE} 행 {entry.meta.doc_key!r} 의 next_step_of {entry.next_step_of!r}"
        previous = by_key.get(entry.next_step_of)
        if previous is None:
            raise ValueError(f"{where} 가 가리키는 행이 없다")
        if previous is entry:
            raise ValueError(f"{where} 가 자기 자신이다")
        if _SEQUENCE_STEP != entry.meta.row_type or _SEQUENCE_STEP != previous.meta.row_type:
            raise ValueError(f"{where} 는 {_SEQUENCE_STEP} 끼리만 이을 수 있다")
