"""문서 행 조회 (코드 tool) — `search_growth_doc`.

run 시작에 코드가 한 번 부른다. 결과는 프롬프트 `[예시]` 구획에 들어간다. 모델에게 검색 tool 은
없다 — 쿼리를 자유 문장으로 만들면서 보호자 발화를 넣을 수 있고, 조회를 했는지 테스트로 고정하기
어렵다 (Growth_Agent_명세.md §5).

    쿼리      라벨 · 단계 · 관심사 `merge_key` · 루틴 카테고리 · 주제 영역 키로만 조립한다. 발화를
              받는 인자가 없다 — 주제는 코드가 읽은 영역 키(`rules/growth_topic`)로만 온다
    종류      `row_type` 은 라벨 · 월령 · 카테고리에서 코드가 정한다 (`doc_row_type`)
    두 번째 관문  포트가 월령 · 종류 · 영역을 걸러 줘도 한 번 더 건다. 월령 밖 행은 안 나간다
              — 비면 호출부가 `doc.no_row` 로 일반 템플릿을 낸다
    근거      문서 행은 참고다. `Ref(kind='growth_doc')` 로만 싣고 개인화 근거로 세지 않는다
              (`count_child_records` 가 뺀다)

자립 단계는 검색에 걸린 행 하나로 끝나지 않는다. `load_routine_chain` 이 그 행의 사슬을 통째로
읽어 `pick_next_step` 에 넘긴다 — top-3 에는 사슬이 다 오지 않는다.

예절 · 습관은 사슬이 없다. `strategy_rows` 가 행 하나 = 연습 · 대응 방법 하나로 고른다 — 예절은 행
이름이 관찰 subject 와 맞는 행, 습관은 관찰 trigger 에서 코드가 읽은 상황 태그가 겹치는 행이다.
"""

import hashlib
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.agents.common.age import stage_of
from app.agents.common.refs import Ref
from app.agents.growth.doc_seed import AREAS, ROUTINE_CATEGORIES, chain_key
from app.agents.growth.gating import routine_mode
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.store.ports import GrowthDocReader, GrowthDocRow, GrowthDocType
from app.agents.growth.tools.routine import ChainError, ordered_chain
from app.rules.growth_topic import OTHER
from app.rules.term_match import Term, match_fields, normalize

logger = logging.getLogger(__name__)

# 프롬프트 [예시]에 싣는 행 수 (RAG_plan §7 — Growth 3)
DOC_LIMIT = 3
# 포트에서 먼저 받는 수. 같은 월령 아이가 늘 같은 앞 3행을 받지 않게 이 안에서 돌려 고른다
DOC_POOL = 30
# 자립 단계 사슬을 모을 때 받는 행 수. 카테고리 하나의 사슬 행이 다 들어오는 크기
CHAIN_POOL = 60
# 사슬 이름과 관찰 subject 를 서로 포함 관계로 볼 때 짧은 쪽의 최소 길이 ("손" ≠ "손 씻기")
MIN_CHAIN_MATCH = 2
# 자립 단계 행 제목의 "이름 — 단계 설명" 구분자
_TITLE_SEP = " — "
# 사슬의 다른 이름. 보호자 말을 Memory 가 그대로 행동 이름으로 옮겨서 "칫솔질하기" 로 저장되면 양치
# 사슬을 못 찾고 이미 말한 행동을 또 묻는다. 정규화한 값으로 적는다
_CHAIN_ALIASES: Mapping[str, tuple[str, ...]] = {"양치": ("칫솔질", "이닦기")}

# 상황 태그는 영문 식별자다 (bored · anxious · tired …). 발화 한 줄이 필터 · 쿼리로 안 흘러가게 한다
_TRIGGER_TAG = re.compile(r"[a-z][a-z0-9_]*")

# 사슬 대신 행 하나가 연습 · 대응 방법 하나인 카테고리.
# 예절은 manner_practice, 습관은 habit_strategy 행이다
STRATEGY_CATEGORIES = frozenset({"social_manner", "habit"})


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
    areas: Sequence[str] = (),
) -> str:
    """의미 검색 쿼리. 코드가 아는 값만 이어 붙인다 — 보호자 원문은 들어올 길이 없다."""
    parts = [task_type.value, stage_of(months), *interest_keys]
    if routine_category:
        parts.append(routine_category)
    parts.extend(areas)
    return " ".join(parts)


async def search_growth_doc(
    docs: GrowthDocReader,
    *,
    task_type: GrowthTaskType,
    months: int,
    interest_keys: Sequence[str] = (),
    routine_category: str | None = None,
    trigger_tags: Sequence[str] = (),
    areas: Sequence[str] = (),
    rotation: str = "",
    blocked: Sequence[Term] = (),
) -> tuple[DocHit, ...]:
    """월령 슬라이스 → 의미 검색 상위 `DOC_POOL` 행 중 `rotation` 으로 돌린 `DOC_LIMIT` 행.

    없으면 빈 튜플. `rotation` 은 run() 이 넘기는 "아이 · 날짜" 다 — 같은 날 같은 아이는 같은
    [예시]를 받고(재호출도 같은 프롬프트), 날이 바뀌면 바뀐다. 비면 포트 순서 그대로다.

    `interest_keys` 는 `profile_affinity` 의 `merge_key` 다. `trigger_tags` 는 습관 교정이 읽는 상황
    태그라서 영문 식별자만 받는다. 모르는 `routine_category` 와 식별자가 아닌 태그는 `ValueError` —
    포트까지 가지 않는다.

    `areas` 는 요청 · 기록 조각에서 코드가 읽은 주제 영역 키다(`GrowthRunState.topic`). 있으면 그
    영역의 행에서만 고른다 — 국어를 물었는데 자연탐구 행이 [예시]로 가지 않게. `OTHER`(영어 같은
    영역 밖 주제)는 행이 없어 비어 돌아간다. 모르는 영역 키는 `ValueError`.

    `blocked` 는 식품 아닌 알레르기의 대응표 낱말이다. 제목 · 본문 · 준비물 중 한 칸이라도 걸린
    행은 돌려 고르기 전에 뺀다 — 막힐 예시를 보이면 모델이 베껴 재호출만 는다.
    """
    if routine_category is not None and routine_category not in ROUTINE_CATEGORIES:
        raise ValueError(f"routine_category 가 정해진 값이 아니다: {routine_category!r}")
    if not all(_TRIGGER_TAG.fullmatch(tag) for tag in trigger_tags):
        raise ValueError("trigger_tags 는 영문 소문자 식별자만 받는다")
    if set(areas) - AREAS - {OTHER}:
        raise ValueError("areas 가 정해진 영역 키가 아니다")

    row_type = doc_row_type(task_type, months, routine_category)
    rows = await docs.search(
        months=months,
        row_type=row_type,
        routine_category=routine_category,
        trigger_tags=tuple(trigger_tags),
        areas=tuple(areas),
        query=build_doc_query(task_type, months, interest_keys, routine_category, areas),
        limit=DOC_POOL,
    )
    served = [r for r in rows if r.row_type == row_type and r.min_month <= months < r.max_month]
    if areas:
        served = [r for r in served if r.area in areas]
    if blocked:
        served = [r for r in served if not match_fields((r.title, r.body, *r.materials), blocked)]
    if rotation:
        served.sort(key=lambda row: _turn(rotation, row.doc_key))
    return tuple(DocHit(row) for row in served[:DOC_LIMIT])


async def load_routine_chain(docs: GrowthDocReader, row: GrowthDocRow) -> tuple[GrowthDocRow, ...]:
    """`row` 가 속한 자립 단계 사슬을 처음부터 끝까지 세운다. `pick_next_step` 에 그대로 넘긴다.

    사슬은 doc_key 의 사슬 이름으로 읽는다(`doc_seed.chain_key`).
    자립 단계 행이 아니면 `ValueError`,
    사슬이 끊겼거나 갈라졌으면 `ChainError` — 적재 검사가 먼저 막는다.
    """
    if row.row_type != "routine_step":
        raise ValueError(f"자립 단계 행이 아니다: {row.doc_key}")
    return ordered_chain(await docs.chain(chain_key=chain_key(row.doc_key)))


def _turn(rotation: str, doc_key: str) -> str:
    return hashlib.sha256(f"{rotation}|{doc_key}".encode()).hexdigest()


async def routine_chains(
    docs: GrowthDocReader, *, months: int, category: str
) -> dict[str, tuple[GrowthDocRow, ...]]:
    """이 월령 · 카테고리의 자립 단계 사슬 전부. 사슬 키(`doc_seed.chain_key`) → 처음부터 끝까지.

    검색에 걸린 행의 사슬을 통째로 다시 읽는다 — 월령으로 걸러 중간이 빠지면 다음 칸을 못 고른다.
    끊긴 사슬(`ChainError`)은 빼고 경고만 남긴다 — 그 행동은 `doc.no_row` 로 닫힌다.
    """
    if category not in ROUTINE_CATEGORIES:
        raise ValueError(f"routine_category 가 정해진 값이 아니다: {category!r}")
    rows = await docs.search(
        months=months,
        row_type="routine_step",
        routine_category=category,
        trigger_tags=(),
        query=build_doc_query(GrowthTaskType.ROUTINE_COACHING, months, (), category),
        limit=CHAIN_POOL,
    )
    keys = dict.fromkeys(
        chain_key(row.doc_key)
        for row in rows
        if row.row_type == "routine_step" and row.routine_category == category
    )
    chains: dict[str, tuple[GrowthDocRow, ...]] = {}
    for key in keys:
        try:
            chains[key] = ordered_chain(await docs.chain(chain_key=key))
        except ChainError:
            # 검수에서 중간 단계가 draft 로 돌아가면 approved 사슬이 끊긴다. 추측해서 잇지 않는다.
            # 로그에는 사슬 이름만 남긴다
            logger.warning("Growth 자립 단계 사슬이 끊겨 뺀다 chain=%s", key)
    return chains


def row_name(row: GrowthDocRow) -> str:
    """행 제목의 이름 — " — " 앞. 예: "인사하기 — 인형 둘로 …" → "인사하기"."""
    return row.title.split(_TITLE_SEP, 1)[0].strip()


def chain_name(chain: Sequence[GrowthDocRow]) -> str:
    """사슬의 행동 이름 — 첫 행 제목의 " — " 앞. 예: "손 씻기 — 어른이 …" → "손 씻기"."""
    return row_name(chain[0])


async def strategy_rows(
    docs: GrowthDocReader,
    *,
    months: int,
    category: str,
    subject: str = "",
    trigger_tags: Sequence[str] = (),
    rotation: str = "",
) -> tuple[GrowthDocRow, ...]:
    """예절 · 습관의 전략 행 — 최대 `DOC_LIMIT` 개. 없으면 빈 튜플이고 호출부는 `doc.no_row` 다.

    - 예절(`manner_practice`) — 행 이름(제목의 " — " 앞)이 관찰 subject 와 맞는 행. 맞춤 규칙은
      사슬 이름과 같다(`match_chain`)
    - 습관(`habit_strategy`) — 상황 태그가 겹치는 행. 태그가 없으면 포트에 가지 않는다 — 언제
      나오는 행동인지 모르는 교정안은 일반론이다
    월령 · 종류 두 번째 관문은 `search_growth_doc` 과 같다. `rotation` 은 "아이 · 날짜" 다.
    """
    if category not in STRATEGY_CATEGORIES:
        raise ValueError(f"전략 행이 있는 카테고리가 아니다: {category!r}")
    if not all(_TRIGGER_TAG.fullmatch(tag) for tag in trigger_tags):
        raise ValueError("trigger_tags 는 영문 소문자 식별자만 받는다")
    if category == "habit" and not trigger_tags:
        return ()
    row_type = doc_row_type(GrowthTaskType.ROUTINE_COACHING, months, category)
    rows = await docs.search(
        months=months,
        row_type=row_type,
        routine_category=category,
        trigger_tags=tuple(trigger_tags),
        query=build_doc_query(GrowthTaskType.ROUTINE_COACHING, months, (), category),
        limit=DOC_POOL,
    )
    wanted, tags = normalize(subject), set(trigger_tags)
    served = [
        row
        for row in rows
        if row.row_type == row_type
        and row.routine_category == category
        and row.min_month <= months < row.max_month
        and (
            bool(tags & set(row.trigger_tags))
            if category == "habit"
            else related_names(normalize(row_name(row)), wanted)
        )
    ]
    if rotation:
        served.sort(key=lambda row: _turn(rotation, row.doc_key))
    return tuple(served[:DOC_LIMIT])


def match_chain(
    chains: Mapping[str, tuple[GrowthDocRow, ...]], subject: str
) -> tuple[GrowthDocRow, ...] | None:
    """관찰의 행동 이름에 맞는 사슬. 띄어쓰기를 무시하고 서로 포함 관계로 본다.

    사슬의 다른 이름(`_CHAIN_ALIASES`)도 같이 본다. 짧은 쪽이 `MIN_CHAIN_MATCH` 보다 짧으면
    같을 때만 맞는다. 둘 이상 맞으면 이름이 긴 쪽이다. 없으면 None — 호출부는 `doc.no_row` 로
    닫는다(단계를 지어내지 않는다).
    """
    wanted = normalize(subject)
    found = [
        chain
        for chain in chains.values()
        if any(related_names(name, wanted) for name in _chain_names(chain))
    ]
    if not found:
        return None
    return max(found, key=lambda chain: len(chain_name(chain)))


def _chain_names(chain: Sequence[GrowthDocRow]) -> tuple[str, ...]:
    name = normalize(chain_name(chain))
    return (name, *_CHAIN_ALIASES.get(name, ()))


def related_names(a: str, b: str) -> bool:
    """정규화한 두 행동 이름이 같은 행동인가 — 같거나, 짧은 쪽이 두 글자 이상이고 긴 쪽에 든다."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= MIN_CHAIN_MATCH and short in long
