"""모델에게는 관찰 · 일정을 지우는 tool 이 없다 (#535).

"지워줘" 에는 조회로 후보를 찾고 propose_delete 로 고른다. 보호자가 카드에서 체크를 고쳐
버튼을 누르면 서버가 지운다. 준비물은 아직 채팅으로 확인하고 지운다 (후속 이슈).
store 의 soft delete 는 store_contract.py 에서 본다.
"""

from app.agents.memory.bundles import LOOKUP_EDIT, RECORD_BASE
from app.agents.memory.registry import TOOL_SPECS, registered_names
from tests.unit.agents.memory.store_contract import DOMAINS as _DOMAINS


def _names() -> set[str]:
    return set(registered_names()) | {spec["function"]["name"] for spec in TOOL_SPECS}


def test_관찰과_일정을_지우는_tool_은_없다() -> None:
    names = _names() | LOOKUP_EDIT | RECORD_BASE

    assert not any(name.startswith("delete_observation_") for name in names)
    assert "delete_event" not in names
    assert {"delete_event_item", "propose_delete"} <= LOOKUP_EDIT


def test_update_스펙에는_status_가_없다() -> None:
    for domain in _DOMAINS:
        spec = next(
            spec
            for spec in TOOL_SPECS
            if spec["function"]["name"] == f"update_observation_{domain}"
        )
        assert "status" not in spec["function"]["parameters"]["properties"]
