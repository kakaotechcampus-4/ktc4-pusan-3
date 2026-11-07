"""W · SSE 프레임 → Trace 칸 (docs/eval/eval-v1.md §3-4).

W 는 앱을 API 로 돌려 `GET /runs/{rid}/events` 의 글자를 읽는다. 이 파일은 DB 도 모델도 모르는
순수 함수만 둔다 — 파서와 칸 채우기는 단위 테스트로 고정한다.

프레임 모양은 `app/api/runs/translate.py` · `sse.py` 가 정한다(프론트 `lib/api/sse.ts` 와 같다).
SSE 에는 의도(intent) · 라우팅이 직접 오지 않는다. 그래서 화면에 나간 것으로 거꾸로 읽는다.
- 도메인 Agent(food · activity · growth · health)의 `agent_result` · `unavailable` → route
- `saved` 가 있고 route 가 있으면 mixed, 저장만 있으면 record, route 만 있으면 request
"""

import json
from dataclasses import dataclass, field
from typing import Any

from tests.eval.suite.trace import Trace

DOMAIN_AGENTS = ("food", "activity", "growth", "health")
Frame = tuple[str, dict[str, Any]]


def parse_sse(text: str) -> list[Frame]:
    """SSE 본문 → [(event, data)]. `data:` 가 없는 프레임(주석 등)은 버린다. ping 은 남긴다."""
    frames: list[Frame] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        name = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        if not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {"_raw": "\n".join(data_lines)}
        frames.append((name, payload if isinstance(payload, dict) else {"_value": payload}))
    return frames


@dataclass
class FrameInfo:
    """Trace 에 안 들어가지만 다음 단계가 쓰는 값."""

    drafts: list[dict[str, Any]] = field(default_factory=list)  # event_draft 프레임의 초안들
    awaits_reply: bool = False  # 답을 기다리는 되묻기가 있었다
    suggestion_ids: list[str] = field(default_factory=list)
    saved_ids: list[str] = field(default_factory=list)  # saved 프레임의 관찰 id (참고용)


def apply_frames(trace: Trace, frames: list[Frame]) -> FrameInfo:
    info = FrameInfo()
    names = tuple(name for name, _ in frames if name != "ping")
    trace.event_names = names
    route: list[str] = []
    guidance: list[str] = []
    questions: list[str] = []
    agent_texts: list[str] = []
    health_texts: list[str] = []
    saw_saved = False
    for name, data in frames:
        if name == "saved":
            saw_saved = True
            info.saved_ids += [
                str(o.get("id")) for o in data.get("observations", []) if o.get("id")
            ]
        elif name == "guidance":
            guidance.append(str(data.get("code", "")))
        elif name == "event_draft":
            info.drafts += list(data.get("drafts", []))
        elif name == "note":
            trace.memory_text = data.get("text")
            if data.get("kind") == "question" and data.get("text"):
                questions.append(str(data["text"]))
                info.awaits_reply = True
        elif name == "unavailable":
            route += [a for a in data.get("agents", []) if a not in route]
        elif name == "partial":
            trace.partial = True
        elif name == "failed":
            trace.failed = str(data.get("reason", "")) or "failed"
        elif name == "done":
            trace.model_calls = int(data.get("model_calls", 0) or 0)
        elif name == "agent_result":
            _agent_result(trace, data, info, route, questions, agent_texts, health_texts)
    trace.guidance = tuple(guidance)
    trace.questions = tuple(questions)
    trace.agent_texts = tuple(agent_texts)
    trace.health_texts = tuple(health_texts)
    trace.route = tuple(route)
    trace.event_drafts = len(info.drafts)
    request = bool(route)
    trace.intent = (
        "mixed"
        if saw_saved and request
        else "record"
        if saw_saved
        else "request"
        if request
        else None
    )
    return info


def _agent_result(
    trace: Trace,
    data: dict[str, Any],
    info: FrameInfo,
    route: list[str],
    questions: list[str],
    agent_texts: list[str],
    health_texts: list[str],
) -> None:
    agent = str(data.get("agent", ""))
    if data.get("question"):
        questions.append(str(data["question"]))
        info.awaits_reply = info.awaits_reply or bool(data.get("awaits_reply"))
        return
    if agent in DOMAIN_AGENTS and agent not in route:
        route.append(agent)
    trace.bundle_kinds[agent] = data.get("kind")
    trace.record_counts[agent] = data.get("record_count")
    info.suggestion_ids += [str(s) for s in data.get("suggestion_ids", [])]
    texts = [t for r in data.get("readouts", []) for t in (r.get("title"), r.get("body")) if t]
    (health_texts if agent == "health" else agent_texts).extend(texts)
