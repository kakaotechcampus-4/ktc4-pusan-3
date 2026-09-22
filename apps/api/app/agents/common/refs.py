"""근거·출처를 가리키는 참조 한 줄.

`suggestion_evidence` 행이 되고 `Readout.source_refs` 에 실린다.
다형 참조라 FK 가 아니다 — 대상이 있는지와 같은 `child_id` 인지는 서버가 검증한다
(`correction.target_kind` / `target_id` 와 같은 패턴).
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

# 아이 기록. 개인화 근거로 센다.
ChildRecordKind = Literal[
    "observation_food",
    "observation_health",
    "observation_education",
    "observation_activity",
    "observation_routine",
    "profile_affinity",
    "child_growth_log",
    "notice",
    "intake_daily",
    "daycare_meal",
]

# 문서 행. 참고일 뿐이라 개인화 근거로 세지 않는다.
DocKind = Literal["food_doc", "growth_doc", "activity_doc"]

MemoryKind = ChildRecordKind | DocKind

_CHILD_RECORD_KINDS: frozenset[str] = frozenset(
    (
        "observation_food",
        "observation_health",
        "observation_education",
        "observation_activity",
        "observation_routine",
        "profile_affinity",
        "child_growth_log",
        "notice",
        "intake_daily",
        "daycare_meal",
    )
)


@dataclass(frozen=True)
class Ref:
    kind: MemoryKind
    id: UUID

    @property
    def is_child_record(self) -> bool:
        """개인화 근거로 셀 수 있는가.

        문서 행만 달고 나간 개인화 추천은 근거 0행과 같다. 행 수만 세면 그게 통과하므로
        품질 지표는 이 값이 참인 행만 센다 (docs/agents/data_model.md suggestion_evidence).
        """
        return self.kind in _CHILD_RECORD_KINDS

    def to_payload(self) -> dict[str, str]:
        return {"kind": self.kind, "id": str(self.id)}


def count_child_records(refs: tuple[Ref, ...]) -> int:
    """개인화 근거 건수. `kind='personalized'` 인데 이 값이 0 이면 버그다."""
    return sum(1 for ref in refs if ref.is_child_record)
