"""`reference/age_gates.yaml` 로더 (#372).

월령 경계값의 정본이다. 틀린 파일은 기본값으로 넘기지 않고 부팅 때 거부한다 — 단계 경계가 어긋나면
모든 게이트가 같이 어긋난다.
"""

import copy

import pytest

from app.agents.common.reference import AgeGates, age_gates, load_reference, parse_age_gates


def raw() -> dict:
    """실제 파일을 읽은 사본. 케이스마다 한 군데만 깨뜨린다."""
    return copy.deepcopy(load_reference("age_gates.yaml"))


def test_실제_파일을_읽는다():
    gates = age_gates()
    assert isinstance(gates, AgeGates)
    assert gates.stage_boundaries == (
        (0, "infant_milk"),
        (4, "infant_weaning"),
        (12, "toddler"),
        (36, "preschool"),
    )
    assert gates.v1_month_limit == 72


def test_부팅_때_한_번만_읽는다():
    assert age_gates() is age_gates()


def test_게이트_값은_옮기기_전과_같다():
    """#372 PR 1 은 동작이 그대로다."""
    gates = age_gates()
    assert gates.affinity_min_month == 18
    assert gates.together_only_below_month == 18
    assert gates.education_min_month == 12
    assert gates.step_min_month == 12
    assert gates.manner_min_month == 24
    assert gates.habit_min_month == 36


def test_Health_영아_취침_경계는_24개월():
    # 영아(0–23개월)는 자기 전을 취침 상수 대신 fixed 19:30 으로 낸다 (M-3)
    assert age_gates().infant_bedtime_below_month == 24


def test_Health_영아_발열_응급_경계는_3개월():
    # 생후 3개월 미만 + 48시간 안 발열이면 타임라인보다 응급 안내가 먼저다 (#557)
    assert age_gates().infant_fever_emergency_below_month == 3


def test_모양이_맞는_데이터는_통과한다():
    assert parse_age_gates(raw()) == age_gates()


def _set(path: tuple[str, ...], value):
    def change(data: dict) -> None:
        node = data
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return change


def _drop(path: tuple[str, ...]):
    def change(data: dict) -> None:
        node = data
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set(("엉뚱한_칸",), 1), "정해지지 않은 칸"),
        (_set(("gates", "엉뚱한_게이트"), 1), "정해지지 않은 칸"),
        (_set(("stage_boundaries", "teen"), 72), "단계"),
        (_drop(("gates", "habit_min_month")), "빠진 칸"),
        (_drop(("gates", "infant_fever_emergency_below_month")), "빠진 칸"),
        (_drop(("stage_boundaries", "toddler")), "단계"),
        (_drop(("v1_month_limit",)), "빠진 칸"),
        (_set(("gates", "habit_min_month"), "36"), "정수"),
        (_set(("gates", "habit_min_month"), 36.0), "정수"),
        (_set(("gates", "habit_min_month"), True), "정수"),
        (_set(("stage_boundaries", "toddler"), "12"), "정수"),
        (_set(("v1_month_limit",), "72"), "정수"),
        (_set(("gates", "habit_min_month"), -1), "범위"),
        (_set(("gates", "habit_min_month"), 73), "범위"),
        (_set(("stage_boundaries", "infant_milk"), 1), "0 에서 시작"),
        (_set(("stage_boundaries", "toddler"), 4), "오름차순"),
        (_set(("stage_boundaries", "toddler"), 3), "오름차순"),
        (_set(("stage_boundaries", "preschool"), 73), "범위"),
    ],
)
def test_틀린_파일은_거부한다(change, message):
    data = raw()
    change(data)
    with pytest.raises(ValueError, match=message):
        parse_age_gates(data)


def test_단계_이름이_Stage_와_다르면_거부한다():
    data = raw()
    data["stage_boundaries"] = {
        "infant_milk": 0,
        "infant_weaning": 4,
        "toddler": 12,
        "kinder": 36,
    }
    with pytest.raises(ValueError, match="단계"):
        parse_age_gates(data)


def test_단계_순서가_바뀌면_거부한다():
    """이름이 같아도 선언 순서가 월령 순서가 아니면 단계가 뒤바뀐다."""
    data = raw()
    data["stage_boundaries"] = {
        "infant_weaning": 4,
        "infant_milk": 0,
        "toddler": 12,
        "preschool": 36,
    }
    with pytest.raises(ValueError, match="단계"):
        parse_age_gates(data)
