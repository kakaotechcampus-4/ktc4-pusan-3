"""생일 · 동의를 값으로 감싼 포트 (#372 PR 2).

API 가 run 마다 한 번 읽어 넘긴 값(`birth_date` · `child_health_granted`)을 도메인 Agent 가 받는
`ChildProfileReader` · `ConsentReader` 로 내보낸다. Agent 는 #319 의 Protocol 을 그대로 받고,
구현만 값이다 — DB 를 열지 않는다.

🚨 이 아이가 아닌 `child_id` 를 물으면 `LookupError` 다. 값 하나를 모든 아이에게 돌려주면 아이 축이
   섞였을 때 남의 생일 · 동의로 게이트가 열린다.
🚨 동의 값은 "읽어서 얻은 값" 이다. 조회에 실패한 것을 False 로 바꿔 넘기지 않는다 — 읽기에 실패하면
   이 객체를 만들기 전에 API 가 run 을 시작하지 않는다 (children 라우터).
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True)
class ChildValues:
    """한 아이의 생일 · `child_health` 동의.

    필드 이름은 포트 메서드(`birth_date` · `child_health_granted`)와 겹치지 않게
    `birth` · `health_consent` 로 둔다.
    """

    child_id: UUID
    birth: date
    health_consent: bool

    async def birth_date(self, *, child_id: UUID) -> date:
        self._check(child_id)
        return self.birth

    async def child_health_granted(self, *, child_id: UUID) -> bool:
        self._check(child_id)
        return self.health_consent

    def _check(self, child_id: UUID) -> None:
        if child_id != self.child_id:
            raise LookupError("이 run 의 아이가 아니다")
