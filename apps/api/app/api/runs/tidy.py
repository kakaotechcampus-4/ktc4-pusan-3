"""정리 한 걸음 — 보관함의 "사용 중" 풀기 · 지우기, 한도 돌려주기가 실패해도 원래 결과를 덮지 않는다
(#899 리뷰 · #877 적대 검증).

메모리 보관함일 때는 이 정리가 실패할 수 없었다. 표로 옮기면서 DB 호출이 됐고, 실패를 그대로
올리면 429 가 500 이 되거나 끝난 run 이 터진 것처럼 보인다. 남은 행은 만료 뒤 `hold_purge` 가
지운다. 로그에는 예외 종류만 남긴다.
"""

import logging
from collections.abc import Awaitable

log = logging.getLogger(__name__)


async def tidy(step: Awaitable[object], what: str, ref: object) -> None:
    """`ref` 는 로그에서 무엇의 정리였는지 가리키는 id(run_id · parent_id).

    원문을 넘기지 않는다.
    """
    try:
        await step
    except Exception as exc:
        log.warning("%s 를 하지 못했다 (%s) — %s", what, ref, type(exc).__name__)
