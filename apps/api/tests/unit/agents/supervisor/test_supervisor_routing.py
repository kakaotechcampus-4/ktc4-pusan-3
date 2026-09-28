"""routing 규칙 검증. LLM 을 부르지 않는다 — Supervisor 정답 출력을 넣고 누구를 부를지 본다.

여기가 무너지면 Supervisor 가 맞게 나눠도 Food 가 엉뚱한 조각을 받거나,
Supervisor 가 틀렸을 때 기록까지 사라진다.
"""

from typing import Any

import pytest

from app.agents.food.schemas.common import FoodTaskType
from app.agents.memory.schemas.task import WorkType
from app.agents.supervisor import routing as routing_module
from app.agents.supervisor.agent import BAD_JSON, SupervisorResult
from app.agents.supervisor.routing import (
    AGENT_LIMIT,
    MAX_DOMAIN_AGENTS,
    SKIP_MEMORY_FOR_PURE_REQUEST,
    covers_only_requests,
    route,
)
from app.agents.supervisor.schemas import SupervisorOutput
from tests.eval.agents.routing_cases import CASES_BY_ID, answer_output, covered_by

RUN_ID = "run-1"


def _route_case(case_id: str) -> Any:
    case = CASES_BY_ID[case_id]
    result = SupervisorResult(output=answer_output(case))
    return route(case.text, result, run_id=RUN_ID)


def _output(*segments: dict[str, Any]) -> SupervisorOutput:
    return SupervisorOutput.model_validate({"segments": list(segments)})


# ── supervisor_plan Step 6 표의 케이스 ──────────────────────────
def test_RC01_혼합형은_기록_힌트와_식단_추천으로_나뉜다() -> None:
    routing = _route_case("RC01")

    assert routing.intent_type == "mixed"
    assert routing.memory_task is not None
    works = [hint.work for hint in routing.memory_task.hints]
    assert works == [WorkType.OBSERVE, WorkType.OBSERVE, WorkType.SCHEDULE]  # [1][2][4]
    assert routing.memory_task.open_lookup_edit is False  # 수정 묶음 닫힘
    assert [task.task_type for task in routing.food_tasks] == [FoodTaskType.MEAL_RECOMMENDATION]
    assert routing.food_tasks[0].request_texts == ("저녁에는 뭐 먹이는 게 좋을까?",)


def test_RC01_Memory_는_원문_전체를_받는다() -> None:
    # S2 — 힌트는 참고용이다. Supervisor 가 놓쳐도 Memory 가 원문에서 찾는다
    routing = _route_case("RC01")
    assert routing.memory_task is not None
    assert routing.memory_task.raw_text == CASES_BY_ID["RC01"].text


def test_REQUEST_조각은_Memory_힌트에_넣지_않는다() -> None:
    # S2 — "이건 요청" 이라는 라벨을 보면 Memory 가 그대로 따라 안 적는다
    routing = _route_case("RC01")
    assert routing.memory_task is not None
    assert all("먹이는 게 좋을까" not in hint.text for hint in routing.memory_task.hints)


def test_RC21_영양소_분석으로_간다() -> None:
    routing = _route_case("RC21")
    assert [task.task_type for task in routing.food_tasks] == [FoodTaskType.NUTRIENT_ANALYSIS]


def test_RC16_미구현_agent_는_부르지_않고_남긴다() -> None:
    routing = _route_case("RC16")
    assert [task.task_type for task in routing.food_tasks] == [FoodTaskType.MEAL_RECOMMENDATION]
    assert routing.unavailable_agents == ("activity",)
    assert routing.dropped_agents == ()


def test_RC13_수정_요청이_있으면_수정_묶음을_연다() -> None:
    routing = _route_case("RC13")
    assert routing.memory_task is not None
    assert routing.memory_task.open_lookup_edit is True


def test_RC10_알레르기_등록은_안내하고_증상_기록은_살린다() -> None:
    routing = _route_case("RC10")

    assert [guidance.code for guidance in routing.guidance] == ["safety_record"]
    assert routing.guidance[0].deeplink == "settings/health-safety"
    assert routing.memory_task is not None
    assert [hint.work for hint in routing.memory_task.hints] == [WorkType.OBSERVE]
    assert routing.food_tasks == ()


def test_RC11_진단_문의는_안내만() -> None:
    routing = _route_case("RC11")
    assert [guidance.code for guidance in routing.guidance] == ["diagnosis"]
    assert routing.intent_type == "record"


def test_RC12_범위_밖_요청도_안내한다() -> None:
    routing = _route_case("RC12")
    assert [guidance.code for guidance in routing.guidance] == ["out_of_scope"]


def test_RC19_일정_조회는_기록형이고_Food_를_부르지_않는다() -> None:
    routing = _route_case("RC19")
    assert routing.intent_type == "record"
    assert routing.food_tasks == ()
    assert routing.memory_task is not None
    assert routing.memory_task.open_lookup_edit is True


def test_RC24_식사_알림은_일정_힌트_하나() -> None:
    routing = _route_case("RC24")
    assert routing.memory_task is not None
    assert [hint.work for hint in routing.memory_task.hints] == [WorkType.SCHEDULE]
    assert routing.food_tasks == ()


def test_RC07_순수_요청형() -> None:
    routing = _route_case("RC07")
    assert routing.intent_type == "request"
    assert routing.unavailable_agents == ("activity",)
    assert routing.food_tasks == ()


def test_T19_알레르기_등록_요청은_안내만_남긴다() -> None:
    # 발화 전체가 guarded 라 기록할 조각이 없다. Memory 를 부르지 않는 편이 안전하다 —
    # 루트 §2, 알레르기는 보호자가 직접 입력해야 하고 Agent 가 저장하면 안 된다
    routing = _route_case("T19")
    assert routing.intent_type == "record"
    assert routing.memory_task is None
    assert [guidance.code for guidance in routing.guidance] == ["safety_record"]


# ── fail-open 판정 (저장에 남았는가) ────────────────────────────
def test_모델이_말을_붙여_적어도_저장한_것으로_본다() -> None:
    """라이브 RC05 — Memory 가 "놀이터에서 그네도 탔어" 를 "오늘 놀이터에서…" 로 적었다.

    원문에는 그 자리에 "오늘" 이 없어 원문 좌표로는 못 찾는다. 저장은 제대로 됐는데
    "저장에 없다" 로 실패했다. 저장된 글이 정답 구간을 담고 있으면 덮은 것으로 본다.
    """
    case = CASES_BY_ID["RC05"]
    span = case.spans[1]
    assert covered_by(case, span, ["오늘 놀이터에서 그네도 탔어"]) is True
    assert covered_by(case, span, ["오늘 도서관에서 책 읽고"]) is False


# ── FoodTask 묶기 ───────────────────────────────────────────────
def test_식단_추천과_영양소_분석이_둘_다_있으면_FoodTask_2개() -> None:
    raw = "영양 골고루 먹었는지 봐줘. 내일 간식은 뭐가 좋을까?"
    output = _output(
        {
            "text": "영양 골고루 먹었는지 봐줘",
            "kind": "request",
            "agent": "food",
            "food_task": "nutrient_analysis",
        },
        {
            "text": "내일 간식은 뭐가 좋을까?",
            "kind": "request",
            "agent": "food",
            "food_task": "meal_recommendation",
        },
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    assert [task.task_type for task in routing.food_tasks] == [
        FoodTaskType.NUTRIENT_ANALYSIS,  # 처음 나온 순서
        FoodTaskType.MEAL_RECOMMENDATION,
    ]
    assert all(task.run_id == RUN_ID for task in routing.food_tasks)


def test_같은_유형의_요청은_한_FoodTask_로_묶는다() -> None:
    raw = "내일 간식은 뭐가 좋을까? 저녁 메뉴도 추천해줘."
    output = _output(
        {
            "text": "내일 간식은 뭐가 좋을까?",
            "kind": "request",
            "agent": "food",
            "food_task": "meal_recommendation",
        },
        {
            "text": "저녁 메뉴도 추천해줘",
            "kind": "request",
            "agent": "food",
            "food_task": "meal_recommendation",
        },
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    assert len(routing.food_tasks) == 1
    assert routing.food_tasks[0].request_texts == (
        "내일 간식은 뭐가 좋을까?",
        "저녁 메뉴도 추천해줘",
    )


def test_food_유형은_enum_으로_바꿔_넘긴다() -> None:
    # Supervisor 출력은 문자열이다 (use_enum_values). Food 는 FoodTaskType 을 받는다
    routing = _route_case("RC02")
    assert isinstance(routing.food_tasks[0].task_type, FoodTaskType)


def test_힌트의_작업_종류도_enum_이다() -> None:
    routing = _route_case("RC03")
    assert routing.memory_task is not None
    assert all(isinstance(hint.work, WorkType) for hint in routing.memory_task.hints)


# ── agent 상한 (NF-01) ──────────────────────────────────────────
def test_RC17_agent_가_셋이면_뒤의_것을_뺀다() -> None:
    routing = _route_case("RC17")

    assert MAX_DOMAIN_AGENTS == 2
    assert [task.task_type for task in routing.food_tasks] == [FoodTaskType.MEAL_RECOMMENDATION]
    assert routing.unavailable_agents == ("activity",)
    assert routing.dropped_agents == ("growth",)


def test_RC17_상한에_걸려_뺀_요청은_안내한다() -> None:
    # 셋을 물었는데 둘만 답하고 이유를 안 알리면 사용자는 나머지가 어디 갔는지 모른다
    routing = _route_case("RC17")
    assert AGENT_LIMIT in [guidance.code for guidance in routing.guidance]


def test_상한에_안_걸리면_그_안내는_없다() -> None:
    assert AGENT_LIMIT not in [guidance.code for guidance in _route_case("RC16").guidance]


def test_상한을_넘은_food_는_부르지_않는다() -> None:
    raw = "주말 놀이랑 한글 공부 방법 추천해줘. 저녁 메뉴도 추천해줘."
    output = _output(
        {"text": "주말 놀이 추천해줘", "kind": "request", "agent": "activity"},
        {"text": "한글 공부 방법 추천해줘", "kind": "request", "agent": "growth"},
        {
            "text": "저녁 메뉴도 추천해줘",
            "kind": "request",
            "agent": "food",
            "food_task": "meal_recommendation",
        },
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    assert routing.food_tasks == ()
    assert routing.dropped_agents == ("food",)


# ── 강등 (S5) ───────────────────────────────────────────────────
def test_Supervisor_가_실패하면_Memory_단독으로_간다() -> None:
    raw = "오늘 사과 먹었어. 저녁엔 뭘 먹일까?"
    routing = route(raw, SupervisorResult(output=None, error=BAD_JSON), run_id=RUN_ID)

    assert routing.degraded is True
    assert routing.memory_task is not None
    assert routing.memory_task.raw_text == raw
    assert routing.memory_task.hints == ()  # 힌트 없음
    assert routing.memory_task.open_lookup_edit is False  # 수정 묶음 닫힘
    assert routing.food_tasks == ()  # Food 호출 없음
    assert routing.guidance == ()


def test_정상_경로는_강등이_아니다() -> None:
    assert _route_case("RC01").degraded is False


# ── Memory 생략 판정 ────────────────────────────────────────────
def test_순수_요청형이면_Memory_를_건너뛴다() -> None:
    # Supervisor 가 의도를 제대로 나눴다고 보는 정책이다. 기록 조각이 없으면 Memory 를 안 부른다
    assert SKIP_MEMORY_FOR_PURE_REQUEST is True
    assert _route_case("RC07").memory_task is None  # 놀이 추천 요청 단독
    assert _route_case("RC20").memory_task is None  # 영양소 분석 요청 단독


@pytest.mark.parametrize(
    ("case_id", "expected"),
    [
        ("RC07", True),  # 순수 요청
        ("RC20", True),  # 영양소 분석 단독
        ("T19", True),  # 알레르기 등록 요청만
        ("RC08", False),  # 기록 + 요청
        ("RC01", False),
        ("T01", False),  # 기록만
    ],
)
def test_순수_요청형_판정(case_id: str, expected: bool) -> None:
    case = CASES_BY_ID[case_id]
    assert covers_only_requests(case.text, answer_output(case)) is expected


def test_조각에_안_들어간_글자가_있으면_순수_요청형이_아니다() -> None:
    # Supervisor 가 기록을 빠뜨리고 요청만 잘랐을 수 있다 — 생략하면 그 기록이 사라진다
    raw = "오늘 공원에서 킥보드를 탔어. 주말에는 뭐 하고 놀면 좋을까?"
    output = _output(
        {"text": "주말에는 뭐 하고 놀면 좋을까?", "kind": "request", "agent": "activity"}
    )
    assert covers_only_requests(raw, output) is False


def test_기록이_섞여_있으면_건너뛰지_않는다() -> None:
    # 요청이 있어도 기록 조각이 하나라도 있으면 Memory 를 부른다
    assert _route_case("RC08").memory_task is not None


def test_생략을_끄면_순수_요청형도_Memory_를_부른다(monkeypatch: pytest.MonkeyPatch) -> None:
    # 되돌리는 경로. Supervisor 가 기록을 요청으로 잘못 잘라도 Memory 가 원문을 한 번 더 본다
    monkeypatch.setattr(routing_module, "SKIP_MEMORY_FOR_PURE_REQUEST", False)
    assert _route_case("RC07").memory_task is not None
