"""식단 추천 `run()` 을 가짜 LLM 으로 끝까지 돈다 — 풀 · 샘플 · 프롬프트 · 루프 · 재호출 · 결과.

레시피 · 아이 · 기록은 전부 가짜다. 후보 풀이 샘플 크기보다 작으면 샘플이 풀 전체라서, 모델이
고를 수 있는 `menu_key` 를 테스트가 미리 안다. 사후 안전 필터가 빼는 상황은 풀 단계에서 이미
걸러져 실제로는 거의 안 생기므로, 풀을 바꿔 끼워(`build_pool` 대체) 풀 필터와 사후 필터 사이의
틈을 흉내 낸다.
"""

from collections.abc import Sequence
from datetime import date
from uuid import uuid4

import pytest

from app.agents.common.evidence import AffinityRow
from app.agents.common.schemas.task import DomainTask
from app.agents.food import agent as food_agent
from app.agents.food.agent import run
from app.agents.food.context import FoodContext
from app.agents.food.meal_pool import PoolItem
from app.agents.food.readouts import FOLLOWUP_PREFERENCE, POOL_EMPTY, READOUTS
from app.agents.food.store import (
    InMemoryFoodDocs,
    InMemoryFoodMemory,
    InMemoryProfile,
    InMemoryRecipeCatalog,
    InMemorySafety,
    MenuCatalogRow,
    SafetyEntry,
    in_memory_ports,
)
from tests.unit.agents.food.support import (
    BIRTH_DATE,
    CHILD,
    KST,
    NOW,
    ScriptedLLM,
    affinity,
    doc_row,
    menu_row,
    propose,
    reply,
    tool_call,
    tool_response,
)

EGG = [SafetyEntry(kind="allergy", label="달걀", status="active")]


def recipe(name: str, *ingredients: str, **overrides: object) -> MenuCatalogRow:
    return menu_row(
        name, source="mafra_recipe", ingredients=ingredients or ("가짜재료",), **overrides
    )


TOFU = recipe("두부조림", "두부", "간장")
SPINACH = recipe("시금치나물", "시금치", "참기름")
SOUP = recipe("미역국", "미역", "참기름")
BEEF = recipe("소불고기", "쇠고기", "양파")
EGGROLL = recipe("계란말이", "계란", "당근", allergen_codes=frozenset({1}))
EGGSOUP = recipe("계란국", "계란", "파", allergen_codes=frozenset({1}))
EGGSTEW = recipe("계란찜", "계란", "새우젓", allergen_codes=frozenset({1}))


def meal_context(
    *rows: MenuCatalogRow,
    safety: Sequence[SafetyEntry] = (),
    memory: InMemoryFoodMemory | None = None,
    docs: InMemoryFoodDocs | None = None,
    birth: date = BIRTH_DATE,
) -> FoodContext:
    overrides: dict[str, object] = {
        "profile": InMemoryProfile({CHILD: birth}),
        "safety": InMemorySafety(safety),
        "recipes": InMemoryRecipeCatalog(rows),
    }
    if memory is not None:
        overrides["memory"] = memory
    if docs is not None:
        overrides["docs"] = docs
    return FoodContext(
        child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=in_memory_ports(**overrides)
    )


def meal_task(text: str = "저녁 뭐 먹일까") -> DomainTask:
    return DomainTask(
        run_id="run-1", agent="food", task_type="meal_recommendation", request_texts=(text,)
    )


def with_pool(monkeypatch: pytest.MonkeyPatch, *rows: MenuCatalogRow) -> None:
    """풀을 바꿔 끼운다. 알레르기 행도 풀에 넣어 사후 안전 필터까지 가게 한다."""

    async def fake(context: FoodContext) -> tuple[PoolItem, ...]:
        return tuple(PoolItem(row=row, weight=1.0) for row in rows)

    monkeypatch.setattr(food_agent, "build_pool", fake)


# ── T6 — 식단 추천 끝까지 ────────────────────────────────────────────────
async def test_식단_추천은_후보를_보여_주고_고른_3개를_추천으로_낸다() -> None:
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP), client=llm)

    assert (result.agent, result.task_type, result.status) == (
        "food",
        "meal_recommendation",
        "completed",
    )
    assert [draft.content for draft in result.suggestions] == ["두부조림", "시금치나물", "미역국"]
    assert result.model_calls == 1
    assert result.readouts == ()  # 3개를 다 채우면 개수 안내가 없다
    assert result.needs_observation == ()
    first_messages = llm.seen[0]
    shown = "\n".join(str(message["content"]) for message in first_messages)
    assert all(name in shown for name in ("두부조림", "시금치나물", "미역국"))


async def test_식단_추천은_tool_을_강제한다() -> None:
    # 출력 tool 이 하나(propose_meal_candidates)뿐이고 쓰지 않는다 — 말로 끝나는 것을 줄인다(#320)
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    await run(meal_task(), meal_context(TOFU, SPINACH, SOUP), client=llm)

    assert llm.tool_choices == ["required"]


async def test_근거가_없는_추천은_일반_추천이고_이유가_코드_문구다() -> None:
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP), client=llm)

    assert {draft.kind for draft in result.suggestions} == {"general"}
    assert all(draft.citations == () for draft in result.suggestions)  # 아이 기록 근거가 0행이다


async def test_기억을_조회해_근거를_달면_개인화_추천이고_근거가_따라간다() -> None:
    liked = affinity("두부", polarity=1)
    memory = InMemoryFoodMemory({CHILD: [liked]}, {})
    llm = ScriptedLLM(
        tool_response(tool_call("m", "search_food_memory", {"keywords": []})),
        propose(
            "두부조림",
            "시금치나물",
            "미역국",
            evidence=[{"kind": "profile_affinity", "id": str(liked.id), "note": "두부를 잘 먹음"}],
        ),
    )

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP, memory=memory), client=llm)

    assert {draft.kind for draft in result.suggestions} == {"personalized"}
    assert all(draft.citations for draft in result.suggestions)  # 근거 0행인 개인화는 버그다


async def test_후보_풀이_비면_모델을_부르지_않고_추천_없이_안내만_낸다() -> None:
    llm = ScriptedLLM()  # 한 번이라도 부르면 실패한다

    result = await run(meal_task(), meal_context(), client=llm)

    assert result.status == "completed"
    assert result.suggestions == ()
    assert result.model_calls == 0
    (notice,) = result.readouts
    assert notice.body == READOUTS.get(POOL_EMPTY).template
    assert notice.code == "empty"


async def test_모든_후보가_알레르기라서_풀이_비어도_추천_없이_안내로_끝난다() -> None:
    llm = ScriptedLLM()

    result = await run(meal_task(), meal_context(EGGROLL, EGGSOUP, EGGSTEW, safety=EGG), client=llm)

    assert result.status == "completed" and result.suggestions == ()
    assert result.readouts[0].code == "empty"


async def test_풀이_3개_미만이면_있는_만큼_내고_재호출하지_않는다() -> None:
    llm = ScriptedLLM(propose("두부조림", "시금치나물"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH), client=llm)

    assert result.status == "completed"
    assert len(result.suggestions) == 2
    assert result.model_calls == 1
    assert result.readouts[0].code == "fewer"


async def test_모델이_출력_tool_없이_말로_끝내면_failed_다() -> None:
    llm = ScriptedLLM(reply("저녁은 두부조림 어때요?"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP), client=llm)

    assert result.status == "failed"
    assert result.suggestions == () and result.readouts == ()
    assert result.model_calls == 1


async def test_알레르기_메뉴는_풀에서_빠져_모델에게_보이지_않는다() -> None:
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    await run(meal_task(), meal_context(TOFU, SPINACH, SOUP, EGGROLL, safety=EGG), client=llm)

    shown = "\n".join(str(message["content"]) for message in llm.seen[0])
    assert "계란말이" not in shown


# ── T7 — 재호출 1회 ──────────────────────────────────────────────────────
async def test_사후_필터로_빠져_3개_미만이면_재호출해_빈자리를_채운다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, TOFU, EGGROLL, SPINACH, SOUP, BEEF)
    llm = ScriptedLLM(
        propose("두부조림", "계란말이"),  # 계란말이는 사후 필터가 뺀다 → 1개
        propose("시금치나물", "미역국", "소불고기"),
    )

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert result.status == "completed"
    assert result.model_calls == 2
    assert [draft.content for draft in result.suggestions] == ["두부조림", "시금치나물", "미역국"]
    assert result.readouts == ()  # 합쳐서 3개를 채웠다


async def test_재호출에는_제외_목록만_붙고_빠진_사유는_없다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, TOFU, EGGROLL, SPINACH, SOUP, BEEF)
    llm = ScriptedLLM(propose("두부조림", "계란말이"), propose("시금치나물", "미역국", "소불고기"))

    await run(meal_task(), meal_context(safety=EGG), client=llm)

    recall = str(llm.seen[1][-1]["content"])  # 재호출을 부를 때의 마지막 user 메시지
    assert "계란말이" in recall and "두부조림" in recall  # 제외 목록은 menu_key 만
    for leaked in ("알레르기", "달걀", "막혔", "안전", "사유", "때문"):
        assert leaked not in recall


async def test_재호출_후보_목록에는_이미_고른_것과_빠진_것이_없다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, TOFU, EGGROLL, SPINACH, SOUP, BEEF)
    llm = ScriptedLLM(propose("두부조림", "계란말이"), propose("시금치나물", "미역국", "소불고기"))

    await run(meal_task(), meal_context(safety=EGG), client=llm)

    recall = str(llm.seen[1][-1]["content"])
    new_list = recall.split("[후보 목록]")[1]
    assert "시금치나물" in new_list and "미역국" in new_list and "소불고기" in new_list
    assert "두부조림" not in new_list and "계란말이" not in new_list


async def test_재호출_뒤에도_모자라면_있는_만큼_내고_안내를_붙인다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, TOFU, EGGROLL, SPINACH)
    llm = ScriptedLLM(propose("두부조림", "계란말이"), propose("시금치나물"))

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert [draft.content for draft in result.suggestions] == ["두부조림", "시금치나물"]
    assert result.readouts[0].code == "fewer"
    assert result.model_calls == 2


async def test_풀이_처음부터_3개_미만이면_사후_필터로_빠져도_재호출하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, TOFU, EGGROLL)  # 풀 2개 — 더 채울 길이가 없다
    llm = ScriptedLLM(propose("두부조림", "계란말이"))

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert result.model_calls == 1
    assert [draft.content for draft in result.suggestions] == ["두부조림"]
    assert result.readouts[0].code == "fewer"


async def test_샘플이_3개_미만이면_풀에_후보가_더_있어도_재호출하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 같은 주재료 제한 같은 이유로 샘플이 2개에 그쳐도 더 채울 길이가 없는 것으로 본다(exhausted)
    with_pool(monkeypatch, TOFU, EGGROLL, SPINACH, SOUP, BEEF)
    monkeypatch.setattr(food_agent, "sample_pool", lambda pool, *, seed: tuple(pool[:2]))
    llm = ScriptedLLM(propose("두부조림", "계란말이"))  # 응답이 하나뿐이라 재호출하면 실패한다

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert result.model_calls == 1
    assert [draft.content for draft in result.suggestions] == ["두부조림"]
    assert result.readouts[0].code == "fewer"


async def test_재호출이_출력_tool_없이_끝나도_처음_것이_남았으면_그것을_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, TOFU, EGGROLL, SPINACH, SOUP, BEEF)
    llm = ScriptedLLM(propose("두부조림", "계란말이"), reply("다른 메뉴가 없어요"))

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert result.status == "completed"
    assert [draft.content for draft in result.suggestions] == ["두부조림"]
    assert result.model_calls == 2


async def test_재호출이_출력_tool_없이_끝나고_합친_결과가_0개면_failed_다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, EGGROLL, EGGSOUP, EGGSTEW, TOFU, SPINACH, SOUP)
    llm = ScriptedLLM(propose("계란말이", "계란국", "계란찜"), reply("모르겠어요"))

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert result.status == "failed"
    assert result.suggestions == ()
    assert result.model_calls == 2


async def test_재호출_결과가_또_전부_빠지면_failed_가_아니라_추천_없음_안내다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 재호출이 후보를 냈고 코드가 그것을 안전 조건으로 걸러 0개가 됐다 — 모델 실패가 아니다
    with_pool(monkeypatch, EGGROLL, EGGSOUP, EGGSTEW, TOFU)
    llm = ScriptedLLM(propose("계란말이", "계란국"), propose("계란찜"))

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert result.status == "completed"
    assert result.suggestions == ()
    assert result.readouts[0].code == "empty"


async def test_처음_출력_tool_이_안_통과하면_재호출하지_않고_failed_다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_pool(monkeypatch, TOFU, SPINACH, SOUP)
    llm = ScriptedLLM(reply("두부조림이요"))

    result = await run(meal_task(), meal_context(safety=EGG), client=llm)

    assert result.status == "failed"
    assert result.model_calls == 1


# ── T8 — [예시] 문서 행 ──────────────────────────────────────────────────
def examples_of(prompt: str) -> str:
    return prompt.split("\n\n[예시]")[1]


async def test_예시는_프롬프트의_마지막_내용_구획이고_출처_기관이_같이_보인다() -> None:
    doc = doc_row(
        "한 끼 구성",
        row_type="meal_pattern",
        months=(12, 60),
        body="밥 국 반찬을 고루 둔다.",
        org="뉴질랜드 보건부",
    )
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    await run(
        meal_task(), meal_context(TOFU, SPINACH, SOUP, docs=InMemoryFoodDocs([doc])), client=llm
    )

    system = str(llm.seen[0][0]["content"])
    example = examples_of(system)
    assert "한 끼 구성" in example and "뉴질랜드 보건부" in example and str(doc.id) in example
    # [예시] 뒤에는 현재 시각 한 줄뿐이다 — 앞에 두면 프롬프트 캐시가 깨진다
    assert example.strip().splitlines()[-1].startswith("현재 시각은")
    assert system.index("[지시]") < system.index("\n\n[예시]")


async def test_예시에는_알레르기_행이_들어가지_않는다() -> None:
    egg_doc = doc_row(
        "계란 이유식",
        row_type="meal_pattern",
        months=(12, 60),
        body="삶은 달걀노른자",
        codes=frozenset({1}),
    )
    ok_doc = doc_row(
        "채소 먼저", row_type="feeding_tip", months=(12, 60), body="채소를 먼저 보여 준다."
    )
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    await run(
        meal_task(),
        meal_context(TOFU, SPINACH, SOUP, docs=InMemoryFoodDocs([egg_doc, ok_doc]), safety=EGG),
        client=llm,
    )

    system = str(llm.seen[0][0]["content"])
    assert "계란 이유식" not in system and "달걀노른자" not in system
    assert "채소 먼저" in system


async def test_예시_문서를_인용한_추천은_문서_근거가_달리고_일반_추천이다() -> None:
    doc = doc_row(
        "한 끼 구성", row_type="meal_pattern", months=(12, 60), body="밥 국 반찬을 고루 둔다."
    )
    llm = ScriptedLLM(
        propose(
            "두부조림",
            "시금치나물",
            "미역국",
            evidence=[{"kind": "food_doc", "id": str(doc.id), "note": "한 끼 구성을 참고함"}],
        )
    )

    result = await run(
        meal_task(), meal_context(TOFU, SPINACH, SOUP, docs=InMemoryFoodDocs([doc])), client=llm
    )

    assert result.status == "completed"
    assert {draft.kind for draft in result.suggestions} == {
        "general"
    }  # 문서는 개인화 근거가 아니다
    assert all(
        [(item.ref.kind, item.ref.id) for item in draft.citations] == [("food_doc", doc.id)]
        for draft in result.suggestions
    )


async def test_예시에_없는_문서_id_를_인용하면_그_후보는_거절된다() -> None:
    llm = ScriptedLLM(
        propose(
            "두부조림",
            "시금치나물",
            "미역국",
            evidence=[{"kind": "food_doc", "id": str(uuid4()), "note": "지어낸 근거"}],
        ),
        propose("두부조림", "시금치나물", "미역국"),
    )

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP), client=llm)

    assert result.status == "completed" and len(result.suggestions) == 3
    assert llm.calls == 2  # 거절 사유를 받고 고쳐 낸 같은 진입 안의 루프다


# ── T9 — DomainAgentResult · 질문 하나 ───────────────────────────────────
async def test_결과는_공통_DomainAgentResult_다() -> None:
    from app.agents.common.result import DomainAgentResult

    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP), client=llm)

    assert isinstance(result, DomainAgentResult)
    assert not hasattr(food_agent, "FoodAgentResult")


def candidate(label: str, *, strength: float, days_ago: int = 1) -> AffinityRow:
    return affinity(label, polarity=None, state="candidate", strength=strength, days_ago=days_ago)


async def test_선호인지_기피인지_모르는_후보가_있으면_되묻는다() -> None:
    memory = InMemoryFoodMemory({CHILD: [candidate("브로콜리", strength=0.4)]}, {})
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP, memory=memory), client=llm)

    assert result.needs_observation == (READOUTS.render(FOLLOWUP_PREFERENCE, menu="브로콜리").body,)


async def test_되물을_후보가_여럿이어도_질문은_하나_신호가_센_것이다() -> None:
    memory = InMemoryFoodMemory(
        {CHILD: [candidate("브로콜리", strength=0.3), candidate("당근", strength=0.7)]}, {}
    )
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP, memory=memory), client=llm)

    assert result.needs_observation == (READOUTS.render(FOLLOWUP_PREFERENCE, menu="당근").body,)


def asked(*labels: str) -> list[object]:
    return [candidate(label, strength=0.5 + 0.1 * index) for index, label in enumerate(labels)]


async def test_알레르기_식품은_되묻지_않는다() -> None:
    # 땅콩 알레르기 아이에게 땅콩을 좋아하는지 묻는 것은 먹여 보라는 신호로 읽힌다 — 필터는 코드다
    peanut = [SafetyEntry(kind="allergy", label="땅콩", status="active")]
    memory = InMemoryFoodMemory({CHILD: asked("당근", "땅콩")}, {})  # 땅콩이 신호가 더 세다
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(
        meal_task(), meal_context(TOFU, SPINACH, SOUP, memory=memory, safety=peanut), client=llm
    )

    assert result.needs_observation == (READOUTS.render(FOLLOWUP_PREFERENCE, menu="당근").body,)


async def test_되물을_후보가_알레르기_식품뿐이면_질문이_없다() -> None:
    peanut = [SafetyEntry(kind="allergy", label="땅콩", status="active")]
    memory = InMemoryFoodMemory({CHILD: asked("땅콩")}, {})
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(
        meal_task(), meal_context(TOFU, SPINACH, SOUP, memory=memory, safety=peanut), client=llm
    )

    assert result.needs_observation == ()


async def test_되묻는_문장은_받침_상관없이_자연스럽다() -> None:
    memory = InMemoryFoodMemory({CHILD: asked("당근")}, {})
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP, memory=memory), client=llm)

    assert result.needs_observation == ("당근은(는) 좋아하는 편인가요?",)


async def test_되물을_후보가_없으면_질문이_없다() -> None:
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP), client=llm)

    assert result.needs_observation == ()


async def test_추천을_못_냈으면_되묻지_않는다() -> None:
    memory = InMemoryFoodMemory({CHILD: [candidate("브로콜리", strength=0.4)]}, {})

    result = await run(meal_task(), meal_context(memory=memory), client=ScriptedLLM())

    assert result.needs_observation == ()


async def test_질식_주의_문구는_결과에_싣지_않는다() -> None:
    llm = ScriptedLLM(propose("두부조림", "시금치나물", "미역국"))

    result = await run(meal_task(), meal_context(TOFU, SPINACH, SOUP), client=llm)

    assert all("질식" not in readout.body for readout in result.readouts)
    assert all(readout.kind != "caution" for readout in result.readouts)
