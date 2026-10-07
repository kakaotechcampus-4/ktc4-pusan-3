"""`reference/activity_doc.yaml` — activity_doc 시드 로더 (D9 · RAG_plan Activity 절).

YAML 이 원본이다. DB 가 생기면 Alembic 이 이 파일을 `doc_key` 기준으로 올리고(upsert),
그 전까지는 `InMemoryActivityDocs.from_seed()` 가 읽어 eval · 개발에 쓴다.

모양이 틀리면 읽을 때 바로 실패한다. 공통 칸(출처 · 월령 · 상태 · 작성자/검수자)은
`common/reference_docs.parse_doc_meta` 가, 출력 후보와 같은 칸과 고시 영역은 여기서 본다.
내용 검사(위험 용어 · 보호자 역할 · 평가 표현 · 커버리지)는 테스트가 한다 —
`tests/unit/agents/activity/test_activity_doc_seed.py`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.agents.activity.schemas.common import ActivitySetting, CaregiverRole, Intensity
from app.agents.activity.store.ports import ActivityDocRow
from app.agents.common.reference import load_reference
from app.agents.common.reference_docs import DocMeta, check_unique_doc_keys, parse_doc_meta

SEED_FILE = "activity_doc.yaml"
KEY_PREFIX = "activity."
ROW_TYPES = frozenset({"play_idea"})

# 고시 영역. 표준보육과정(0–2세)은 기본생활을 포함한 6개, 누리과정(3–5세)은 5개라
# 신체운동 · 신체운동·건강은 physical_health 하나로 묶는다
AREAS = frozenset({"basic_life", "physical_health", "communication", "social", "art", "nature"})

_DOMAIN_KEYS = frozenset(
    {"setting", "materials", "caregiver_role", "physical_intensity", "involves_food", "area"}
)


@dataclass(frozen=True)
class ActivityDocEntry:
    """시드 한 행. 공통 칸(`meta`) + 출력 후보와 같은 칸 + 고시 영역."""

    meta: DocMeta
    setting: ActivitySetting
    materials: tuple[str, ...]
    caregiver_role: CaregiverRole
    physical_intensity: Intensity
    involves_food: bool
    area: str

    def to_row(self) -> ActivityDocRow:
        """포트가 돌려주는 모양. `id` 는 doc_key 로 늘 같게 만든다 — DB 의 uuidv7 과는 다르다."""
        return ActivityDocRow(
            id=uuid5(NAMESPACE_URL, f"activity_doc:{self.meta.doc_key}"),
            doc_key=self.meta.doc_key,
            title=self.meta.title,
            body=self.meta.body,
            min_month=self.meta.min_month,
            max_month=self.meta.max_month,
            setting=self.setting,
            materials=self.materials,
            caregiver_role=self.caregiver_role,
            physical_intensity=self.physical_intensity,
            involves_food=self.involves_food,
        )


@cache
def activity_doc_seed() -> tuple[ActivityDocEntry, ...]:
    """시드 전체(상태 무관). 서비스는 `approved` 만 쓴다 — 거르는 건 읽는 쪽이다."""
    return parse_activity_doc(load_reference(SEED_FILE))


def parse_activity_doc(data: Mapping[str, Any]) -> tuple[ActivityDocEntry, ...]:
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{SEED_FILE} 에 rows 가 없다")
    entries = tuple(_parse_entry(raw) for raw in rows)
    check_unique_doc_keys([entry.meta for entry in entries], file=SEED_FILE)
    return entries


def _parse_entry(raw: Mapping[str, Any]) -> ActivityDocEntry:
    meta = parse_doc_meta(
        raw, file=SEED_FILE, key_prefix=KEY_PREFIX, row_types=ROW_TYPES, domain_keys=_DOMAIN_KEYS
    )
    where = f"{SEED_FILE} 행 {meta.doc_key!r}"
    materials = raw.get("materials")
    if not isinstance(materials, list) or not all(isinstance(m, str) and m for m in materials):
        raise ValueError(f"{where} 의 materials 는 문자열 목록이어야 한다 (없으면 [])")
    involves_food = raw.get("involves_food")
    if not isinstance(involves_food, bool):
        raise ValueError(f"{where} 의 involves_food 는 true / false 여야 한다")
    area = raw.get("area")
    if area not in AREAS:
        raise ValueError(f"{where} 의 area {area!r} 는 정해진 값이 아니다")
    try:
        setting = ActivitySetting(raw.get("setting"))
        caregiver_role = CaregiverRole(raw.get("caregiver_role"))
        intensity = Intensity(raw.get("physical_intensity"))
    except ValueError as exc:
        raise ValueError(f"{where} 의 setting · caregiver_role · physical_intensity 값: {exc}")
    return ActivityDocEntry(
        meta=meta,
        setting=setting,
        materials=tuple(materials),
        caregiver_role=caregiver_role,
        physical_intensity=intensity,
        involves_food=involves_food,
        area=area,
    )
