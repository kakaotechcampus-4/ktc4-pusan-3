"""MemoryTask에 따라 모델에 노출할 tool 묶음을 결정한다.

기록용 tool은 항상 포함하고, 수정·삭제용 tool은 lookup_edit 작업이 있을 때만 추가한다.
수정과 삭제는 같은 묶음으로 관리한다.

현재 tool 조합은 두 가지이며, 각 조합별 프롬프트 캐시를 유지한다.
새 tool을 추가할 때는 어느 묶음에 포함할지 함께 정하고 test_bundles에서 누락 여부를 확인한다.
"""

from app.agents.memory.schemas.task import MemoryTask

_DOMAINS = ("food", "health", "education", "activity", "routine")

# 기록 기본 묶음: 관찰 create 5 + 일정/준비물 등록 + 준비물을 붙일 일정 찾기
RECORD_BASE: frozenset[str] = frozenset(
    {
        *(f"create_observation_{domain}" for domain in _DOMAINS),
        "create_event",
        "query_event",
        "create_event_item",
    }
)

# 조회·수정·삭제 묶음: 관찰 query·update·delete 15 + 일정·준비물 update·delete 4
LOOKUP_EDIT: frozenset[str] = frozenset(
    {
        *(
            f"{operation}_observation_{domain}"
            for operation in ("query", "update", "delete")
            for domain in _DOMAINS
        ),
        "update_event",
        "delete_event",
        "update_event_item",
        "delete_event_item",
    }
)


def tools_for(task: MemoryTask) -> frozenset[str]:
    """이번 작업에서 모델에게 열 tool."""
    return RECORD_BASE | LOOKUP_EDIT if task.open_lookup_edit else RECORD_BASE
