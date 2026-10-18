"""Health가 읽는 데이터의 계약.

생일 · 동의 포트와 `SafetyLookupError` 는 `common/ports.py` 에 있다. health_safety 를 읽는
`HealthSafetyReader` 와 `HealthSafetyEntry` 는 Health 가 따로 둔다 — Food · Activity · Growth 와
읽는 범위가 달라서(active 행의 kind 전부, 관리 칸까지) 합치면 한쪽 규칙이 다른 쪽에 새어 들어간다.

| 포트 | 연결 대상 |
| ChildProfileReader | Child_Profile — birth_date 만 |
| ConsentReader | consent(scope=child_health) 최신 행 |
| HealthSafetyReader | health_safety — active 행, kind 전부. 응급 카드 · 진료 준비 · 반복 안내 |

복약 · 건강 관찰 · 체온 · 장소 포트는 그 tool 을 만드는 이슈가 더한다.
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from app.agents.common.ports import (
    ChildProfileReader,
    ConsentReader,
    SafetyLookupError,  # noqa: F401 — context.py · inmemory.py 가 여기서 가져간다
)

# health_safety 의 kind · status 와 같은 값. 어긋나면 어댑터 테스트가 잡는다
SafetyKind = Literal["allergy", "chronic_disease", "behavioral", "environmental", "other_medical"]
SafetyStatus = Literal["active", "retracted", "none"]


@dataclass(frozen=True)
class HealthSafetyEntry:
    kind: SafetyKind
    label: str  # 보호자가 적은 이름 원문
    status: SafetyStatus
    severity: str | None = None
    reactions: tuple[str, ...] = ()
    management: str | None = None


class HealthSafetyReader(Protocol):
    async def health_safety(self, *, child_id: UUID) -> list[HealthSafetyEntry]:
        """active 행만, kind 는 전부. 못 읽으면 `SafetyLookupError` — 빈 목록이 아니다."""
        ...


@dataclass(frozen=True)
class HealthPorts:
    """아이 하나 몫의 읽기 포트 묶음."""

    profile: ChildProfileReader
    consent: ConsentReader
    safety: HealthSafetyReader
