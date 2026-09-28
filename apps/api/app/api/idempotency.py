"""Idempotency-Key 기억 — 프로세스 메모리.

계약서 §01 은 "되돌릴 수 없는 POST 는 헤더가 없으면 400" 까지만 정한다.
docs/api/idempotency-v1.md 가 그 위에 제안한 동작 중 **"같은 키 · 같은 보호자 → 처음 응답 재생"**
까지만 여기서 한다.
프론트 mutation 은 retry: false 라 중복은 보호자가 버튼을 다시 누를 때만 생기고, 그때 재생이
없으면 같은 한 줄이 관찰 N건씩 두 번 저장된다.

- 스코프는 (parent_id, method, path, key) — 보호자 사이에 키가 섞이지 않는다 (§3-5)
- 422(같은 키 · 다른 본문) · 409(처리 중) · 24시간 보관은 다음 이슈. 409 는 접수가 즉시 끝나는
  구조라 창이 몇 ms 뿐이고, 키를 **러너를 띄우기 전에** 적어 두면 그 창도 재생으로 흡수된다

🚨 프로세스 메모리라 서버를 껐다 켜면 사라지고, --workers 2 이상이면 프로세스마다 따로다.
   run 채널(runs/registry.py)과 같은 전제다 (#134 — 데모는 단일 프로세스).
"""

from uuid import UUID

_seen: dict[tuple[UUID, str, str, str], str] = {}


def recall(*, parent_id: UUID, method: str, path: str, key: str) -> str | None:
    """이 보호자가 이 창구에 이 키로 보낸 적이 있으면 그때의 run_id."""
    return _seen.get((parent_id, method, path, key))


def remember(*, parent_id: UUID, method: str, path: str, key: str, run_id: str) -> None:
    _seen[(parent_id, method, path, key)] = run_id


def forget_run(run_id: str) -> None:
    """이 run 을 가리키는 키를 지운다. 실패로 끝난 run 에 쓴다.

    기억하는 것은 성공뿐이다 (§3-4). 실패한 run 이 남아 있으면 같은 키로 "다시 시도" 할 때마다
    이미 닫힌 실패가 재생돼 재시도가 영원히 막힌다.
    """
    for scope in [scope for scope, seen in _seen.items() if seen == run_id]:
        del _seen[scope]


def clear() -> None:
    """전부 비운다. 테스트용."""
    _seen.clear()
