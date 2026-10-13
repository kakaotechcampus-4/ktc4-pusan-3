"""생일 · 동의 포트와 SafetyLookupError 는 세 Agent 가 같은 객체를 쓴다 (#319 2부).

백엔드 어댑터는 `common/ports.py` 의 예외를 올린다. 어느 Agent 가 자기 사본을 잡으면
조회 실패에 닫히지 않고 run 이 예외로 끝난다.
"""

import pytest

from app.agents.activity import store as activity_store
from app.agents.activity.store import ports as activity_ports
from app.agents.common import ports as common_ports
from app.agents.food import store as food_store
from app.agents.food.store import ports as food_ports
from app.agents.growth.store import ports as growth_ports

NAMES = ("SafetyLookupError", "ChildProfileReader", "ConsentReader")


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize(
    "module", [food_ports, activity_ports, growth_ports, food_store, activity_store]
)
def test_Agent_의_포트는_common_과_같은_객체다(module, name):
    assert getattr(module, name) is getattr(common_ports, name)


@pytest.mark.parametrize("module", [food_ports, activity_ports, growth_ports])
def test_common_예외를_Agent_이름으로_잡는다(module):
    with pytest.raises(module.SafetyLookupError):
        raise common_ports.SafetyLookupError("어댑터 조회 실패")
