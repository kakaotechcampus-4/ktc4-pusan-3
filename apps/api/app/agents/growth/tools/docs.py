"""문서 행 조회 (코드 tool) — `search_growth_doc`.

run 시작에 코드가 한 번 부른다. 결과는 프롬프트 `[예시]` 구획에 들어간다. 모델에게 검색 tool 은
없다 — 쿼리를 자유 문장으로 만들면서 보호자 발화를 넣을 수 있고, 조회를 했는지 테스트로 고정하기
어렵다 (Growth_Agent_명세.md §5).

    쿼리      라벨 · 단계 · 관심사 `merge_key` · 루틴 카테고리로만 조립한다. 발화를 받는 인자가 없다
    종류      `row_type` 은 라벨 · 월령 · 카테고리에서 코드가 정한다 (`doc_row_type`)
    두 번째 관문  포트가 월령 · 종류를 걸러 줘도 한 번 더 건다. 월령 밖 행은 안 나간다
              — 비면 호출부가 `doc.no_row` 로 일반 템플릿을 낸다
    근거      문서 행은 참고다. `Ref(kind='growth_doc')` 로만 싣고 개인화 근거로 세지 않는다
              (`count_child_records` 가 뺀다)

자립 단계는 검색에 걸린 행 하나로 끝나지 않는다. `load_routine_chain` 이 그 행의 사슬을 통째로
읽어 `pick_next_step` 에 넘긴다 — top-3 에는 사슬이 다 오지 않는다.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.common.refs import Ref
from app.agents.growth.doc_seed import ROUTINE_CATEGORIES, chain_key
from app.agents.growth.gating import routine_mode
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.ports import GrowthDocReader, GrowthDocRow, GrowthDocType
from app.agents.growth.tools.routine import ordered_chain
from app.rules.age import stage_of

# 프롬프트 [예시]에 싣는 행 수 (RAG_plan §7 — Growth 3)
DOC_LIMIT = 3

# 상황 태그는 영문 식별자다 (bored · anxious · tired …). 발화 한 줄이 필터 · 쿼리로 안 흘러가게 한다
_TRIGGER_TAG = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class DocHit:
    """조회된 문서 행 하나. 참고일 뿐 개인화 근거가 아니다."""

    row: GrowthDocRow

    @property
    def ref(self) -> Ref:
        return Ref(kind="growth_doc", id=self.row.id)


def doc_row_type(
    task_type: GrowthTaskType, months: int, routine_category: str | None
) -> GrowthDocType:
    """라벨 · 월령 · 카테고리가 읽을 row_type 을 정한다. 모델이 고르지 않는다.

    `growth_review` 는 모델 0회 · tool 0개라 문서를 조회하지 않는다(Growth_Agent_명세 §5) —
    `ValueError`. `measure_guide` 는 `delta.need_more` 옆에 붙는 행이라 이 경로로 읽지 않는다.
    """
    if task_type is GrowthTaskType.LEARNING_SUGGESTION:
        return "learning_activity"
    if task_type is GrowthTaskType.BOOK_SUGGESTION:
        return "book_guide"
    if task_type is GrowthTaskType.GROWTH_REVIEW:
        raise ValueError(f"{task_type.value} 는 문서를 조회하지 않는다")
    mode = routine_mode(months, routine_category)
    if mode == "rhythm_info":
        return "rhythm_info"
    if mode == "habit_fix":
        return "habit_strategy"
    return "manner_practice" if routine_category == "social_manner" else "routine_step"


def build_doc_query(
    task_type: GrowthTaskType,
    months: int,
    interest_keys: Sequence[str],
    routine_category: str | None,
) -> str:
    """의미 검색 쿼리. 코드가 아는 값만 이어 붙인다 — 보호자 원문은 들어올 길이 없다."""
    parts = [task_type.value, stage_of(months), *interest_keys]
    if routine_category:
        parts.append(routine_category)
    return " ".join(parts)


async def search_growth_doc(
    docs: GrowthDocReader,
    *,
    task_type: GrowthTaskType,
    months: int,
    interest_keys: Sequence[str] = (),
    routine_category: str | None = None,
    trigger_tags: Sequence[str] = (),
) -> tuple[DocHit, ...]:
    """월령 슬라이스 → 의미 검색 상위 `DOC_LIMIT` 행. 없으면 빈 튜플.

    `interest_keys` 는 `profile_affinity` 의 `merge_key` 다. `trigger_tags` 는 습관 교정이 읽는 상황
    태그라서 영문 식별자만 받는다. 모르는 `routine_category` 와 식별자가 아닌 태그는 `ValueError` —
    포트까지 가지 않는다.
    """
    if routine_category is not None and routine_category not in ROUTINE_CATEGORIES:
        raise ValueError(f"routine_category 가 정해진 값이 아니다: {routine_category!r}")
    if not all(_TRIGGER_TAG.fullmatch(tag) for tag in trigger_tags):
        raise ValueError("trigger_tags 는 영문 소문자 식별자만 받는다")

    row_type = doc_row_type(task_type, months, routine_category)
    rows = await docs.search(
        months=months,
        row_type=row_type,
        routine_category=routine_category,
        trigger_tags=tuple(trigger_tags),
        query=build_doc_query(task_type, months, interest_keys, routine_category),
        limit=DOC_LIMIT,
    )
    served = (r for r in rows if r.row_type == row_type and r.min_month <= months < r.max_month)
    return tuple(DocHit(row) for row in served)[:DOC_LIMIT]


async def load_routine_chain(docs: GrowthDocReader, row: GrowthDocRow) -> tuple[GrowthDocRow, ...]:
    """`row` 가 속한 자립 단계 사슬을 처음부터 끝까지 세운다. `pick_next_step` 에 그대로 넘긴다.

    사슬은 doc_key 의 사슬 이름으로 읽는다(`doc_seed.chain_key`).
    자립 단계 행이 아니면 `ValueError`,
    사슬이 끊겼거나 갈라졌으면 `ChainError` — 적재 검사가 먼저 막는다.
    """
    if row.row_type != "routine_step":
        raise ValueError(f"자립 단계 행이 아니다: {row.doc_key}")
    return ordered_chain(await docs.chain(chain_key=chain_key(row.doc_key)))
