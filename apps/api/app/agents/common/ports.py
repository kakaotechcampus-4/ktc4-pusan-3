"""도메인 Agent가 같이 쓰는 읽기 포트.

생일 · 동의 포트와 안전 조회 실패 예외는 Food · Activity · Growth 가 같은 이름 · 시그니처로 한 벌씩
갖고 있던 것을 옮겼다. 각 Agent 의 `store/ports.py` 가 여기서 가져가 그대로 내보낸다. 구현체는
`app/api` 가 붙인다 — TODO: 백엔드 조회 함수(#201)가 이 Protocol 을 구현한다.

health_safety 를 읽는 포트(`SafetyReader`)와 그 행(`SafetyEntry`)은 Agent 마다 둔다. 읽는 범위가
달라서(Food 는 active 행의 kind 전부, Activity · Growth 는 allergy · environmental 의 status 전부)
합치면 한쪽 규칙이 다른 쪽에 새어 들어간다. 읽기 실패 예외만 하나다 — 어댑터가
어느 Agent 에 붙든 같은 예외를 올리고, 각 Agent 의 `build_gate` 가 그 예외로 닫는다.

생일 · 동의는 지금은 포트다.
"""

from datetime import date
from typing import Protocol
from uuid import UUID

from app.agents.common.errors import (
    SafetyLookupError as SafetyLookupError,  # 다시 내보낸다 (#768)
)


class ChildProfileReader(Protocol):
    async def birth_date(self, *, child_id: UUID) -> date:
        """월령 · 식이 단계는 이 값에서 코드가 계산한다 (app/rules/age.py)."""
        ...


class ConsentReader(Protocol):
    async def child_health_granted(self, *, child_id: UUID) -> bool:
        """consent(scope=child_health) 최신 행이 granted 인가."""
        ...
