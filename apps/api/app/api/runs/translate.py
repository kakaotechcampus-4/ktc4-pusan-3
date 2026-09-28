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

agents 는 진입점(`app.agents.entrypoint`) 하나로만 본다 (apps/api/CLAUDE.md 레이어 경계).
"""

from app.agents.entrypoint import Done, Emit, Event, EventDrafts, Failed, Guidance, Step
from app.api.runs import sse
from app.api.runs.registry import RunChannel


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

    🚨 번역이 터지면 그대로 올린다 — run 이 failed 로 끝나 화면에 "읽지 못했어요" 로 드러난다.
       번역 실패는 코드 버그(초안 모양과 번역기가 어긋남)라, 그 이벤트만 건너뛰면 화면에서 조용히
       빠져서 아무도 모른다. 올려도 데이터는 안전하다 — 러너 세션은 done 일 때만 확정하고 나머지는
       되돌려서(7단계) failed 는 언제나 "저장 없음" 이고, 키가 풀려 다시 보내도 두 번 저장되지
       않는다. 로그는 러너가 남긴다(예외 종류 · 위치만, 원문 없이).
    🚨 채널에 넣기 전에 SSE 글자로 한 번 바꿔 본다. JSON 으로 못 바뀌는 값이 채널에 들어가면 200 을
       보낸 뒤 스트림 중간에 끊기고, 다시 붙어도 같은 자리에서 또 끊긴다 — 넣기 전에 터뜨린다.
    """

    def emit(event: Event) -> None:
        translated = to_sse(event)
        if translated is None:
            return
        sse.frame(*translated)  # JSON 으로 못 바뀌면 여기서 터진다 — 채널에 넣기 전에
        channel.publish(translated)

    return emit
