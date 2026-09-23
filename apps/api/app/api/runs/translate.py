"""Agent 이벤트 → 화면(SSE) 이벤트 번역 (#134 5단계).

pipeline 은 진행 상황을 파이썬 객체(`Step` · `Failed` …)로 내보내고, 화면은 정해진 이름의 SSE
글자만 알아듣는다. 화면이 아는 이름은 프론트 `apps/web/src/lib/api/sse.ts` 의 `RunEvent` 를 기준으로
본다 — HTML 계약서는 9/21 부터 갱신을 보류했고, SSE 는 스웨거로 그리기 어렵다.
⚠️ `event_draft` · `guidance` 는 아직 `RunEvent` 에 없다 (#141 에서 추가 중). 그 전까지 화면은
   모르는 이벤트로 보고 건너뛴다 — 깨지지는 않는다.

🚨 화면에 보낼 것만 번역하고, 나머지는 None(보내지 않음)이다.
   - `Saved` — 화면은 관찰 내용 전체(`observations`)를 원하는데 id 만 실려 온다. 8단계에서 행을
     읽어 채울 때까지 보내지 않는다.
   - `MemoryNote`(Memory 가 한 말 — 되묻는 질문 포함) · `Unavailable`("준비 중" 안내) — 화면에 둘
     자리를 아직 안 정했다 (docs/event/event-draft-flow-v1.md §6 의 `note`). 🚨 pipeline 은 이 둘만
     있어도 done 으로 끝나서, 정하기 전까지 화면에는 내용 없는 성공이 뜬다.
   - `FoodRouted` · `Unwritten` · `Rerouted` — 로그·지표용이다.
   pipeline 에 이벤트가 새로 생기면 tests/unit/api/test_run_translate.py 가 실패해서 정하라고 한다.

🚨 필드를 하나씩 옮겨 적는다 (`dataclasses.asdict` 로 통째로 넘기지 않는다). agents 가 이벤트에
   필드를 늘려도 화면으로 새어 나가지 않게 — 화면에 가는 모양은 이 파일이 정한다.

TODO(#145): 머지되면 agents import 를 `app.agents.entrypoint` 하나로 바꾼다
            — api 는 agents 진입점만 본다 (apps/api/CLAUDE.md 레이어 경계).
"""

import logging
import traceback

from app.agents.pipeline import Done, Emit, Event, EventDrafts, Failed, Step
from app.agents.supervisor.routing import Guidance
from app.api.runs import sse
from app.api.runs.registry import RunChannel

log = logging.getLogger(__name__)


def to_sse(event: Event) -> sse.SseEvent | None:
    """이벤트 하나를 화면용 (이름, 내용) 으로. 화면에 보낼 게 아니면 None."""
    if isinstance(event, Step):
        return "step", {"index": event.index, "total": event.total, "label": event.label}
    if isinstance(event, Failed):
        return sse.failed_event(event.reason, event.raw_text)
    if isinstance(event, Done):
        return "done", {"run_id": event.run_id, "model_calls": event.model_calls}
    if isinstance(event, Guidance):
        # deeplink 가 없어도 키는 남긴다 — 화면이 "키 없음" 과 "null" 을 따로 다루지 않게
        return "guidance", {
            "code": event.code,
            "message": event.message,
            "deeplink": event.deeplink,
        }
    if isinstance(event, EventDrafts):
        # 초안 모양은 AI 파트(drafts.py)가 정한다. 여기서 다시 적지 않는다
        return "event_draft", {"drafts": [draft.to_payload() for draft in event.drafts]}
    return None


def relay(channel: RunChannel) -> Emit:
    """pipeline 에 넘길 emit 을 만든다. 번역해서 채널에 넣는다.

    sync 다 — pipeline 은 emit 을 기다리지(await) 않고, 채널의 publish 도 sync 라 그대로 이어진다.
    끝 신호 뒤에 오는 것(pipeline 이 Failed 뒤에 보내는 Done 등)은 채널이 버린다.

    🚨 번역이 터져도 pipeline 을 멈추지 않는다. emit 은 pipeline 안에서 불려서, 여기서 예외가 나면
       Memory 가 이미 저장한 뒤에 run 이 실패로 끝난다 — 화면은 "아무것도 저장 안 했어요" 를 보고
       보호자가 다시 보내면 두 번 저장된다. 그 이벤트 하나만 빼고 로그를 남긴다.
    🚨 채널에 넣기 전에 SSE 글자로 한 번 바꿔 본다. JSON 으로 못 바뀌는 값을 넣으면 200 을 보낸 뒤
       스트림 중간에 연결이 끊기고, 다시 붙어도 같은 자리에서 또 끊긴다.
    """

    def emit(event: Event) -> None:
        try:
            translated = to_sse(event)
            if translated is None:
                return
            sse.frame(*translated)
        except Exception as exc:
            # 🚨 로그에 내용을 남기지 않는다 (루트 §2) — 이벤트 종류 · 예외 종류 · 코드 위치만
            log.error(
                "run %s 의 %s 를 화면 이벤트로 못 바꿔 건너뛴다 — %s\n%s",
                channel.run_id,
                type(event).__name__,
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            return
        channel.publish(translated)

    return emit
