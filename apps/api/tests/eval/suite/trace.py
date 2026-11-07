"""run 1건을 정규화한 기록 (docs/eval/eval-v1.md §3-1). 판정기는 이것만 본다.

P(진입점)와 W(API)가 같은 모양을 채운다. 실행 경로를 바꿔도 판정기는 그대로다.
"""

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

# 끝 이벤트를 어느 층에서 봤는가. pipeline 은 Failed 뒤에도 Done 이 나가고(Done 이 늘 마지막),
# SSE 는 done · failed 중 하나가 끝이다 (I12)
EventLevel = Literal["pipeline", "sse"]


@dataclass(frozen=True)
class CitationView:
    kind: str  # suggestion_evidence.source_kind
    id: str
    note: str
    child_record: bool  # Ref.is_child_record — 개인화 근거로 세는 행인가


@dataclass(frozen=True)
class SuggestionView:
    agent: str
    kind: str  # personalized · general
    content: str
    reason: str
    items: tuple[str, ...] = ()
    citations: tuple[CitationView, ...] = ()

    @property
    def child_citations(self) -> tuple[CitationView, ...]:
        return tuple(c for c in self.citations if c.child_record)


@dataclass(frozen=True)
class SavedRow:
    domain: str
    id: str
    observed_on: date


@dataclass
class Trace:
    case_id: str
    attempt: int
    run_id: str = ""
    about: str = "child"  # 마지막 턴의 주체. caregiver 면 I9
    event_level: EventLevel = "pipeline"
    intent: str | None = None
    route: tuple[str, ...] = ()  # 도메인 Agent 이름 (routing.domain_tasks)
    degraded: bool = False
    guidance: tuple[str, ...] = ()  # 나간 안내 코드
    saved: tuple[SavedRow, ...] = ()  # 이 run 이 새로 만든 아이 관찰
    suggestions: tuple[SuggestionView, ...] = ()
    bundle_kinds: dict[str, str | None] = field(default_factory=dict)  # 화면 묶음 종류(AgentResult)
    record_counts: dict[str, int | None] = field(default_factory=dict)
    searched: frozenset[str] | None = frozenset()  # 이 케이스에서 읽힌 아이 기록 id. None=안 모음
    stale_ids: frozenset[str] = frozenset()  # 6개월 지난 심은 기록 id
    event_drafts: int = 0
    stored_events: int = 0  # 이 run 이 저장소에 만든 일정 행 (승인 단계 제외)
    questions: tuple[str, ...] = ()  # 보호자에게 낸 되묻기 문장
    event_names: tuple[str, ...] = ()
    agent_texts: tuple[str, ...] = ()  # Health 밖 도메인 Agent 의 화면 문장
    health_texts: tuple[str, ...] = ()
    memory_text: str | None = None
    usage: tuple[dict[str, Any], ...] = ()
    model_calls: int = 0
    latency_ms: int = 0
    partial: bool = False
    failed: str | None = None
    logs: tuple[str, ...] = ()
    canaries: tuple[str, ...] = ()  # 로그에 있으면 안 되는 글자 (입력 원문 · 별명)
    error: str | None = None  # 끝까지 못 돈 이유 (하네스 예외 · 이어받을 되묻기 없음). 한 줄
    error_detail: str | None = None  # 예외였다면 traceback 전체 (결과 jsonl 에 남는다)

    def output_texts(self) -> tuple[str, ...]:
        texts: list[str] = [*self.agent_texts, *self.health_texts, *self.questions]
        if self.memory_text:
            texts.append(self.memory_text)
        for s in self.suggestions:
            texts += [s.content, s.reason, *s.items, *(c.note for c in s.citations)]
        return tuple(texts)

    @property
    def tokens(self) -> int:
        return sum(
            int(u.get("prompt_tokens", 0)) + int(u.get("completion_tokens", 0)) for u in self.usage
        )

    def to_json(self) -> dict[str, Any]:
        data = _plain(asdict(self))
        data.pop("logs")  # 로그는 결과 파일에 싣지 않는다 — 위반은 violation 으로 남는다
        return data


def _plain(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
