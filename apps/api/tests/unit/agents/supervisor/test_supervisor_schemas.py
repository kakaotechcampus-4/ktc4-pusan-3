"""Supervisor 출력 스키마·route tool 스펙 검증. LLM 을 부르지 않는다.

여기가 무너지면 라벨만 맞고 필드가 빈 조각이 통과해서, routing 이 그 조각을 어디로 보낼지 모른다.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from app.agents.food.schemas.common import FoodTaskType
from app.agents.memory.schemas.task import WorkType
from app.agents.supervisor.schemas import (
    EMPTY_TEXT,
    NOT_SUBSTRING,
    ROUTE_TOOL_SPEC,
    DomainAgentName,
    GuardReason,
    Segment,
    SegmentKind,
    SupervisorOutput,
    align_segment,
    validate_segments,
)
from tests.eval.agents.routing_cases import check_cases

RAW = "오늘 사과를 먹었어. 저녁에는 뭘 먹이면 좋을까?"


def _output(*segments: dict[str, Any]) -> SupervisorOutput:
    return SupervisorOutput.model_validate({"segments": list(segments)})


# ── route tool 스펙 ─────────────────────────────────────────────
def test_tool_이름과_설명이_있다() -> None:
    assert ROUTE_TOOL_SPEC["function"]["name"] == "route"
    assert len(ROUTE_TOOL_SPEC["function"]["description"]) >= 20


def test_스펙에_ref_가_남지_않는다() -> None:
    # Segment 가 중첩이라 $ref 가 남으면 모델이 스키마를 못 읽는다
    serialized = json.dumps(ROUTE_TOOL_SPEC, ensure_ascii=False)
    assert "$ref" not in serialized and "$defs" not in serialized


def test_조각은_최소_한_개다() -> None:
    parameters = ROUTE_TOOL_SPEC["function"]["parameters"]
    assert parameters["properties"]["segments"]["minItems"] == 1
    assert parameters["additionalProperties"] is False


def test_모든_필드에_description_이_있다() -> None:
    segment = ROUTE_TOOL_SPEC["function"]["parameters"]["properties"]["segments"]["items"]
    for name, schema in segment["properties"].items():
        assert schema.get("description"), name


def test_정해진_필드만_노출한다() -> None:
    # 아이 이름·날짜 같은 걸 모델이 채우게 두지 않는다
    segment = ROUTE_TOOL_SPEC["function"]["parameters"]["properties"]["segments"]["items"]
    assert set(segment["properties"]) == {"text", "kind", "work", "agent", "food_task", "guard"}


# ── enum 일치 ───────────────────────────────────────────────────
def test_agent_이름이_suggestion_과_한_칸만_다르다() -> None:
    """회의 §5 ㉡ — 각자 두고 parity 로 지킨다. 어긋나면 추천이 저장되지 않는다.

    지금은 Supervisor 만 growth 이고 백엔드는 education 이다 (AI Owner 결정 · 2026-09-14).
    백엔드 suggestion_agent enum 과 루트 CLAUDE.md §5 용어가 growth 로 바뀌면
    이 테스트가 깨진다 — 그때 양쪽이 같은지 보는 parity 로 되돌린다.
    """
    from app.domains.suggestion.models import SuggestionAgent

    supervisor = {member.value for member in DomainAgentName}
    backend = {member.value for member in SuggestionAgent}
    assert supervisor & backend == {"food", "activity", "health"}
    assert supervisor - backend == {"growth"}
    assert backend - supervisor == {"education"}


def test_작업_종류는_memory_어휘를_그대로_쓴다() -> None:
    assert {member.value for member in WorkType} == {"observe", "schedule", "lookup_edit"}


# ── kind 와 짝 ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "segment",
    [
        {"text": "오늘 사과를 먹었어", "kind": "record", "work": "observe"},
        {
            "text": "저녁에는 뭘 먹이면 좋을까?",
            "kind": "request",
            "agent": "food",
            "food_task": "meal_recommendation",
        },
        {"text": "저녁에는 뭘 먹이면 좋을까?", "kind": "request", "agent": "activity"},
        {"text": "오늘 사과를 먹었어", "kind": "guarded", "guard": "safety_record"},
        {"text": "오늘 사과를 먹었어", "kind": "out_of_scope"},
        {"text": "오늘 사과를 먹었어", "kind": "unclear"},
    ],
    ids=["record", "food요청", "activity요청", "guarded", "범위밖", "불명확"],
)
def test_짝이_맞으면_통과한다(segment: dict[str, Any]) -> None:
    assert _output(segment).segments[0].text


@pytest.mark.parametrize(
    "segment",
    [
        {"text": "x", "kind": "record"},  # work 없음
        {"text": "x", "kind": "guarded"},  # guard 없음
        {"text": "x", "kind": "record", "work": "eat"},  # 없는 work
        {"text": "x", "kind": "기록형", "work": "observe"},  # 없는 kind
    ],
    ids=["work없음", "guard없음", "없는work", "없는kind"],
)
def test_어디로_보낼지_모르면_거절한다(segment: dict[str, Any]) -> None:
    # 필드가 비면 routing 이 조각을 어디로 보낼지 알 수 없다 — 코드가 고칠 방법이 없다
    with pytest.raises(ValidationError):
        _output(segment)


@pytest.mark.parametrize(
    ("segment", "kind"),
    [
        ({"text": "오늘 사과 먹었어", "work": "observe"}, "record"),
        (
            {"text": "저녁 메뉴 추천해줘", "agent": "food", "food_task": "meal_recommendation"},
            "request",
        ),
        ({"text": "알레르기 등록해줘", "guard": "safety_record"}, "guarded"),
    ],
    ids=["work→record", "agent→request", "guard→guarded"],
)
def test_kind_만_빠지면_라벨_필드로_되짚는다(segment: dict[str, Any], kind: str) -> None:
    # 라이브 RC21 — 두 조각 모두 kind 만 빠져 분리 전체가 버려졌다.
    # work·agent·guard 는 각각 한 종류 전용이라 되짚을 수 있다
    assert _output(segment).segments[0].kind == kind


def test_되짚을_단서가_없으면_거절한다() -> None:
    with pytest.raises(ValidationError):
        _output({"text": "x"})


def test_값이_틀린_라벨_필드는_지운다() -> None:
    """라이브 T11 — agent 에 enum 밖의 값을 넣었다.

    필드를 지우면 나머지 규칙이 받는다(여기서는 agent 없는 request → unclear).
    조각 하나의 오타로 분리 전체를 버리지 않는다.
    """
    parsed = _output({"text": "모레 운동회 시간 바꿔줘", "kind": "request", "agent": "memory"})
    assert parsed.segments[0].kind == "unclear"
    assert parsed.segments[0].agent is None


def test_없는_kind_는_고치지_않는다() -> None:
    # 무엇을 하려던 조각인지 모르면 고칠 방법이 없다. 그 조각만 버린다 (agent.py)
    with pytest.raises(ValidationError):
        _output({"text": "x", "kind": "기록형", "work": "observe"})


def test_agent_를_못_고른_request_는_unclear_다() -> None:
    """라이브 RC01·RC03·RC17 — "내일 준비물 알림도 해줘" 를 kind=request · agent=null 로 냈다.

    어디로 보낼지 모르겠다는 뜻이고, 그 라벨은 unclear 다. 조각 하나 때문에 분리 전체를 버리면
    같은 발화의 멀쩡한 요청(저녁 메뉴 추천)까지 사라진다.
    """
    parsed = _output({"text": "내일 준비물 알림도 해줘", "kind": "request", "agent": None})
    segment = parsed.segments[0]
    assert segment.kind == "unclear"
    assert segment.agent is None


def test_food_에_유형이_없으면_추천으로_채운다() -> None:
    """라이브 RC17 — agent=food 만 주고 food_task 를 빠뜨렸다.

    프롬프트가 이미 "둘이 애매하면 meal_recommendation" 으로 정해 뒀다.
    없다고 출력 전체를 버리면 그 요청이 Food 에 아예 닿지 않는다.
    """
    parsed = _output({"text": "저녁 메뉴 추천해줘", "kind": "request", "agent": "food"}).segments[0]
    assert parsed.food_task == "meal_recommendation"


def test_work_만_있고_agent_가_없으면_record_로_고친다() -> None:
    """라이브 T15 — "오늘 기록 다 지워줘" 를 kind=request · work=lookup_edit 로 냈다.

    work 는 record 전용 필드다. 종류는 맞게 골랐으니 kind 를 고쳐 살린다.
    """
    parsed = _output({"text": "오늘 기록 다 지워줘", "kind": "request", "work": "lookup_edit"})
    segment = parsed.segments[0]
    assert (segment.kind, segment.work) == ("record", "lookup_edit")
    assert segment.agent is None


@pytest.mark.parametrize(
    ("segment", "gone"),
    [
        ({"text": "x", "kind": "record", "work": "observe", "agent": "food"}, "agent"),
        (
            {
                "text": "x",
                "kind": "request",
                "agent": "food",
                "food_task": "nutrient_analysis",
                "work": "lookup_edit",
            },
            "work",
        ),
        (
            {
                "text": "x",
                "kind": "request",
                "agent": "activity",
                "food_task": "meal_recommendation",
            },
            "food_task",
        ),
        ({"text": "x", "kind": "unclear", "work": "observe"}, "work"),
    ],
    ids=["record에agent", "request에work", "food아닌데food_task", "unclear에work"],
)
def test_짝이_아닌_필드는_버리고_통과한다(segment: dict[str, Any], gone: str) -> None:
    # 라이브 29회 중 6회가 이것 때문에 출력 전체를 잃고 강등됐다.
    # routing 은 짝이 아닌 필드를 읽지도 않으므로, 지우고 나머지 분리를 살린다 (S3)
    parsed = _output(segment).segments[0]
    assert getattr(parsed, gone) is None
    assert parsed.kind == segment["kind"]


def test_모르는_필드를_받지_않는다() -> None:
    with pytest.raises(ValidationError):
        _output({"text": "x", "kind": "unclear", "child_name": "민준"})


def test_조각이_없으면_거절한다() -> None:
    with pytest.raises(ValidationError):
        SupervisorOutput.model_validate({"segments": []})


# ── 부분 문자열 검사 ────────────────────────────────────────────
def test_원문에서_잘라냈으면_통과한다() -> None:
    output = _output(
        {"text": "오늘 사과를 먹었어", "kind": "record", "work": "observe"},
        {
            "text": "저녁에는 뭘 먹이면 좋을까?",
            "kind": "request",
            "agent": "food",
            "food_task": "nutrient_analysis",
        },
    )
    assert validate_segments(RAW, output) == []


def test_공백_차이는_허용한다() -> None:
    output = _output({"text": "오늘  사과를   먹었어", "kind": "record", "work": "observe"})
    assert validate_segments(RAW, output) == []


def test_의역하면_걸린다() -> None:
    # 의역은 "했대" 같은 출처 단서를 지운다 — 그 라벨을 Memory 가 붙인다
    output = _output({"text": "사과를 먹은 기록", "kind": "record", "work": "observe"})
    assert validate_segments(RAW, output) == [(1, NOT_SUBSTRING)]


# ── 공유 서술어 — request 조각만 ────────────────────────────────
def _request(text: str, agent: str = "food") -> dict[str, Any]:
    segment: dict[str, Any] = {"text": text, "kind": "request", "agent": agent}
    if agent == "food":
        segment["food_task"] = "meal_recommendation"
    return segment


def test_요청은_공유_서술어를_붙여도_된다() -> None:
    # RC16 실측 — 모델은 "저녁 메뉴랑 …" 을 "저녁 메뉴 추천해줘" 로 나눈다. Food 에도 이게 낫다
    raw = "오늘 수영장 다녀왔어. 저녁 메뉴랑 주말 놀이 추천해줘."
    output = _output(
        {"text": "오늘 수영장 다녀왔어", "kind": "record", "work": "observe"},
        _request("저녁 메뉴 추천해줘"),
        _request("주말 놀이 추천해줘", agent="activity"),
    )
    assert validate_segments(raw, output) == []


def test_셋을_나열해도_된다() -> None:
    # RC17 — "A도, B도, C도 다 추천해줘"
    raw = "저녁 메뉴도, 주말 놀이도, 한글 공부 방법도 다 추천해줘."
    output = _output(
        _request("저녁 메뉴 추천해줘"),
        _request("주말 놀이도 추천해줘", agent="activity"),
        _request("한글 공부 방법 다 추천해줘", agent="growth"),
    )
    assert validate_segments(raw, output) == []


def test_공유_서술어_조각은_두_구간으로_정렬된다() -> None:
    raw = "저녁 메뉴랑 주말 놀이 추천해줘."
    ranges = align_segment(raw, Segment.model_validate(_request("저녁 메뉴 추천해줘")))
    assert ranges is not None and len(ranges) == 2
    assert [raw[start:end] for start, end in ranges] == ["저녁 메뉴", "추천해줘"]


def test_기록은_공유_서술어를_받지_않는다() -> None:
    # 기록에서 말을 빼면 뜻이 바뀐다 — record 는 예외 없이 원문 그대로
    raw = "브로콜리랑 당근 다 남겼어."
    output = _output({"text": "브로콜리 다 남겼어", "kind": "record", "work": "observe"})
    assert validate_segments(raw, output) == [(1, NOT_SUBSTRING)]


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ("우유 말고 다른 간식 추천해줘.", "우유 추천해줘"),  # 뜻이 뒤집힌다
        ("우유랑 빵 빼고 추천해줘.", "우유 추천해줘"),  # 건너뛴 곳에 '빼고'
        ("우유랑 안 매운 반찬 추천해줘.", "우유 추천해줘"),  # 건너뛴 곳에 '안'
        ("저녁 메뉴. 주말 놀이 추천해줘.", "저녁 메뉴 추천해줘"),  # 문장을 넘는다
        ("간식 나중에 추천해줘.", "간식 추천해줘"),  # '나중에' 는 접속 조사가 아니다
        ("저녁 메뉴랑 주말 놀이 추천해줘서 고마워.", "저녁 메뉴 추천해줘"),  # 문장 끝이 아니다
    ],
    ids=["말고", "빼고", "안", "문장경계", "접속아님", "끝이아님"],
)
def test_뜻이_바뀌는_생략은_거절한다(raw: str, text: str) -> None:
    assert validate_segments(raw, _output(_request(text))) == [(1, NOT_SUBSTRING)]


def test_몇_번째_조각이_걸렸는지_알려준다() -> None:
    # 번호만 있으면 로그에 원문 없이 어디가 걸렸는지 적을 수 있다
    output = _output(
        {"text": "오늘 사과를 먹었어", "kind": "record", "work": "observe"},
        {"text": "지어낸 문장", "kind": "unclear"},
    )
    assert validate_segments(RAW, output) == [(2, NOT_SUBSTRING)]


def test_빈_조각은_걸린다() -> None:
    output = _output({"text": "   ", "kind": "unclear"})
    assert validate_segments(RAW, output) == [(1, EMPTY_TEXT)]


# ── 케이스 데이터 ───────────────────────────────────────────────
def test_정답_구간이_원문에_있다() -> None:
    # routing_cases 의 오타는 채점을 조용히 틀어지게 한다
    assert check_cases() == []


def test_food_유형은_두_가지다() -> None:
    assert {member.value for member in FoodTaskType} == {"meal_recommendation", "nutrient_analysis"}


def test_kind_와_guard_값() -> None:
    assert {member.value for member in SegmentKind} == {
        "record",
        "request",
        "guarded",
        "out_of_scope",
        "unclear",
    }
    assert {member.value for member in GuardReason} == {"safety_record", "diagnosis"}


def test_segment_는_enum_이_아니라_값을_담는다() -> None:
    # use_enum_values — routing 과 JSONL 이 문자열로 다룬다
    segment = Segment.model_validate({"text": "x", "kind": "record", "work": "observe"})
    assert segment.kind == "record" and segment.work == "observe"
