"""Health tool 표 — 명세 §1 전체를 처음부터 담고, 열리는 것은 구현된 tool 뿐이다."""

import pytest

from app.agents.health.registry import (
    CODE_TOOLS,
    HOOK_HANDLERS,
    HOOK_TOOLS,
    IMPLEMENTED_TASKS,
    MODEL_TASKS,
    MODEL_TOOLS,
    OUTPUT_TOOLS,
    TOOL_HANDLERS,
    closed_readout_key,
    execute_tool,
    specs_for,
    tools_for,
)
from app.agents.health.result import ErrorCode
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.schemas.tool_defs import TOOL_DEFINITIONS
from tests.unit.agents.health.support import context, gate

# Health_Tool_명세.md §1 의 "모델 tool" · "코드 tool" 칸
SPEC_MODEL = {
    "visit_summary": {
        "search_health_observations",
        "compose_symptom_timeline",
        "show_fever_timeline",
    },
    "schedule_check": set(),
    "place_lookup": set(),
    "medication": {
        "search_medication_schedules",
        "create_medication_schedule",
        "update_medication_schedule",
        "stop_medication_schedule",
        "show_dose_status",
        "defer_dose_question",
    },
    "care_handoff": set(),
}
SPEC_CODE = {
    "visit_summary": {
        "collect_meals",
        "collect_medication_checks",
        "load_health_safety",
        "normalize_symptom_term",
        "group_symptom_episodes",
        "build_fever_timeline",
        "get_next_dose",
        "get_health_phrase",
    },
    "schedule_check": {
        "compute_checkup_schedule",
        "compute_vaccine_schedule",
        "summarize_checkup_context",
        "get_health_phrase",
    },
    "place_lookup": {"find_pediatric_places"},
    "medication": {
        "resolve_dose_timing",
        "normalize_symptom_term",
        "get_next_dose",
        "lookup_prescription_draft",
    },
    "care_handoff": {
        "search_medication_schedules",
        "load_health_safety",
        "compose_daycare_med_notice",
        "build_emergency_card",
    },
}


def test_모델_tool_표가_명세_1절과_같다():
    assert {t.value: set(v) for t, v in MODEL_TOOLS.items()} == SPEC_MODEL


def test_코드_tool_표가_명세_1절과_같다():
    assert {t.value: set(v) for t, v in CODE_TOOLS.items()} == SPEC_CODE


def test_출력_tool_은_모델_tool_안에_있다():
    assert all(OUTPUT_TOOLS[t] <= MODEL_TOOLS[t] for t in OUTPUT_TOOLS)


def test_모델을_부르는_라벨은_출력_tool_이_있는_라벨이다():
    assert MODEL_TASKS == {HealthTaskType.VISIT_SUMMARY, HealthTaskType.MEDICATION}


def test_hook_은_세_tool_이다():
    assert HOOK_TOOLS == {
        "check_symptom_repetition",
        "lookup_condition_guidance",
        "check_infant_fever",
    }


def test_구현된_hook_은_hook_tool_안에_있다():
    assert set(HOOK_HANDLERS) <= HOOK_TOOLS


def test_응급_hook_은_아직_등록하지_않는다():
    # 등록은 pipeline 이 hook 을 부르는 연결과 같이 한다 — 등록하면 eval 이 hook 호출을 요구한다
    assert "check_infant_fever" not in HOOK_HANDLERS


@pytest.mark.parametrize("label", list(HealthTaskType))
def test_동의가_없으면_전_라벨이_닫힌다(label):
    closed = gate(48, consent_child_health=False)

    assert closed_readout_key(label, closed) == "blocked.consent"
    assert tools_for(label, closed) == ()


@pytest.mark.parametrize("label", [HealthTaskType.VISIT_SUMMARY, HealthTaskType.CARE_HANDOFF])
def test_안전_조회_실패로는_닫지_않는다(label):
    # 명세 §7: 진행 + 경고. 닫을지 경고를 붙일지는 라벨 경로가 정한다
    assert closed_readout_key(label, gate(48, safety_ok=False)) is None


@pytest.mark.parametrize(
    "label",
    [
        label
        for label in HealthTaskType
        if label not in (HealthTaskType.MEDICATION, HealthTaskType.VISIT_SUMMARY)
    ],
)
def test_열리는_tool_은_구현된_것뿐이다(label):
    # 구현된 라벨이어도 핸들러가 있는 tool 만 열린다 — schedule_check 는 모델 tool 이 없다
    assert tools_for(label, gate(48)) == ()


def test_복약_라벨은_핸들러가_있는_모델_tool_만_이름순으로_연다():
    assert tools_for(HealthTaskType.MEDICATION, gate(48)) == (
        "create_medication_schedule",
        "defer_dose_question",
        "search_medication_schedules",
        "show_dose_status",
        "stop_medication_schedule",
        "update_medication_schedule",
    )


def test_복약의_읽기_전용_출구는_출력_tool_이다():
    # 둘 다 인자가 없고 부르면 라벨이 끝난다 — 모델이 이어서 다른 말을 덧붙이지 않는다
    assert {"show_dose_status", "defer_dose_question"} <= OUTPUT_TOOLS[HealthTaskType.MEDICATION]


def test_구현된_라벨은_schedule_check_medication_care_handoff_visit_summary_이다():
    assert IMPLEMENTED_TASKS == {
        HealthTaskType.SCHEDULE_CHECK,
        HealthTaskType.MEDICATION,
        HealthTaskType.CARE_HANDOFF,
        HealthTaskType.VISIT_SUMMARY,
    }


def test_정의와_핸들러가_같다():
    assert {d.name for d in TOOL_DEFINITIONS} == set(TOOL_HANDLERS)


def test_스펙은_핸들러가_있는_tool_만_싣는다():
    # build_fever_timeline 은 코드 tool 이라 정의 · 핸들러가 없다
    specs = specs_for(["build_fever_timeline", "search_health_observations"])

    assert [spec["function"]["name"] for spec in specs] == ["search_health_observations"]


def test_진료_준비_식사_복약은_모델_tool_이_아니다():
    # 모델에게 고르게 했더니 라이브 eval 에서 건너뛰었다 — 코드가 늘 읽는다 (#551 리뷰)
    for name in ("search_recent_meals", "search_medication_checks"):
        assert name not in MODEL_TOOLS[HealthTaskType.VISIT_SUMMARY]
        assert name not in TOOL_HANDLERS
    assert "search_health_observations" in TOOL_HANDLERS


def test_진료_준비_라벨은_증상_조회와_출력_둘만_이름순으로_연다():
    # 코드 tool(build_fever_timeline · collect_* 등)은 모델에 직접 열리지 않는다 — 열 경과만 묻는
    # 말은 인자 없는 출구(show_fever_timeline)가 그 코드 tool 을 부른다. 약 이름 · 용량을
    # 모델에게 보여 주는 search_medication_schedules 도 열지 않는다 (#551)
    assert tools_for(HealthTaskType.VISIT_SUMMARY, gate(48)) == (
        "compose_symptom_timeline",
        "search_health_observations",
        "show_fever_timeline",
    )


def test_열_경과_출구는_출력_tool_이다():
    assert "show_fever_timeline" in OUTPUT_TOOLS[HealthTaskType.VISIT_SUMMARY]


@pytest.mark.parametrize("name", ["show_dose_status", "defer_dose_question", "show_fever_timeline"])
def test_읽기_전용_출구는_다른_출력_tool_과_같이_부르지_말라고_안내한다(name):
    # 한 스텝에서 출력 tool 이 하나라도 통과하면 loop 가 끝난다. 항상 성공하는 읽기 전용 출구를
    # 같은 응답에서 부르면, 인자 오류로 실패한 쓰기 출력 tool 은 고칠 기회 없이 요청이 사라진다
    description = next(d.description for d in TOOL_DEFINITIONS if d.name == name)

    assert "다른 출력 tool 과 같은 응답에서 부르지 않는다" in description


def test_열_타임라인_출구_설명은_긴_기간이면_정리하라고_안내한다():
    # 열 타임라인은 최근 48시간이다 — "요즘" · "최근" 처럼 며칠 넘는 기간은 정리가 맞다
    description = next(d.description for d in TOOL_DEFINITIONS if d.name == "show_fever_timeline")

    assert "요즘" in description and "정리" in description


def test_용량_질문_출구_설명은_표가_받는_말을_넣지_않는다():
    # "더 먹여도" · "또 먹여도" 는 표가 먼저 받아 dose.status 로 간다 — 두 길이 어긋나면 안 된다
    description = next(d.description for d in TOOL_DEFINITIONS if d.name == "defer_dose_question")

    assert "더 먹여도" not in description


async def test_없는_tool_은_거절():
    result = await execute_tool("compose_symptom_timeline", {}, context(), allowed=())

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.TOOL_NOT_ALLOWED


async def test_허용_목록_밖이면_핸들러가_있어도_거절():
    result = await execute_tool(
        "compose_symptom_timeline", {}, context(), allowed=("search_recent_meals",)
    )

    assert result.success is False
    assert result.error is not None and result.error["code"] == ErrorCode.TOOL_NOT_ALLOWED
