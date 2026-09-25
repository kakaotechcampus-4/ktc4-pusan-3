"""근거·출처를 가리키는 참조 한 줄.

`suggestion_evidence` 행이 되고 `Readout.source_refs` 에 실린다.
다형 참조라 FK 가 아니다 — 대상이 있는지와 같은 `child_id` 인지는 서버가 검증한다
(`correction.target_kind` / `target_id` 와 같은 패턴).
"""

from dataclasses import dataclass
from datetime import datetime
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

SourceKind = ChildRecordKind | DocKind

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
    kind: SourceKind
    id: UUID

    @property
    def is_child_record(self) -> bool:
        """개인화 근거로 셀 수 있는가.

        문서 행만 달고 나간 개인화 추천은 근거 0행과 같다. 행 수만 세면 그게 통과하므로
        품질 지표는 이 값이 참인 행만 센다 (docs/agents/data_model.md suggestion_evidence).
        `suggestion_evidence` 는 이 값을 `source_kind` 로 받는다 — Memory 소유 테이블만 가리키는
        게 아니라서 `memory_*` 가 아니다 (문서 행과 daycare_meal 도 들어온다).
        """
        return self.kind in _CHILD_RECORD_KINDS

    def to_payload(self) -> dict[str, str]:
        return {"kind": self.kind, "id": str(self.id)}


@dataclass(frozen=True)
class EvidenceCitation:
    """`suggestion_evidence` 한 행.

    `Ref`는 가리키는 것만 들고 있고, 왜 골랐는지와 언제 것인지는 여기 저장
    `note`는 Agent가 쓰므로, 문서 행이든 아이 기록이든 비워 둘 수 없다.
    "보호자 화면에 그대로 나가는 값"이다.

    `source_updated_at`은 인용할 때 읽은 원본의 시각이다. 아이 기록은 그 행의
    `updated_at`, 문서 행은 `written_at`이 들어온다.
    `polarity` 와 `label` 은 기피 검사에만 쓴다. 문서 행은 기본값 그대로다.
    """

    ref: Ref
    source_updated_at: datetime
    note: str
    polarity: int = 0
    label: str = ""

    @property
    def is_avoidance(self) -> bool:
        """기피 근거인가. 인용했으면 `reason` 에 무엇을 피했는지 적어야 한다."""
        return self.polarity < 0

    def to_payload(self) -> dict[str, str]:
        return {
            "source_kind": self.ref.kind,
            "source_id": str(self.ref.id),
            "source_updated_at": self.source_updated_at.isoformat(),
            "note": self.note,
        }


def count_child_records(refs: tuple[Ref, ...]) -> int:
    """개인화 근거 건수. `kind='personalized'` 인데 이 값이 0 이면 버그다."""
    return sum(1 for ref in refs if ref.is_child_record)
