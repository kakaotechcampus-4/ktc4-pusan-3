"""LLM 사용량 기록 — 호출 1번을 보호자 · run · 모델에 묶어 쓰기 큐에 넣는다 (BM 산정)

LLM 을 부르는 곳(Agent · Curator · 식단 OCR)은 run 도 보호자도 모른다. 창구가 처리 단위를 시작할 때
`attribute()` 로 귀속을 묶어 두고, 부르는 곳은 `record()` 만 부른다.

- 귀속은 contextvar 다. asyncio 태스크와 `asyncio.to_thread` 는 만들 때 context 를 복사해서
  Agent 병렬 실행 · Curator 백그라운드 · OCR 스레드가 같은 귀속을 본다.
  🚨 `loop.run_in_executor` 는 복사하지 않는다 — 이 길로 부르면 귀속이 없어 저장되지 않고
  WARNING 만 남는다.
- `record()` 는 동기이고 바로 돌아온다. DB 쓰기는 api 의 writer 가 `drain()` 으로 모아서 한다.
  동기로 둔 이유는 식단 OCR 이 동기 클라이언트라 스레드에서 돌기 때문이다.
- writer 가 `enable()` 하기 전에는 아무것도 하지 않는다 — 테스트 · eval · 스크립트는
  큐도 경고도 없다.
- 🚨 서버(writer 켜짐)에서 귀속 없이 부르면 저장하지 않고 WARNING 이다. 보호자 없이 쓴 토큰이
  생겼다는 뜻이라 운영에서는 0건이 정상이다. 새 LLM 창구를 만들면 그 창구에서 `attribute()` 를
  건다.
- 🚨 원문 · 프롬프트 · 응답 · tool 인자를 받지 않는다. 남기는 것은 id · 이름 · 숫자뿐이다 (루트 §2).
- 🚨 기록 때문에 호출이 실패하면 안 된다. `record()` 는 예외를 밖으로 내지 않는다.
- 🚨 한 행 때문에 같은 묶음의 500행이 실패하면 안 된다. 그래서 큐에 들이기 전에 걸러낸다 —
  숫자는 int4 범위로(넘으면 0), 글자는 NUL 을 지우고, `Source` 밖의 창구 값은 저장하지 않고 WARNING.
- writer 가 못 쓴 묶음(DB 가 잠깐 아플 때 · 멈추는 중 취소)은 `requeue()` 로 큐 뒤에 되돌린다.
  상한(`MAX_PENDING`)을 넘는 만큼만 버린다.
"""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Empty, SimpleQueue
from typing import Literal, get_args
from urllib.parse import urlsplit
from uuid import UUID

logger = logging.getLogger(__name__)

Source = Literal["input", "suggest", "photo", "delete"]
"""어느 창구에서 시작된 처리인가. 모델이 아니라 창구다 — 모델 구분은 `role` 이 한다.

- `input` 입력 run (이어받기 포함)
- `suggest` 05 다시 만들기
- `photo` 사진 run — 공지 읽기와 식단표 읽기가 한 장에서 같이 불린다 (#710 PM 코멘트)
- `delete` 기록 삭제 · 대화 삭제 확정 뒤에 뜨는 Curator (run 밖이다)
"""

MAX_PENDING = 10_000
"""writer 가 못 비울 때(DB 장애) 프로세스 메모리가 끝없이 자라지 않게 하는 상한."""

UNKNOWN_PROVIDER = "unknown"

_SOURCES = frozenset(get_args(Source))
"""DB 의 CHECK(`ck_llm_usage_source`)와 같은 값. 밖의 값은 큐에 들이지 않는다."""

_INT4_MAX = 2**31 - 1
"""표의 정수 칸(int4)이 받는 최댓값. 넘는 숫자는 한 행이 묶음 전체를 실패시킨다."""


@dataclass(frozen=True)
class Attribution:
    parent_id: UUID
    run_id: str
    source: Source


@dataclass(frozen=True)
class UsageRecord:
    parent_id: UUID
    run_id: str
    source: Source
    role: str
    provider: str
    model: str
    prompt_tokens: int  # 캐시 포함 입력 전체. 임베딩은 total_tokens
    cached_prompt_tokens: int  # prompt_tokens 에 이미 포함된 값
    completion_tokens: int
    reasoning_tokens: int  # completion_tokens 에 이미 포함된 값 — 더하면 두 번 센다
    latency_ms: int
    occurred_at: datetime


_current: ContextVar[Attribution | None] = ContextVar("llm_usage_attribution", default=None)
_queue: SimpleQueue[UsageRecord] = SimpleQueue()
_enabled = False


@contextmanager
def attribute(*, parent_id: UUID, run_id: str, source: Source) -> Iterator[None]:
    """이 블록 안에서 일어나는 LLM 호출을 이 보호자 · run 에 묶는다."""
    token = _current.set(Attribution(parent_id=parent_id, run_id=run_id, source=source))
    try:
        yield
    finally:
        _current.reset(token)


def current() -> Attribution | None:
    return _current.get()


def enable() -> None:
    """writer 가 돌기 시작할 때 부른다. 이 뒤부터 record 가 큐에 넣는다."""
    global _enabled
    _enabled = True


def disable() -> None:
    global _enabled
    _enabled = False


def drain(limit: int = 500) -> list[UsageRecord]:
    """큐에서 최대 limit 건을 꺼낸다. writer 가 한 번에 INSERT 할 묶음이다."""
    rows: list[UsageRecord] = []
    while len(rows) < limit:
        try:
            rows.append(_queue.get_nowait())
        except Empty:
            break
    return rows


def requeue(records: Sequence[UsageRecord]) -> int:
    """writer 가 못 쓴 묶음을 큐 뒤에 되돌린다 — DB 가 잠깐 아픈 때 기록이 같이 사라지지 않게.

    순서는 상관없다(`occurred_at` 이 기록에 있다). 큐가 `MAX_PENDING` 에 닿으면 넘치는 만큼은 버리고
    WARNING 한 줄을 남긴다. 되돌린 건수를 돌려준다.
    writer 가 멈추는 중(`disable()` 뒤)에도 되돌린다.
    """
    queued = 0
    for record_ in records:
        if _queue.qsize() >= MAX_PENDING:
            break
        _queue.put(record_)
        queued += 1
    if queued < len(records):
        logger.warning(
            "llm usage 큐가 %d건이라 되돌리지 못하고 %d건을 버렸다",
            MAX_PENDING,
            len(records) - queued,
        )
    return queued


def provider_of(base_url: str | None) -> str:
    """주소에서 host 만 뽑는다. 같은 모델 이름도 게이트웨이마다 단가가 달라서 남긴다.

    🚨 키 · 계정 · 경로 · 쿼리는 싣지 않는다 — 주소에 키가 들어 있는 제공처가 있다.
    """
    if not base_url:
        return UNKNOWN_PROVIDER
    try:
        return urlsplit(base_url).hostname or UNKNOWN_PROVIDER
    except ValueError:
        return UNKNOWN_PROVIDER


def _count(value: object) -> int:
    """제공처가 준 숫자를 정수로. None · 문자열 · 음수 · int4 를 넘는 값은 0 이다.

    나중 INSERT 가 한 행 때문에 묶음째 실패하지 않게 큐에는 표가 받는 정수만 넣는다.
    """
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _INT4_MAX:
        return value
    return 0


def _text(value: object) -> str:
    """글자 칸에 넣을 값. NUL 은 지우고, 글자가 아니면 `unknown` 이다.

    Postgres 는 NUL 이 든 글자를 거부하고 NOT NULL 칸에 None 도 못 넣는다 — 둘 다 같은 묶음의 다른
    행까지 실패시킨다. 값은 우리 설정 · 응답의 모델 이름이라 지우는 것만으로 충분하다.
    """
    if not isinstance(value, str):
        return UNKNOWN_PROVIDER
    return value.replace("\x00", "")


def record(
    *,
    role: str,
    provider: str,
    model: str,
    prompt_tokens: int | None,
    cached_prompt_tokens: int | None = 0,
    completion_tokens: int | None = 0,
    reasoning_tokens: int | None = 0,
    latency_ms: int | None = 0,
) -> None:
    """토큰 값은 응답에서 읽은 그대로 넘긴다. 숫자인지 거르는 일은 여기서 한다.

    호출 쪽에서 `int()` 로 감싸다 이미 성공한 호출이 터지는 일을 막으려는 것이다.
    """
    try:
        if not _enabled:
            return
        attribution = _current.get()
        if attribution is None:
            logger.warning(
                "llm usage 귀속 없음 role=%s model=%s prompt_tokens=%s completion_tokens=%s",
                role,
                model,
                prompt_tokens,
                completion_tokens,
            )
            return
        if attribution.source not in _SOURCES:
            # CHECK 에 걸릴 행이다 — 큐에 들이면 같은 묶음의 다른 행까지 날아간다. source 는 코드가
            # 정한 글자(Literal)라 로그에 남겨도 된다
            logger.warning(
                "llm usage 창구 값이 Source 밖이라 저장하지 않는다 source=%s role=%s",
                attribution.source,
                role,
            )
            return
        if _queue.qsize() >= MAX_PENDING:
            logger.warning("llm usage 큐가 %d건이라 버렸다 role=%s", MAX_PENDING, role)
            return
        _queue.put(
            UsageRecord(
                parent_id=attribution.parent_id,
                run_id=attribution.run_id,
                source=attribution.source,
                role=_text(role),
                provider=_text(provider),
                model=_text(model),
                prompt_tokens=_count(prompt_tokens),
                cached_prompt_tokens=_count(cached_prompt_tokens),
                completion_tokens=_count(completion_tokens),
                reasoning_tokens=_count(reasoning_tokens),
                latency_ms=_count(latency_ms),
                occurred_at=datetime.now(UTC),
            )
        )
    except Exception as exc:
        # 예외 메시지는 싣지 않는다 — 종류만 (원문이 섞일 수 있다)
        logger.warning("llm usage 기록 실패 (%s)", type(exc).__name__)
