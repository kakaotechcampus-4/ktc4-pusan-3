"""Food 조회 · 후보 · 급식 tool 테스트가 같이 쓰는 값. 이름 · 메뉴 · 기록은 전부 가짜다."""

import copy
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.agents.common.evidence import AffinityRow, AffinityState
from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import DomainTask
from app.agents.food.context import FoodContext
from app.agents.food.store import (
    DaycareMealRow,
    FoodDocRow,
    FoodObservation,
    InMemoryDaycareMeals,
    InMemoryFoodDocs,
    InMemoryFoodMemory,
    InMemoryMenuCatalog,
    InMemoryProfile,
    InMemoryRecipeCatalog,
    InMemorySuggestionHistory,
    MenuCatalogRow,
    SafetyEntry,
    in_memory_ports,
)
from app.agents.food.store.ports import ConfidenceSource, DaycareSlot

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=KST)
TODAY = NOW.date()
BIRTH_DATE = date(2024, 6, 14)  # NOW 기준 28개월 — toddler


def day(days_ago: int) -> date:
    return TODAY - timedelta(days=days_ago)


def context(
    *,
    memory: InMemoryFoodMemory | None = None,
    daycare: InMemoryDaycareMeals | None = None,
    catalog: InMemoryMenuCatalog | None = None,
    recipes: InMemoryRecipeCatalog | None = None,
    docs: InMemoryFoodDocs | None = None,
    suggestions: InMemorySuggestionHistory | None = None,
    safety: Sequence[SafetyEntry] | None = (),
    birth: date = BIRTH_DATE,
    run_id: str = "run-1",
    utterance: str = "",
) -> FoodContext:
    """`build_gate` 가 한 뒤의 context. `safety=None` 이면 조회 실패(`알레르기 확인 못 함`)다."""
    overrides: dict[str, object] = {"profile": InMemoryProfile({CHILD: birth})}
    for name, value in (
        ("memory", memory),
        ("daycare", daycare),
        ("catalog", catalog),
        ("recipes", recipes),
        ("docs", docs),
        ("suggestions", suggestions),
    ):
        if value is not None:
            overrides[name] = value
    made = FoodContext(
        child_id=CHILD,
        run_id=run_id,
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(**overrides),
    )
    made.state.safety = None if safety is None else tuple(safety)
    made.state.utterance = utterance
    return made


def observation(
    subject: str,
    *,
    days_ago: int = 0,
    polarity: int = 0,
    amount_text: str | None = None,
    confidence_source: ConfidenceSource | None = None,
    source_notice_id: UUID | None = None,
) -> FoodObservation:
    return FoodObservation(
        id=uuid4(),
        child_id=CHILD,
        observed_on=day(days_ago),
        subject=subject,
        amount_text=amount_text,
        polarity=polarity,
        updated_at=NOW,
        confidence_source=confidence_source,
        source_notice_id=source_notice_id,
    )


def affinity(
    label: str,
    *,
    polarity: int | None = 1,
    state: AffinityState = "confirmed",
    strength: float = 0.8,
    days_ago: int = 1,
) -> AffinityRow:
    return AffinityRow(
        id=uuid4(),
        merge_key=label,
        state=state,
        polarity=polarity,
        strength=strength,
        last_observed_on=day(days_ago),
    )


def daycare(
    *menu_keys: str,
    days_ago: int = 1,
    slot: DaycareSlot = "lunch",
    amount_factor: float = 1.0,
    amount_known: bool = False,
) -> DaycareMealRow:
    return DaycareMealRow(
        id=uuid4(),
        child_id=CHILD,
        serve_date=day(days_ago),
        meal_slot=slot,
        menu_keys=tuple(menu_keys),
        amount_factor=amount_factor,
        amount_known=amount_known,
    )


def menu_row(
    menu_key: str,
    *,
    source: str = "manual",
    ingredients: tuple[str, ...] = ("가짜재료",),
    **overrides: object,
) -> MenuCatalogRow:
    """`menu_catalog` / `recipe_catalog` 행 하나. 레시피 행은 `source="mafra_recipe"` 로 넘긴다."""
    fields: dict[str, object] = {
        "menu_key": menu_key,
        "display_name": menu_key,
        "source": source,
        "resolved": True,
        "synced_at": NOW,
        "ingredients": ingredients,
    }
    if source == "mafra_recipe":
        fields["source_id"] = menu_key
    fields.update(overrides)
    return MenuCatalogRow(**fields)  # type: ignore[arg-type]


def doc_row(
    title: str,
    *,
    row_type: str = "weaning_stage",
    months: tuple[int, int] = (4, 12),
    body: str = "가짜 문장이다.",
    codes: frozenset[int] = frozenset(),
    org: str = "가짜 기관",
) -> FoodDocRow:
    return FoodDocRow(
        id=uuid4(),
        row_type=row_type,
        title=title,
        body=body,
        min_month=months[0],
        max_month=months[1],
        texture=None,
        food_groups=frozenset(),
        allergen_codes=codes,
        source_org=org,
        written_at=NOW,
    )


# 일반 추천은 문서 행이 근거다 (루트 §2). 식단 추천 테스트의 기본 후보는 이 행을 인용한다.
# 월령 범위를 넓게 잡아 어느 월령의 아이에게도 [예시]로 보인다
MEAL_DOC = doc_row(
    "기본 식단 자료", row_type="meal_pattern", months=(0, 120), body="밥 국 반찬을 고루 둔다."
)


# ── 가짜 LLM — run() 을 끝까지 돌리는 테스트가 같이 쓴다 ─────────────────────
USAGE = {"prompt_tokens": 10, "completion_tokens": 5}


class ScriptedLLM:
    """정해 둔 응답을 순서대로 돌려준다. 받은 `messages` 를 부를 때마다 복사해 둔다.

    `seen` 은 호출마다의 messages 스냅샷이다 — 모델 입력 검사가 프롬프트와 tool 결과를 다 훑는다.
    응답을 다 쓴 뒤에 또 부르면 실패한다 — "모델 0회" 가 맞는 경로가 모델을 부른 것이다.
    """

    def __init__(self, *responses: LLMResponse) -> None:
        self._queue = list(responses)
        self.seen: list[list[dict[str, Any]]] = []
        self.tool_choices: list[Any] = []  # 호출마다의 tool_choice

    @property
    def calls(self) -> int:
        return len(self.seen)

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        self.seen.append(copy.deepcopy(messages))
        self.tool_choices.append(kwargs.get("tool_choice"))
        if not self._queue:
            raise AssertionError("응답을 다 썼는데 모델을 또 불렀다")
        return self._queue.pop(0)


def tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    raw = json.dumps(arguments, ensure_ascii=False)
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=raw))


def tool_response(*calls: SimpleNamespace) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=None, tool_calls=list(calls)), usage=USAGE, latency_ms=1
    )


def reply(content: str) -> LLMResponse:
    return LLMResponse(
        message=SimpleNamespace(content=content, tool_calls=None), usage=USAGE, latency_ms=1
    )


def food_task(task_type: str, text: str = "저녁") -> DomainTask:
    """Food 에 넘기는 task. `task_type` 은 `FoodTaskType` 의 값이다."""
    return DomainTask(run_id="r", agent="food", task_type=task_type, request_texts=(text,))


def propose(
    *menu_keys: str,
    reason: str = "어제 급식과 겹치지 않아서 골랐어요",
    evidence: list[dict[str, str]] | None = None,
) -> LLMResponse:
    """`propose_meal_candidates` 를 부르는 응답 하나. `evidence` 를 안 주면 MEAL_DOC 을 인용한다."""
    if evidence is None:
        evidence = [{"kind": "food_doc", "id": str(MEAL_DOC.id), "note": "식단 자료를 참고함"}]
    return tool_response(
        tool_call(
            f"propose-{'-'.join(menu_keys)}",
            "propose_meal_candidates",
            {
                "candidates": [
                    {
                        "menu_key": key,
                        "meal_slot": "dinner",
                        "reason": reason,
                        "evidence": evidence,
                    }
                    for key in menu_keys
                ]
            },
        )
    )
