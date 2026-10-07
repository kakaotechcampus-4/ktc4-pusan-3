"""Agent 이벤트 → 화면(SSE) 이벤트 번역 (#134 5단계).

pipeline 은 진행 상황을 파이썬 객체(`Step` · `Failed` …)로 내보내고, 화면은 정해진 이름의 SSE
글자만 알아듣는다. 화면이 아는 이름은 프론트 `apps/web/src/lib/api/sse.ts` 의 `RunEvent` 를 기준으로
본다 — HTML 계약서는 9/21 부터 갱신을 보류했고, SSE 는 스웨거로 그리기 어렵다.

🚨 화면에 보낼 것만 번역하고, 나머지는 None(보내지 않음)이다.
   - `Saved` — 화면은 관찰 내용 전체(`observations`)를 원하는데 id 만 실려 온다. 8단계에서 행을
     읽어 채울 때까지 보내지 않는다.
     🚨 보내기 시작할 때도 기록 단계 commit 뒤여야 한다. pipeline은 commit 이 끝난 뒤에
        `Saved` 를 낸다. 여기서는 받은 순서대로 보내기만 하고, 미리 만들어 앞당겨 보내지 않는다.
        화면이 "저장됐어요"로 본 기록은 DB에 있어야 한다.
   - `DomainRouted` · `Unwritten` · `Rerouted` — 로그·지표용이다. 도메인 Agent 결과 중
     화면에 갈 것은 바로 뒤에 오는 `AgentResult`(agent_result, #227)가 싣는다.
   - `PendingReply` — 조각 원문이 들어 있다. API 의 pending store 로만 간다(runner.py).
   pipeline 에 이벤트가 새로 생기면 tests/unit/api/test_run_translate.py 가 실패해서 정하라고 한다.

🚨 필드를 하나씩 옮겨 적는다 (`dataclasses.asdict` 로 통째로 넘기지 않는다). agents 가 이벤트에
   필드를 늘려도 화면으로 새어 나가지 않게 — 화면에 가는 모양은 이 파일이 정한다.

agents 는 진입점(`app.agents.entrypoint`) 하나로만 본다 (apps/api/CLAUDE.md 레이어 경계).
"""

from app.agents.entrypoint import (
    AgentResult,
    Done,
    Emit,
    Event,
    EventDrafts,
    Failed,
    Guidance,
    MemoryNote,
    Partial,
    Step,
    Unavailable,
)
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
        # 초안 모양은 app/core/event_draft.py 가 정하고 to_payload 가 그 모델로 직렬화한다.
        # 여기서 다시 적지 않는다
        return "event_draft", {"drafts": [draft.to_payload() for draft in event.drafts]}
    if isinstance(event, MemoryNote):
        # kind=question 이면 화면이 "이어서 적기" 를 연다.
        return "note", {"text": event.text, "kind": event.kind}
    if isinstance(event, Unavailable):
        # "준비 중" 카드. 문구는 화면이 Agent 이름으로 만들고, 서버는 이름만 넘긴다
        return "unavailable", {"agents": list(event.agents)}
    if isinstance(event, AgentResult):
        # 도메인 Agent 결과 한 건 = 화면 블록 하나. 도착한 순서대로 그리고 재정렬하지 않는다 (#215)
        # readout의 source_refs는 싣지 않는다 — 근거 종류(child_growth_log · growth_doc …)가 화면
        # Ref에 아직 없어서, 보내면 타입이 안 맞는다 (apps/web/src/lib/api/types.ts 의 REF_KINDS)
        # question은 없어도 키를 남긴다 (guidance의 deeplink와 같은 이유)
        return "agent_result", {
            "agent": event.agent,
            "task_type": event.task_type,
            "status": event.status,
            "readouts": [
                {
                    "kind": readout.kind,
                    "title": readout.title,
                    "body": readout.body,
                    "authored_by": readout.authored_by,
                }
                for readout in event.readouts
            ],
            "question": event.question,
        }
    if isinstance(event, Partial):
        # 끝 신호가 아니다. 뒤에 done 이 오고, 화면은 그때 "부분 결과" 로 닫는다.
        # 같은 Agent 가 succeeded · failed 양쪽에 있을 수 있다(task 둘 중 하나만 실패)
        # 문구는 unavailable 처럼 화면이 Agent 이름으로 만든다. 서버 문구를 두면 정본이 두 곳이 된다
        return "partial", {
            "reason": event.reason,
            "succeeded": list(event.succeeded),
            "failed": list(event.failed),
        }
    return None


def relay(channel: RunChannel) -> Emit:
    """pipeline 에 넘길 emit 을 만든다. 번역해서 채널에 넣는다.

    sync 다 — pipeline 은 emit 을 기다리지(await) 않고, 채널의 publish 도 sync 라 그대로 이어진다.
    끝 신호 뒤에 오는 것(pipeline 이 Failed 뒤에 보내는 Done 등)은 채널이 버린다.

    🚨 번역이 터지면 그대로 올린다 — run 이 failed 로 끝나 화면에 "읽지 못했어요" 로 드러난다.
       번역 실패는 코드 버그(초안 모양과 번역기가 어긋남)라, 그 이벤트만 건너뛰면 화면에서 조용히
       빠져서 아무도 모른다. 올려도 데이터는 안전하다 — Memory 단계가 끝나면 commit 하고,
       commit 이후에 터져도 저장된 관찰은 되돌리지 않는다. commit 된 run 은 done 으로
       끝내고 키를 놓지 않아 다시 보내도 두 번 저장되지 않는다.
       로그는 러너가 남긴다(예외 종류 · 위치만, 원문 없이).
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
