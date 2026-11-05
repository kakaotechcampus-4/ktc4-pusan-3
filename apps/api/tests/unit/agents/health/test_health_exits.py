"""읽기 전용 출구(인자 없는 출력 tool)를 잘못 부르는 길을 코드로 막는다.

`run_tool_loop` 는 한 응답 안의 tool 을 온 순서대로 실행하고, 출력 tool 이 하나라도 통과하면
그 스텝에서 끝낸다. 항상 성공하는 읽기 전용 출구가 같은 응답의 쓰기 출력 tool 과 같이 오면, 인자
오류로 실패한 쓰기 요청이 고쳐 볼 기회 없이 사라진다. 그래서 같은 응답에 다른 출력 tool 이 있으면
출구를 거절한다.
"""

import pytest

from app.agents.health.registry import OUTPUT_TOOLS
from app.agents.health.result import ErrorCode
from app.agents.health.schemas.task import HealthTaskType
from app.agents.health.tools.exits import READ_ONLY_EXITS, blocked_exit, same_response_calls

MEDICATION_OUTPUTS = OUTPUT_TOOLS[HealthTaskType.MEDICATION]


def _assistant(*names: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}
            for i, name in enumerate(names)
        ],
    }


def test_읽기_전용_출구는_셋이다():
    assert READ_ONLY_EXITS == {"show_dose_status", "defer_dose_question", "show_fever_timeline"}


def test_같은_응답은_가장_최근_assistant_메시지의_tool_이다():
    messages = [
        {"role": "system", "content": "s"},
        _assistant("create_medication_schedule"),
        {"role": "tool", "tool_call_id": "c0", "content": "{}"},
        _assistant("show_dose_status", "stop_medication_schedule"),
        {"role": "tool", "tool_call_id": "c0", "content": "{}"},
    ]

    assert same_response_calls(messages) == ("show_dose_status", "stop_medication_schedule")


def test_assistant_메시지가_없으면_비어_있다():
    assert same_response_calls([{"role": "user", "content": "x"}]) == ()


@pytest.mark.parametrize("write", ["create_medication_schedule", "update_medication_schedule"])
def test_쓰기_출력_tool_과_같은_응답이면_출구를_거절한다(write):
    messages = [_assistant("show_dose_status", write)]

    blocked = blocked_exit("show_dose_status", messages, output_tools=MEDICATION_OUTPUTS)

    assert blocked is not None and blocked.success is False
    assert blocked.operation is None  # 실행하기 전에 거절했다
    assert blocked.error is not None and blocked.error["code"] == ErrorCode.EXIT_REJECTED


def test_출구만_부르면_통과한다():
    messages = [_assistant("defer_dose_question")]

    assert blocked_exit("defer_dose_question", messages, output_tools=MEDICATION_OUTPUTS) is None


def test_출구끼리는_같이_불러도_통과한다():
    messages = [_assistant("show_dose_status", "defer_dose_question")]

    assert blocked_exit("show_dose_status", messages, output_tools=MEDICATION_OUTPUTS) is None
    assert blocked_exit("defer_dose_question", messages, output_tools=MEDICATION_OUTPUTS) is None


def test_조회_tool_과_같이_불러도_통과한다():
    # 조회는 출력이 아니다 — 쓰기 요청이 끝나는 것을 가리지 않는다
    messages = [_assistant("search_medication_schedules", "show_dose_status")]

    assert blocked_exit("show_dose_status", messages, output_tools=MEDICATION_OUTPUTS) is None


def test_출구가_아닌_tool_은_보지_않는다():
    messages = [_assistant("create_medication_schedule", "stop_medication_schedule")]

    assert (
        blocked_exit("create_medication_schedule", messages, output_tools=MEDICATION_OUTPUTS)
        is None
    )


def test_앞_응답의_쓰기_호출은_세지_않는다():
    # 쓰기가 앞 응답에서 끝났고 이번 응답에는 출구뿐이다
    messages = [_assistant("create_medication_schedule"), _assistant("show_dose_status")]

    assert blocked_exit("show_dose_status", messages, output_tools=MEDICATION_OUTPUTS) is None
