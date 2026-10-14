"""급식 갱신 `run()` — 모델이 조회 · 갱신 tool 을 부르고, 화면 문구는 tool 이 정한다.

아이 · 급식 · 메뉴는 전부 가짜다. 이 테스트는 `run()` 의 결과(`DomainAgentResult`)와 모델 호출
수를 본다. tool 자체의 되묻기 · 거절 규칙은 `test_food_daycare.py` 가 본다.
"""

from collections.abc import Sequence

from app.agents.common.schemas.task import DomainTask
from app.agents.food.agent import run
from app.agents.food.context import FoodContext
from app.agents.food.readouts import READOUTS
from app.agents.food.store import (
    InMemoryDaycareMeals,
    InMemoryProfile,
    InMemorySafety,
    SafetyEntry,
    in_memory_ports,
)
from tests.unit.agents.food.support import (
    BIRTH_DATE,
    CHILD,
    KST,
    NOW,
    TODAY,
    ScriptedLLM,
    daycare,
    reply,
    tool_call,
    tool_response,
)

UTTERANCE = "오늘 점심에 된장국 대신 미역국 먹었대"


def daycare_context(
    *, safety: Sequence[SafetyEntry] = (), safety_fails: bool = False, rows: bool = True
) -> FoodContext:
    row = daycare("된장국", "잡곡밥", days_ago=0)
    ports = in_memory_ports(
        profile=InMemoryProfile({CHILD: BIRTH_DATE}),
        safety=InMemorySafety(safety, fail=safety_fails),
        daycare=InMemoryDaycareMeals([row] if rows else []),
    )
    return FoodContext(child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=ports)


def daycare_task(text: str = UTTERANCE) -> DomainTask:
    return DomainTask(run_id="run-1", agent="food", task_type="daycare_meal", request_texts=(text,))


def update_call() -> object:
    return tool_response(
        tool_call(
            "u",
            "update_daycare_meal",
            {
                "date_span": "오늘",
                "slot": "lunch",
                "removed_spans": ["된장국"],
                "added_spans": ["미역국"],
            },
        )
    )


async def test_급식_갱신은_모델이_갱신_tool_을_부르면_끝나고_행이_바뀐다() -> None:
    context = daycare_context()
    llm = ScriptedLLM(update_call())

    result = await run(daycare_task(), context, client=llm)

    assert (result.agent, result.task_type, result.status) == ("food", "daycare_meal", "completed")
    assert result.model_calls == 1
    assert result.suggestions == () and result.needs_observation == ()
    (readout,) = result.readouts
    assert "미역국" in readout.body and readout.authored_by == "code"
    (row,) = await context.ports.daycare.rows(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    assert set(row.menu_keys) == {"미역국", "잡곡밥"}  # 된장국만 바뀌고 밥은 남는다


async def test_급식_갱신은_tool_을_강제하지_않는다() -> None:
    # 출력 tool 이 쓰기(갱신 · 삭제)뿐이라 required 면 조회만 하려던 발화도 쓰기로 몰린다.
    # 삭제는 되돌릴 수 없다(OCR 재적재) — 모델이 말로 끝내면 failed 로 끝나는 쪽이 낫다
    llm = ScriptedLLM(update_call())

    await run(daycare_task(), daycare_context(), client=llm)

    assert llm.tool_choices == ["auto"]


async def test_조회한_뒤_갱신해도_왕복은_한_진입이다() -> None:
    llm = ScriptedLLM(
        tool_response(tool_call("l", "lookup_daycare_menu", {"day": "today"})),
        update_call(),
    )

    result = await run(daycare_task(), daycare_context(), client=llm)

    assert result.status == "completed"
    assert result.model_calls == 1  # model_calls 는 Agent 진입 수다 — 루프 왕복이 아니다
    assert llm.calls == 2


async def test_양만_바꾼_갱신도_빈_결과가_아니라_반영했다는_문구가_나간다() -> None:
    context = daycare_context()
    llm = ScriptedLLM(
        tool_response(
            tool_call("u", "update_daycare_meal", {"date_span": "오늘", "amount_span": "엄청 많이"})
        )
    )

    result = await run(daycare_task("오늘 급식 엄청 많이 먹었대"), context, client=llm)

    assert result.status == "completed"
    assert [readout.body for readout in result.readouts] == [
        READOUTS.render("daycare.amount_updated", date="10월 14일", slot="점심").body
    ]
    (row,) = await context.ports.daycare.rows(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    assert (row.amount_factor, row.amount_known) == (1.5, True)


async def test_날짜를_못_정하면_쓰지_않고_되묻는_문구가_readouts_로_나간다() -> None:
    context = daycare_context()
    llm = ScriptedLLM(
        tool_response(
            tool_call(
                "u",
                "update_daycare_meal",
                {"date_span": "그날", "removed_spans": ["된장국"], "added_spans": ["미역국"]},
            )
        )
    )

    result = await run(daycare_task("그날 된장국 대신 미역국 먹었대"), context, client=llm)

    assert result.status == "completed"
    assert [readout.body for readout in result.readouts] == [
        READOUTS.get("daycare.ambiguous").template
    ]
    (row,) = await context.ports.daycare.rows(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    assert "된장국" in row.menu_keys  # 추측해서 고치지 않는다


async def test_모델이_출력_tool_없이_끝나면_failed_이고_행은_그대로다() -> None:
    context = daycare_context()
    llm = ScriptedLLM(reply("바꿔 뒀어요"))

    result = await run(daycare_task(), context, client=llm)

    assert result.status == "failed" and result.readouts == ()
    (row,) = await context.ports.daycare.rows(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    assert "된장국" in row.menu_keys


async def test_급식_행이_없으면_모델을_부르지_않고_닫는다() -> None:
    llm = ScriptedLLM()

    result = await run(daycare_task(), daycare_context(rows=False), client=llm)

    assert (result.status, result.model_calls) == ("blocked", 0)
    assert result.readouts[0].body == READOUTS.get("closed.no_daycare").template


async def test_급식_갱신은_안전_조회가_실패해도_닫히지_않는다() -> None:
    # 식품 안내가 아니라 보호자가 말한 사실을 고치는 일이다 — 알레르기 겹침을 확인하지 못할 뿐이다
    context = daycare_context(safety_fails=True)
    llm = ScriptedLLM(update_call())

    result = await run(daycare_task(), context, client=llm)

    assert result.status == "completed"
    assert [readout.body for readout in result.readouts] == [
        READOUTS.render("daycare.updated", date="10월 14일", slot="점심", menu="미역국").body
    ]


async def test_알레르기와_겹치는_메뉴도_저장하고_경고_문구를_함께_낸다() -> None:
    egg = [SafetyEntry(kind="allergy", label="달걀", status="active")]
    context = daycare_context(safety=egg)
    llm = ScriptedLLM(
        tool_response(
            tool_call(
                "u",
                "update_daycare_meal",
                {
                    "date_span": "오늘",
                    "slot": "lunch",
                    "removed_spans": ["된장국"],
                    "added_spans": ["계란국"],
                },
            )
        )
    )

    result = await run(daycare_task("오늘 점심에 된장국 대신 계란국 먹었대"), context, client=llm)

    assert result.status == "completed"
    assert any("알레르기와 겹쳐요" in readout.body for readout in result.readouts)  # 화면 문구
    (row,) = await context.ports.daycare.rows(child_id=CHILD, date_from=TODAY, date_to=TODAY)
    assert "계란국" in row.menu_keys  # 있었던 일이라 기록은 막지 않는다
