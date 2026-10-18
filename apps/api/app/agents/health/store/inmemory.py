"""Health 포트의 인메모리 구현. 테스트·eval이 DB 없이 HealthPorts를 주입할 때 쓴다.

실패를 흉내 낼 때는 `fail=True` — 빈 결과와 구분해야 한다 (루트 §2).

🚨 **동의는 기본값이 없다.** 어느 아이가 동의했는지 생성자에서 명시해야 하고, 적지 않은 아이는
`KeyError` 다. 기본값이 True 면 동의를 철회한 아이의 건강정보가 테스트에서 아무렇지 않게 읽힌다.
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from app.agents.health.store.ports import HealthPorts, HealthSafetyEntry, SafetyLookupError


class InMemoryProfile:
    def __init__(self, birth_dates: dict[UUID, date] | None = None) -> None:
        self._birth_dates = dict(birth_dates or {})

    async def birth_date(self, *, child_id: UUID) -> date:
        return self._birth_dates[child_id]


class InMemoryConsent:
    def __init__(self, granted: dict[UUID, bool]) -> None:
        self._granted = dict(granted)

    async def child_health_granted(self, *, child_id: UUID) -> bool:
        if child_id not in self._granted:
            raise KeyError(f"동의 여부를 지정하지 않은 아이: {child_id}")
        return self._granted[child_id]


class InMemoryHealthSafety:
    def __init__(self, entries: Sequence[HealthSafetyEntry] = (), *, fail: bool = False) -> None:
        self._entries = list(entries)
        self._fail = fail
        self.calls = 0  # 동의가 없거나 안 쓰는 라벨이면 아예 읽지 않는지 테스트가 본다

    async def health_safety(self, *, child_id: UUID) -> list[HealthSafetyEntry]:
        self.calls += 1
        if self._fail:
            raise SafetyLookupError("health_safety 조회 실패 (테스트 주입)")
        return list(self._entries)


def in_memory_ports(
    child_id: UUID,
    birth_date: date,
    *,
    consent: bool,
    safety: InMemoryHealthSafety | None = None,
) -> HealthPorts:
    """아이 하나 몫의 포트 묶음. `consent` 는 꼭 적는다 — 기본값이 없다."""
    return HealthPorts(
        profile=InMemoryProfile({child_id: birth_date}),
        consent=InMemoryConsent({child_id: consent}),
        safety=safety or InMemoryHealthSafety(),
    )
