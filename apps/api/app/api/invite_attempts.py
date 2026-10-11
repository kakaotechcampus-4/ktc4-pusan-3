"""초대 코드 시도 제한 — 실패가 1시간에 계정 5회 · IP 30회를 넘으면 429 (#198 · invite-v1.md §4).

8자 코드(40비트)는 이 제한이 있어야 성립한다. 확인(GET /invites/{code})과 수락이
**같은 통**을 쓴다 — 확인은 코드를 소비하지 않아 수락보다 좋은 추측 도구라, 따로 세면 막은
것이 아니다.

🚨 실패를 **먼저 잡아 두고**, 실패가 아니었으면 돌려준다. 끝난 뒤에 세면 한꺼번에 보낸 요청이
   전부 검사를 통과한 뒤 DB 를 기다리는 동안 한도를 넘는다. acquire 의 확인과 기록 사이에
   await 가 없어서 동시에 와도 한도를 넘지 않는다 (quota.py 와 같은 원칙).

🚨 프로세스 메모리다. api 는 --workers 1 전제다 (#134). 재시작하면 카운터가 비워진다.
"""

import ipaddress
from collections import deque
from datetime import datetime, timedelta
from uuid import UUID

ACCOUNT_LIMIT = 5
IP_LIMIT = 30
WINDOW = timedelta(hours=1)

# 이보다 키가 많아지면 지난 실패만 남은 키를 한 번에 치운다. 다시 오지 않는 계정 · IP 의 키는
# 접근할 때만 정리되므로 그대로 두면 계속 쌓인다.
_SWEEP_AT = 10_000

_failures: dict[str, deque[datetime]] = {}


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


def _keys(parent_id: UUID, ip: str) -> tuple[tuple[str, int], tuple[str, int]]:
    return (f"parent:{parent_id}", ACCOUNT_LIMIT), (f"ip:{ip}", IP_LIMIT)


def _recent(key: str, now: datetime) -> deque[datetime]:
    entries = _failures.setdefault(key, deque())
    while entries and entries[0] <= now - WINDOW:
        entries.popleft()
    return entries


def acquire(*, parent_id: UUID, ip: str, now: datetime) -> bool:
    """실패 한 건을 미리 잡는다. 이미 한도면 잡지 않고 False — 호출부가 429 로 답한다."""
    if len(_failures) > _SWEEP_AT:
        for key in [k for k, v in _failures.items() if not v or v[-1] <= now - WINDOW]:
            del _failures[key]
    keys = _keys(parent_id, ip)
    if any(len(_recent(key, now)) >= limit for key, limit in keys):
        return False
    for key, _ in keys:
        _failures[key].append(now)
    return True


def release(*, parent_id: UUID, ip: str, now: datetime) -> None:
    """실패가 아니었다 — acquire 가 잡은 한 건을 돌려준다."""
    for key, _ in _keys(parent_id, ip):
        entries = _failures.get(key)
        if entries and now in entries:
            entries.remove(now)
        if not entries:
            _failures.pop(key, None)


def clear() -> None:
    """전부 비운다. 테스트용."""
    _failures.clear()
