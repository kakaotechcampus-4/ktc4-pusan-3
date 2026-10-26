"""섭취 확인(`food_check`) `run()` — 등록 알레르기 · 연령 규칙과 겹치는지 코드 문구로 답한다.

모델을 부르지 않는다. 가짜 LLM 은 큐가 비어 있어서 한 번이라도 부르면 실패한다.
"허가" 로 읽히는 답(먹여도 돼요 · 괜찮아요)은 어디에도 없다 — 못 찾았다는 것은 괜찮다는 뜻이 아니다.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.llm_client import LLMResponse
from app.agents.common.schemas.task import DomainTask
from app.agents.food.agent import run
from app.agents.food.context import FoodContext
from app.agents.food.store import (
    InMemoryConsent,
    InMemoryProfile,
    InMemorySafety,
    SafetyEntry,
    in_memory_ports,
)

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=KST)
BIRTH_50 = date(2022, 8, 2)  # NOW 기준 50개월 — preschool
BIRTH_30 = date(2024, 4, 2)  # 30개월 — toddler
BIRTH_8 = date(2026, 2, 2)  # 8개월 — infant_weaning
BIRTH_2 = date(2026, 8, 2)  # 2개월 — infant_milk

NUT = [SafetyEntry(kind="allergy", label="견과류", status="active")]
WALNUT = [SafetyEntry(kind="allergy", label="호두", status="active")]
PEANUT = [SafetyEntry(kind="allergy", label="땅콩 알레르기", status="active")]
PEANUT_NOTE = "땅콩은 콩과 식물이지만 견과류와 함께 막고 있어요."
CAVEAT = "여러 재료가 든 음식이면 들어간 재료나 성분표를 한 번 더 확인해 주세요."
NONE_REGISTERED = "등록된 알레르기가 없어서 알레르기는 확인하지 못했어요."
NO_CONSENT = "건강 정보 이용에 동의하지 않아서 알레르기는 확인하지 않았어요."
BANNED = ("먹여도 돼", "먹어도 돼", "괜찮", "안 돼", "드셔도")


class NeverLLM:
    """부르면 실패한다 — 섭취 확인은 모델 0회다."""

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        raise AssertionError("섭취 확인이 모델을 불렀다")


def check_context(
    birth: date,
    safety: list[SafetyEntry],
    *,
    consent: bool = True,
    fail: bool = False,
) -> FoodContext:
    return FoodContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=KST,
        ports=in_memory_ports(
            profile=InMemoryProfile({CHILD: birth}),
            consent=InMemoryConsent({CHILD: consent}),
            safety=InMemorySafety(safety, fail=fail),
        ),
    )


def check_task(text: str) -> DomainTask:
    return DomainTask(run_id="run-1", agent="food", task_type="food_check", request_texts=(text,))


async def ask(text: str, birth: date, safety: list[SafetyEntry], **kwargs: Any):
    result = await run(check_task(text), check_context(birth, safety, **kwargs), client=NeverLLM())
    assert result.model_calls == 0
    assert result.suggestions == ()
    return result


def bodies(result) -> list[str]:
    return [readout.body for readout in result.readouts]


async def test_견과류_등록_아이의_땅콩소스는_겹친다고_답하고_모델을_부르지_않는다() -> None:
    result = await ask("땅콩소스 먹여도 될까?", BIRTH_50, NUT)

    assert (result.agent, result.task_type, result.status) == ("food", "food_check", "completed")
    assert bodies(result) == [
        "땅콩소스은(는) 등록해 두신 견과류 알레르기와(과) 겹쳐요.",
        PEANUT_NOTE,
    ]
    assert all(readout.authored_by == "code" for readout in result.readouts)


async def test_호두만_등록했으면_땅콩소스는_못_찾았다고_하고_재료_확인을_붙인다() -> None:
    result = await ask("땅콩소스 먹여도 될까?", BIRTH_50, WALNUT)

    assert bodies(result) == [
        "땅콩소스 이름으로는 등록해 두신 호두 알레르기와(과) 겹치는 걸 찾지 못했어요.",
        CAVEAT,
    ]


async def test_땅콩_등록_아이의_견과류도_겹치고_묶음_설명이_붙는다() -> None:
    result = await ask("견과류 먹여도 돼?", BIRTH_50, PEANUT)

    assert bodies(result) == [
        "견과류은(는) 등록해 두신 땅콩 알레르기와(과) 겹쳐요.",
        PEANUT_NOTE,
    ]


async def test_양쪽_다_땅콩_이름이_아니면_묶음_설명이_없다() -> None:
    result = await ask("견과류 먹여도 돼?", BIRTH_50, NUT)

    assert bodies(result) == ["견과류은(는) 등록해 두신 견과류 알레르기와(과) 겹쳐요."]


UNKNOWN_FOOD = (
    "음식 이름을 알아보지 못해서 연령 기준과 등록해 두신 땅콩 알레르기와(과) 겹치는지는 "
    "확인하지 못했어요."
)
UNKNOWN_PLAIN = "음식 이름을 알아보지 못해서 연령 기준에 걸리는지는 확인하지 못했어요."


@pytest.mark.parametrize(
    "text",
    ["마라탕 먹여도 돼?", "친구가 준 간식 먹여도 돼?"],
)
async def test_음식을_알아보지_못하면_겹치지_않는다고_하지_않고_확인하지_못했다고_한다(
    text,
) -> None:
    result = await ask(text, BIRTH_50, PEANUT)

    assert bodies(result) == [UNKNOWN_FOOD]


@pytest.mark.parametrize(
    ("text", "food"),
    [
        ("김밥 먹여도 돼?", "김밥"),
        ("볶음밥은 먹여도 돼?", "볶음밥"),
        ("붕어빵도 먹여도 돼?", "붕어빵"),
    ],
)
async def test_사전_낱말의_조각이_아니라_말한_음식으로_못_찾았다고_한다(text, food) -> None:
    result = await ask(text, BIRTH_50, PEANUT)

    assert bodies(result) == [
        f"{food} 이름으로는 등록해 두신 땅콩 알레르기와(과) 겹치는 걸 찾지 못했어요.",
        CAVEAT,
    ]


@pytest.mark.parametrize(
    "text",
    [
        "이거 어떻게 먹여도 돼?",
        "이 간식 먹여도 되나요?",
        "이 음식 먹여도 돼?",
        "그 반찬 먹여도 돼?",
    ],
)
async def test_음식을_가리키기만_하면_무슨_음식인지_묻는다(text) -> None:
    result = await ask(text, BIRTH_50, PEANUT)

    assert result.readouts == ()
    assert result.needs_observation == (
        "어떤 음식인지 알려 주시면 등록된 알레르기와 겹치는지 볼게요.",
    )


async def test_동의가_없으면_음식을_못_알아봐도_알레르기를_안_봤다고만_한다() -> None:
    result = await ask("마라탕 먹여도 돼?", BIRTH_50, [], consent=False)

    assert bodies(result) == [UNKNOWN_PLAIN, NO_CONSENT]


async def test_동의가_없어도_연령_규칙은_답하고_알레르기는_안_봤다고_한다() -> None:
    result = await ask("꿀 넣은 이유식 먹여도 돼?", BIRTH_8, [], consent=False)

    assert bodies(result) == ["꿀은(는) 12개월 전에는 먹이지 않는 음식이에요.", NO_CONSENT]


async def test_등록_0행이면_못_찾음이_아니라_등록이_없다고_한다() -> None:
    result = await ask("땅콩 먹여도 돼?", BIRTH_30, [])

    assert bodies(result) == [NONE_REGISTERED]  # 질식 주의 문구는 어디에도 없다


async def test_수유기도_닫지_않고_답한다() -> None:
    result = await ask("꿀 먹여도 돼?", BIRTH_2, [])

    assert result.status == "completed"
    assert bodies(result) == ["꿀은(는) 12개월 전에는 먹이지 않는 음식이에요.", NONE_REGISTERED]


async def test_가리키는_말만_있으면_무슨_음식인지_하나만_묻는다() -> None:
    result = await ask("이거 먹여도 돼?", BIRTH_50, NUT)

    assert result.readouts == ()
    assert result.needs_observation == (
        "어떤 음식인지 알려 주시면 등록된 알레르기와 겹치는지 볼게요.",
    )


async def test_안전_조회가_실패하면_답하지_않는다() -> None:
    result = await run(
        check_task("땅콩소스 먹여도 될까?"),
        check_context(BIRTH_50, NUT, fail=True),
        client=NeverLLM(),
    )

    assert (result.status, result.model_calls) == ("blocked", 0)
    assert bodies(result) == ["알레르기 정보를 확인할 수 없어서 지금은 답을 드릴 수 없어요."]


@pytest.mark.parametrize(
    ("text", "birth", "safety"),
    [
        ("땅콩소스 먹여도 될까?", BIRTH_50, NUT),
        ("땅콩소스 먹여도 될까?", BIRTH_50, WALNUT),
        ("마라탕 먹여도 돼?", BIRTH_50, PEANUT),
        ("꿀 먹여도 돼?", BIRTH_2, []),
        ("땅콩 먹여도 돼?", BIRTH_30, []),
    ],
)
async def test_어떤_답에도_허가로_읽히는_표현이_없다(text, birth, safety) -> None:
    result = await ask(text, birth, safety)

    joined = "\n".join(bodies(result))
    assert joined
    assert not any(word in joined for word in BANNED)


# ── /code-review 2 · 8 — 한 음식에 겹침 · 못 찾음을 같이 말하지 않고, 보호자가 쓴 말로 말한다
async def test_겹친_음식에는_못_찾음을_같이_말하지_않는다() -> None:
    shrimp = [SafetyEntry(kind="allergy", label="새우", status="active")]
    result = await ask("새우볶음밥 먹여도 돼?", BIRTH_50, shrimp)

    assert bodies(result) == ["새우볶음밥은(는) 등록해 두신 새우 알레르기와(과) 겹쳐요."]


async def test_한_어절의_못_찾음은_한_번만_말한다() -> None:
    result = await ask("치킨마요덮밥 먹여도 돼?", BIRTH_50, PEANUT)

    assert bodies(result) == [
        "치킨마요덮밥 이름으로는 등록해 두신 땅콩 알레르기와(과) 겹치는 걸 찾지 못했어요.",
        CAVEAT,
    ]


@pytest.mark.parametrize(
    ("text", "label", "expected"),
    [
        (
            "아이스크림 먹여도 돼?",
            "우유",
            "아이스크림은(는) 등록해 두신 우유 알레르기와(과) 겹쳐요.",
        ),
        ("계란말이 먹여도 돼?", "달걀", "계란말이은(는) 등록해 두신 달걀 알레르기와(과) 겹쳐요."),
    ],
)
async def test_겹침은_사전_낱말이_아니라_보호자가_쓴_음식으로_말한다(text, label, expected) -> None:
    result = await ask(text, BIRTH_50, [SafetyEntry(kind="allergy", label=label, status="active")])

    assert bodies(result) == [expected]


@pytest.mark.parametrize("birth", [BIRTH_8, BIRTH_30])
async def test_합성어의_연령_금지는_어절마다_한_번_가장_긴_기준으로(birth) -> None:
    result = await ask("꿀떡 먹여도 돼?", birth, [])

    assert bodies(result) == ["꿀떡은(는) 48개월 전에는 먹이지 않는 음식이에요.", NONE_REGISTERED]


# ── /code-review 6 — 음식을 못 알아봤으면 등록 · 동의와 상관없이 그렇다고 말한다 ───────────
async def test_등록이_없어도_못_알아본_음식은_확인하지_못했다고_한다() -> None:
    result = await ask("마라탕 먹여도 돼?", BIRTH_30, [])

    assert bodies(result) == [UNKNOWN_PLAIN, NONE_REGISTERED]


# ── /code-review 7 — 못 찾음 문구에는 음식과 관련된 등록만 싣는다 ──────────────────────
async def test_못_찾음에는_음식과_관련된_등록만_싣는다() -> None:
    mixed = [
        SafetyEntry(kind="allergy", label="우유", status="active"),
        SafetyEntry(kind="chronic_disease", label="천식", status="active"),
        SafetyEntry(kind="environmental", label="꽃가루", status="active"),
    ]
    result = await ask("카레 먹여도 돼?", BIRTH_50, mixed)

    assert bodies(result) == [
        "카레 이름으로는 등록해 두신 우유 알레르기와(과) 겹치는 걸 찾지 못했어요.",
        CAVEAT,
    ]


async def test_음식과_관련된_등록이_없으면_등록이_없다고_한다() -> None:
    asthma = [SafetyEntry(kind="chronic_disease", label="천식", status="active")]
    result = await ask("카레 먹여도 돼?", BIRTH_50, asthma)

    assert bodies(result) == [NONE_REGISTERED]
