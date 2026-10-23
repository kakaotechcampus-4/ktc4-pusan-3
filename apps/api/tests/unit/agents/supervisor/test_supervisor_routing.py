"""routing 규칙 검증. LLM 을 부르지 않는다 — Supervisor 정답 출력을 넣고 누구를 부를지 본다.

여기가 무너지면 Supervisor 가 맞게 나눠도 Food 가 엉뚱한 조각을 받거나,
Supervisor 가 틀렸을 때 기록까지 사라진다.
"""

from typing import Any

import pytest

from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.task import task_type_of
from app.agents.memory.schemas.task import WorkType
from app.agents.supervisor import routing as routing_module
from app.agents.supervisor.agent import BAD_JSON, SupervisorResult
from app.agents.supervisor.routing import (
    AGENT_LIMIT,
    MAX_DOMAIN_AGENTS,
    SKIP_MEMORY_FOR_PURE_REQUEST,
    TASK_LIMIT,
    covers_only_requests,
    route,
)
from app.agents.supervisor.schemas import SegmentKind, SupervisorOutput
from tests.eval.agents.routing_cases import (
    CASES_BY_ID,
    GR_ALWAYS_GROWTH,
    GR_CASES,
    GR_NEVER_REQUEST,
    SplitScore,
    answer_output,
    covered_by,
    growth_violations,
    sum_scores,
)

RUN_ID = "run-1"


@pytest.fixture
def food_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """미구현 agent 경로를 보는 테스트. activity 가 붙은 뒤에도(#380) 그 경로를 보도록 구현된
    agent 를 food 하나로 고정한다 — 아직 없는 agent 예시로 activity 를 쓰고 있다."""
    monkeypatch.setattr(routing_module, "IMPLEMENTED_AGENTS", frozenset({"food"}))


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
    assert [task.task_type for task in routing.domain_tasks] == [FoodTaskType.MEAL_RECOMMENDATION]
    assert routing.domain_tasks[0].request_texts == ("저녁에는 뭐 먹이는 게 좋을까?",)


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
    assert [task.task_type for task in routing.domain_tasks] == [FoodTaskType.NUTRIENT_ANALYSIS]


@pytest.mark.usefixtures("food_only")
def test_RC16_미구현_agent_는_부르지_않고_남긴다() -> None:
    routing = _route_case("RC16")
    assert [task.task_type for task in routing.domain_tasks] == [FoodTaskType.MEAL_RECOMMENDATION]
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
    assert routing.domain_tasks == ()


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
    assert routing.domain_tasks == ()
    assert routing.memory_task is not None
    assert routing.memory_task.open_lookup_edit is True


def test_RC24_식사_알림은_일정_힌트_하나() -> None:
    routing = _route_case("RC24")
    assert routing.memory_task is not None
    assert [hint.work for hint in routing.memory_task.hints] == [WorkType.SCHEDULE]
    assert routing.domain_tasks == ()


@pytest.mark.usefixtures("food_only")
def test_RC07_순수_요청형() -> None:
    routing = _route_case("RC07")
    assert routing.intent_type == "request"
    assert routing.unavailable_agents == ("activity",)
    assert routing.domain_tasks == ()


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


# ── DomainTask 묶기 ───────────────────────────────────────────────
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

    assert [task.task_type for task in routing.domain_tasks] == [
        FoodTaskType.NUTRIENT_ANALYSIS,  # 처음 나온 순서
        FoodTaskType.MEAL_RECOMMENDATION,
    ]
    assert all(task.run_id == RUN_ID for task in routing.domain_tasks)


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

    assert len(routing.domain_tasks) == 1
    assert routing.domain_tasks[0].request_texts == (
        "내일 간식은 뭐가 좋을까?",
        "저녁 메뉴도 추천해줘",
    )


def test_라벨은_문자열로_넘기고_enum_변환은_Agent_가_한다() -> None:
    # routing 이 Agent 의 enum 을 알면 Agent 가 늘 때마다 여기를 고쳐야 한다
    task = _route_case("RC02").domain_tasks[0]
    assert type(task.task_type) is str
    assert task_type_of(task) is FoodTaskType(task.task_type)


@pytest.mark.usefixtures("food_only")
def test_RC16_구현된_agent_몫만_DomainTask_가_된다() -> None:
    routing = _route_case("RC16")  # 저녁 메뉴(food) + 주말 놀이(activity)
    assert [task.agent for task in routing.domain_tasks] == ["food"]
    assert routing.unavailable_agents == ("activity",)


def test_RC16_라벨이_없는_agent_는_task_type_이_None(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routing_module, "IMPLEMENTED_AGENTS", frozenset({"food", "activity"}))
    routing = _route_case("RC16")
    assert [(task.agent, task.task_type) for task in routing.domain_tasks] == [
        ("food", "meal_recommendation"),
        ("activity", None),
    ]
    assert routing.unavailable_agents == ()


def test_힌트의_작업_종류도_enum_이다() -> None:
    routing = _route_case("RC03")
    assert routing.memory_task is not None
    assert all(isinstance(hint.work, WorkType) for hint in routing.memory_task.hints)


# ── agent 상한 (NF-01) ──────────────────────────────────────────
@pytest.mark.usefixtures("food_only")
def test_RC17_agent_가_셋이면_뒤의_것을_뺀다() -> None:
    routing = _route_case("RC17")

    assert MAX_DOMAIN_AGENTS == 2
    assert [task.task_type for task in routing.domain_tasks] == [FoodTaskType.MEAL_RECOMMENDATION]
    assert routing.unavailable_agents == ("activity",)
    assert routing.dropped_agents == ("growth",)


def test_RC17_상한에_걸려_뺀_요청은_안내한다() -> None:
    # 셋을 물었는데 둘만 답하고 이유를 안 알리면 사용자는 나머지가 어디 갔는지 모른다
    routing = _route_case("RC17")
    assert AGENT_LIMIT in [guidance.code for guidance in routing.guidance]


def test_상한에_안_걸리면_그_안내는_없다() -> None:
    assert AGENT_LIMIT not in [guidance.code for guidance in _route_case("RC16").guidance]


@pytest.mark.usefixtures("food_only")
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

    assert routing.domain_tasks == ()
    assert routing.dropped_agents == ("food",)


# ── 강등 (S5) ───────────────────────────────────────────────────
def test_Supervisor_가_실패하면_Memory_단독으로_간다() -> None:
    raw = "오늘 사과 먹었어. 저녁엔 뭘 먹일까?"
    routing = route(raw, SupervisorResult(output=None, error=BAD_JSON), run_id=RUN_ID)

    assert routing.degraded is True
    assert routing.memory_task is not None
    assert routing.memory_task.raw_text == raw
    assert routing.memory_task.hints == ()  # 힌트 없음
    # 수정인지 모르니 묶음을 연다. 수정 문장이 강등되면 새 행이 생겼다 (#483)
    assert routing.memory_task.open_lookup_edit is True
    assert routing.memory_task.unclear == ()  # 못 본 것이지 아이 기록이 아니라고 한 게 아니다
    assert routing.domain_tasks == ()  # Food 호출 없음
    assert routing.guidance == ()


def test_정상_경로는_강등이_아니다() -> None:
    assert _route_case("RC01").degraded is False


# ── unclear (#484) ──────────────────────────────────────────────
def test_unclear_조각만_있으면_Memory_에_그_조각을_넘긴다() -> None:
    raw = "딸기 맛있다."
    output = _output({"text": "딸기 맛있다", "kind": "unclear"})
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    assert routing.memory_task is not None
    assert routing.memory_task.unclear == ("딸기 맛있다",)
    assert routing.memory_task.unclear_only is True
    assert routing.memory_task.open_lookup_edit is True  # "자동차로 바꿔줘" 가 unclear 로 남는 경우


def test_조각에_안_들어간_글자가_있으면_unclear_only_가_아니다() -> None:
    # Supervisor 가 기록을 빠뜨렸을 수 있다 — unclear 조각만 막고 나머지는 Memory 가 본다
    raw = "딸기 맛있다. 오늘 공원에서 킥보드 탔어."
    output = _output({"text": "딸기 맛있다", "kind": "unclear"})
    task = route(raw, SupervisorResult(output=output), run_id=RUN_ID).memory_task

    assert task is not None
    assert task.unclear == ("딸기 맛있다",)
    assert task.unclear_only is False


def test_기록_조각과_섞이면_unclear_only_가_아니다() -> None:
    raw = "딸기 맛있다. 오늘 블록놀이 했어."
    output = _output(
        {"text": "딸기 맛있다", "kind": "unclear"},
        {"text": "오늘 블록놀이 했어", "kind": "record", "work": "observe"},
    )
    task = route(raw, SupervisorResult(output=output), run_id=RUN_ID).memory_task

    assert task is not None
    assert task.unclear == ("딸기 맛있다",)
    assert task.unclear_only is False


def test_기록_조각만_있으면_수정_묶음은_닫혀_있다() -> None:
    task = _route_case("T01").memory_task
    assert task is not None and task.unclear == () and task.open_lookup_edit is False


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


def test_growth_조각은_라벨을_task_type_으로_넘긴다() -> None:
    raw = "잘 크고 있어?"
    output = _output(
        {"text": raw, "kind": "request", "agent": "growth", "growth_task": "growth_review"}
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)
    (task,) = routing.domain_tasks
    assert (task.agent, task.task_type) == ("growth", "growth_review")


def _growth(text: str, label: str) -> dict[str, Any]:
    return {"text": text, "kind": "request", "agent": "growth", "growth_task": label}


def test_혼합형의_기록_조각을_도메인_task_에도_싣는다() -> None:
    """요청 조각만으로는 무엇을 묻는지 모른다 — Growth 닫힘 판정이 같은 입력의 기록 조각을 본다."""
    raw = "약 먹기 싫어해. 어떻게 하지?"
    output = _output(
        {"text": "약 먹기 싫어해", "kind": "record", "work": "observe"},
        _growth("어떻게 하지?", "routine_coaching"),
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)
    (task,) = routing.domain_tasks
    assert task.request_texts == ("어떻게 하지?",)
    assert task.record_texts == ("약 먹기 싫어해",)


def test_기록_조각이_없으면_빈_튜플이다() -> None:
    raw = "양치 혼자 하게 하고 싶어"
    routing = route(
        raw,
        SupervisorResult(output=_output(_growth(raw, "routine_coaching"))),
        run_id=RUN_ID,
    )
    assert routing.domain_tasks[0].record_texts == ()


def test_한_run_에_Growth_추천은_처음_것_하나만_돈다() -> None:
    raw = "숫자 놀이랑 양치 혼자 하는 법 알려줘"
    output = _output(
        _growth("숫자 놀이 알려줘", "learning_suggestion"),
        _growth("양치 혼자 하는 법 알려줘", "routine_coaching"),
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    assert [(t.agent, t.task_type) for t in routing.domain_tasks] == [
        ("growth", "learning_suggestion")
    ]
    assert TASK_LIMIT in [guidance.code for guidance in routing.guidance]


def test_성장_추이는_추천과_같이_돈다() -> None:
    raw = "잘 크고 있어? 숫자 놀이도 추천해줘"
    output = _output(
        _growth("잘 크고 있어?", "growth_review"),
        _growth("숫자 놀이도 추천해줘", "learning_suggestion"),
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    assert [t.task_type for t in routing.domain_tasks] == ["growth_review", "learning_suggestion"]
    assert TASK_LIMIT not in [guidance.code for guidance in routing.guidance]


def test_같은_Growth_라벨_조각_둘은_한_task_로_묶인다() -> None:
    raw = "한글 공부랑 숫자 공부 추천해줘"
    output = _output(
        _growth("한글 공부 추천해줘", "learning_suggestion"),
        _growth("숫자 공부 추천해줘", "learning_suggestion"),
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    (task,) = routing.domain_tasks
    assert task.request_texts == ("한글 공부 추천해줘", "숫자 공부 추천해줘")
    assert TASK_LIMIT not in [guidance.code for guidance in routing.guidance]


def test_도서는_다른_Growth_추천이_같이_오면_자리를_내준다() -> None:
    # 도서는 운영에서 늘 닫혀서(closed.book_api), 앞에 온다고 열리는 요청까지 빼면 남는 것이 없다
    raw = "공룡 책이랑 양치 혼자 하는 법 알려줘"
    output = _output(
        _growth("공룡 책 알려줘", "book_suggestion"),
        _growth("양치 혼자 하는 법 알려줘", "routine_coaching"),
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    assert [(t.agent, t.task_type) for t in routing.domain_tasks] == [
        ("growth", "routine_coaching")
    ]
    assert TASK_LIMIT in [guidance.code for guidance in routing.guidance]


def test_도서만_있으면_도서가_돈다() -> None:
    raw = "공룡 책이랑 동물 책 추천해줘"
    output = _output(
        _growth("공룡 책 추천해줘", "book_suggestion"),
        _growth("동물 책 추천해줘", "book_suggestion"),
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    (task,) = routing.domain_tasks
    assert (task.task_type, task.request_texts) == (
        "book_suggestion",
        ("공룡 책 추천해줘", "동물 책 추천해줘"),
    )
    assert TASK_LIMIT not in [guidance.code for guidance in routing.guidance]


def test_도서가_양보해도_성장_추이는_같이_돈다() -> None:
    raw = "잘 크고 있어? 공룡 책이랑 양치 혼자 하는 법도 알려줘"
    output = _output(
        _growth("잘 크고 있어?", "growth_review"),
        _growth("공룡 책 알려줘", "book_suggestion"),
        _growth("양치 혼자 하는 법도 알려줘", "routine_coaching"),
    )
    routing = route(raw, SupervisorResult(output=output), run_id=RUN_ID)

    assert [t.task_type for t in routing.domain_tasks] == ["growth_review", "routine_coaching"]
    assert TASK_LIMIT in [guidance.code for guidance in routing.guidance]


# ── GR01~GR24 — Growth 경계 (정답 출력이 routing 을 지나는 길) ────────
@pytest.mark.parametrize("case", GR_CASES, ids=[case.case_id for case in GR_CASES])
def test_GR_정답_출력은_적어_둔_라벨_밖으로_안_가고_0건_규칙을_지킨다(case) -> None:
    """정답 표의 오타 · 0건 규칙 검사기의 오판을 라이브 전에 잡는다."""
    wanted = {
        (str(span.agent), str(span.growth_task) if span.growth_task else None)
        for span in case.spans
        if span.kind == SegmentKind.REQUEST
    }
    routing = _route_case(case.case_id)

    assert {(t.agent, t.task_type) for t in routing.domain_tasks} <= wanted
    assert growth_violations(case.case_id, answer_output(case)) == []


def test_GR_배웠대_는_도메인_Agent_를_부르지_않는다() -> None:
    for case_id in GR_NEVER_REQUEST:
        routing = _route_case(case_id)
        assert routing.domain_tasks == ()
        assert routing.memory_task is not None


def test_GR_신체_성장은_growth_review_하나로_간다() -> None:
    for case_id in GR_ALWAYS_GROWTH:
        tasks = [(t.agent, t.task_type) for t in _route_case(case_id).domain_tasks]
        assert tasks == [("growth", "growth_review")]


def test_GR23_놀이와_교육은_두_Agent_로_간다() -> None:
    routing = _route_case("GR23")
    assert [(t.agent, t.task_type) for t in routing.domain_tasks] == [
        ("activity", None),
        ("growth", "learning_suggestion"),
    ]


def test_GR24_도서와_루틴이면_루틴이_돈다() -> None:
    routing = _route_case("GR24")
    assert [(t.agent, t.task_type) for t in routing.domain_tasks] == [
        ("growth", "routine_coaching")
    ]
    assert TASK_LIMIT in [guidance.code for guidance in routing.guidance]


def test_신체_성장이_guarded_로_가면_0건_규칙_위반이다() -> None:
    bad = _output({"text": "잘 크고 있어?", "kind": "guarded", "guard": "diagnosis"})
    assert growth_violations("GR13", bad)


def test_배웠대_가_요청이_되면_0건_규칙_위반이다() -> None:
    bad = _output(_growth("오늘 블록 쌓는 거 배웠대", "learning_suggestion"))
    assert growth_violations("GR04", bad)


def test_약_을_기록으로_떼도_Growth_가_같이_보므로_위반이_아니다() -> None:
    # 기록 조각은 DomainTask.record_texts 로 Growth 닫힘 판정에 같이 간다
    split = _output(
        {"text": "약 먹기 싫어해", "kind": "record", "work": "observe"},
        _growth("어떻게 먹이지?", "routine_coaching"),
    )
    assert growth_violations("GR17", split) == []


def test_약_이_Growth_로도_기록으로도_안_가면_0건_규칙_위반이다() -> None:
    # 낱말이 안내 조각으로 빠지면 Growth 는 "어떻게 먹이지?" 만 받아 닫지 못하고 모델로 간다
    bad = _output(
        {"text": "약 먹기 싫어해", "kind": "out_of_scope"},
        _growth("어떻게 먹이지?", "routine_coaching"),
    )
    assert growth_violations("GR17", bad)


def test_점수_합계는_칸마다_더하고_놓친_구간에_케이스를_붙인다() -> None:
    first = SplitScore(case_id="GR01", request_spans=1, agent_ok=1)
    second = SplitScore(case_id="GR02", request_spans=1, growth_spans=1, missed=["request/growth"])
    total = sum_scores([first, second])

    assert (total.request_spans, total.agent_ok, total.growth_spans) == (2, 1, 1)
    assert total.missed == ["GR02:request/growth"]


def test_별도_설정_없이_Growth_요청은_준비_중이_아니라_도메인_task_로_간다() -> None:
    # 환경변수 스위치가 없다 — #446 이 들어가면 Growth 가 바로 열린다
    assert "growth" in routing_module.IMPLEMENTED_AGENTS
    raw = "잘 크고 있어?"
    routing = route(
        raw, SupervisorResult(output=_output(_growth(raw, "growth_review"))), run_id=RUN_ID
    )

    assert [(t.agent, t.task_type) for t in routing.domain_tasks] == [("growth", "growth_review")]
    assert routing.unavailable_agents == ()
