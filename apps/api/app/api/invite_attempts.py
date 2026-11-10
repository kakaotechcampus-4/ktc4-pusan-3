"""초대 코드 시도 제한 — 실패가 1시간에 계정 5회 · IP 30회를 넘으면 429 (#198 · invite-v1.md §4).

8자 코드(40비트)는 이 제한이 있어야 성립한다. 확인(GET /invites/{code})과 수락이
**같은 통**을 쓴다 — 확인은 코드를 소비하지 않아 수락보다 좋은 추측 도구라, 따로 세면 막은
것이 아니다.

🚨 실패를 **먼저 잡아 두고**, 실패가 아니었으면 돌려준다. 끝난 뒤에 세면 한꺼번에 보낸 요청이
   전부 검사를 통과한 뒤 DB 를 기다리는 동안 한도를 넘는다. acquire 의 확인과 기록이 한 트랜잭션 ·
   통마다 잠금이라 워커가 여럿이어도 동시에 와서 한도를 넘지 않는다 (quota.py 와 같은 원칙).

🚨 워커 밖 카운터(`rate_event`, #877 3단계)다. 한 번마다 자기 세션으로 바로 commit 한다.
"""

import ipaddress
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.db import background_session
from app.domains.rate_event.repository import give_back, take

ACCOUNT_LIMIT = 5
IP_LIMIT = 30
WINDOW = timedelta(hours=1)

_session_factory: Callable[[], AsyncSession] = background_session


def ip_bucket(host: str | None) -> str:
    """IPv6 는 /64 로 묶는다.

    한 가입자가 보통 /64 하나를 통째로 받아서, 주소를 바꿔 가며 IP 제한을 피할 수 있다.
    """
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    # 듀얼 스택 소켓(--host ::)은 IPv4 를 ::ffff:1.2.3.4 로 넘긴다. /64 로 묶으면 IPv4 전체가
    # ::/64 한 버킷이 되므로 IPv4 로 되돌린다.
    if address.version == 6 and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _keys(parent_id: UUID, ip: str) -> list[tuple[str, int]]:
    return [(f"invite:parent:{parent_id}", ACCOUNT_LIMIT), (f"invite:ip:{ip}", IP_LIMIT)]


async def acquire(*, parent_id: UUID, ip: str, now: datetime) -> bool:
    """실패 한 건을 미리 잡는다. 이미 한도면 잡지 않고 False — 호출부가 429 로 답한다."""
    async with _session_factory() as session:
        taken = await take(session, limits=_keys(parent_id, ip), since=now - WINDOW, at=now)
        await session.commit()
    return taken


async def release(*, parent_id: UUID, ip: str, now: datetime) -> None:
    """실패가 아니었다 — acquire 가 그 시각에 잡은 한 건을 돌려준다."""
    async with _session_factory() as session:
        await give_back(session, keys=[key for key, _ in _keys(parent_id, ip)], at=now)
        await session.commit()
