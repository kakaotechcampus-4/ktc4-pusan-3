"""근거·출처를 가리키는 참조 한 줄.

`suggestion_evidence` 행이 되고 `Readout.source_refs` 에 실린다.
다형 참조라 FK 가 아니다 — 대상이 있는지와 같은 `child_id` 인지는 서버가 검증한다
(`correction.target_kind` / `target_id` 와 같은 패턴).
"""

from dataclasses import dataclass
from datetime import date
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
    "daycare_meal",
]

# 문서 행. 참고일 뿐이라 개인화 근거로 세지 않는다.
DocKind = Literal["food_doc", "growth_doc", "activity_doc"]

# 메뉴 행 — Food 일반 추천만 쓴다(#442). 고른 메뉴 자체(레시피 카탈로그 행)라 문서 행이 없는
# 월령에서도 일반 추천에 근거가 붙는다. 아이 기록이 아니라 개인화 근거로 세지 않는다.
# 값은 `rules/menu_evidence.MENU_EVIDENCE_KIND` 와 같다
MenuKind = Literal["recipe_catalog"]

# 복약 코스 행 — Health 의 진료 준비 복약 체크 줄 · 열 타임라인 · 체크 현황(`dose.status`) ·
# "먹였어" 체크 안내가 단다. Health 는 추천을 내지 않아 `suggestion_evidence` 로 가지 않고
# `Readout.source_refs` 에만 실린다. 개인화 근거로 세지 않는다
MedicationKind = Literal["medication_schedule"]

# 책 행 — Growth 일반 도서 추천만 쓴다. 고른 책 자체(도서관 정보나루 서지 행)라 문서 행이 없어도
# 일반 추천에 근거가 붙는다 — Food 메뉴와 같은 방식이다(#442). 아이 기록이 아니라 개인화 근거로
# 세지 않는다. 값은 `rules/book_evidence.BOOK_EVIDENCE_KIND` 와 같다
BookKind = Literal["book_catalog"]

SourceKind = ChildRecordKind | DocKind | MenuKind | MedicationKind | BookKind

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

    `polarity` · `label` · `subject` 는 기피 검사에만 쓴다. 문서 행은 기본값 그대로다.
    `label` 은 화면용 이름이라 관찰이면 "물감 놀이 하다가 울었음" 같은 문장일 수 있고,
    `subject` 는 대조 키("물감")다 (Tool_공통 §5-3). 이유 문장에 둘 중 하나가 나오면
    밝힌 것으로 본다.

    `observed_on` 은 아이 기록의 날짜다(affinity 는 마지막 관찰일, 문서 행은 None). `build()` 가
    6개월 판정에 쓰고 `suggestion_evidence` 로는 나가지 않는다.
    """

    ref: Ref
    note: str
    polarity: int = 0
    label: str = ""
    subject: str = ""
    observed_on: date | None = None

    @property
    def is_avoidance(self) -> bool:
        """기피 근거인가. 인용했으면 `reason` 에 무엇을 피했는지 적어야 한다."""
        return self.polarity < 0

    def to_payload(self) -> dict[str, str]:
        return {
            "source_kind": self.ref.kind,
            "source_id": str(self.ref.id),
            "note": self.note,
        }


def count_child_records(refs: tuple[Ref, ...]) -> int:
    """개인화 근거 건수. `kind='personalized'` 인데 이 값이 0 이면 버그다."""
    return sum(1 for ref in refs if ref.is_child_record)
