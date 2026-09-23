"""백그라운드 러너 — 접수 창구가 띄우고 잊는 쪽.

`POST inputs` 는 Agent 를 기다리지 않는다. `start()` 가 태스크를 만들어 채널에 매달고 바로 돌아오면,
창구는 202 를 내보내고 화면은 `GET events` 로 붙는다.

🚨 껍데기의 약속 — **무슨 일이 있어도 채널은 닫힌다.** job 이 예외로 죽으면 `failed` 를
   내보내고 닫는다. 안 닫히면 화면이 20초 뒤 "결과를 받지 못했어요"(unconfirmed) 로 떨어지고,
   그때 보호자는 무엇이 저장됐는지 모른다.

🚨 끝 신호는 `done` 또는 `failed` **하나**다. 둘 다 보내지 않는다 — 목(handlers/runs.ts)도
   failed 에서 끝나고, 화면의 `case "done"` 은 상태를 성공으로 덮는다. 끝 신호 뒤에 오는 것은
   채널(registry.RunChannel.publish)이 버린다.

🚨 태스크를 채널에 매단다. asyncio 는 태스크를 약하게만 잡고 있어서, 참조를 지역 변수로만 두면
   GC 가 도중에 거둬 run 이 조용히 사라진다.

5단계부터 접수 창구는 `agent_job` 으로 진짜 Agent 를 부른다. `fake_job` 은 LLM 없이 흐름만 볼 때
(테스트) 쓴다. 껍데기(`start` · `_guarded`)는 둘 다 같다.
"""

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable
from uuid import UUID

from app.agents import entrypoint
from app.api import idempotency
from app.api.runs import sse, translate
from app.api.runs.registry import RunChannel

log = logging.getLogger(__name__)

Job = Callable[[RunChannel], Awaitable[None]]

DEMO_STEP_DELAY = 0.4
"""가짜 러너의 단계 사이 간격(초). 브라우저에서 진행 오버레이가 움직이는 게 보이라고 둔 값이다."""

STEP_LABELS = ("적어주신 말을 읽고 있어요", "관찰을 나누고 있어요", "정리하고 있어요")


def agent_job(*, child_id: UUID, parent_id: UUID, raw_text: str) -> Job:
    """진짜 Agent(entrypoint.handle_input)를 돌리는 job 을 만든다. 진행 이벤트는 번역해서 채널로.

    - 저장소는 넘기지 않는다 — 지금은 run 마다 메모리 저장소다. DB 저장은 7단계.
    - 생일도 아직 안 넘긴다 — 아이 정보를 읽는 9단계에서. 그 전까지 식이 단계는 진입점의 기본값.
    - Agent 가 스스로 낸 실패(Failed)는 예외가 아니라 이벤트로 온다. 뒤따르는 Done 은 채널이 버린다.

    🚨 `entrypoint.handle_input` 을 모듈 이름으로 부른다 — 테스트가 진입점을 바꿔 끼울 수 있게.
    """

    async def job(channel: RunChannel) -> None:
        await entrypoint.handle_input(
            child_id=child_id,
            parent_id=parent_id,
            raw_text=raw_text,
            run_id=channel.run_id,
            emit=translate.relay(channel),
        )

    return job


async def fake_job(channel: RunChannel, *, step_delay: float | None = None) -> None:
    """step 1/3 → 2/3 → 3/3 → done. 저장은 하지 않는다.

    LLM 없이 흐름만 볼 때 쓴다 — 테스트가 agent_job 대신 이걸 끼운다.
    """
    delay = DEMO_STEP_DELAY if step_delay is None else step_delay
    total = len(STEP_LABELS)
    for index, label in enumerate(STEP_LABELS, start=1):
        channel.publish(("step", {"index": index, "total": total, "label": label}))
        await asyncio.sleep(delay)
    channel.publish(("done", {"run_id": channel.run_id, "model_calls": 0}))


async def _guarded(channel: RunChannel, job: Job, raw_text: str) -> None:
    try:
        await job(channel)
    except Exception as exc:
        # 🚨 로그에 원문을 남기지 않는다 (루트 §2). log.exception 은 예외 메시지까지 찍는데,
        #    거기에 입력 문장이 섞이기 쉽다(pydantic 검증 에러의 input_value 등).
        #    그래서 예외 종류와 코드 위치(format_tb — 메시지 없이 파일·줄·코드만)만 남긴다.
        log.error(
            "run %s 의 job 이 %s 로 끝났다\n%s",
            channel.run_id,
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        # failed 가 끝 신호다. done 을 붙이지 않는다 — 화면의 `case "done"` 이 성공으로 덮는다.
        # 이미 끝 신호가 나갔으면(done 뒤 결과 기록에서 터진 경우) 덧붙이지 않는다 — 화면은 이미
        # 결과를 받았다. 채널도 버리지만, 읽는 사람이 헷갈리지 않게 여기서도 적어 둔다.
        if channel.ended_with is None:
            channel.publish(sse.failed_event("internal_error", raw_text))
    finally:
        # 실패로 끝난 run 만 키를 놓아준다 — 같은 키로 "다시 시도" 하면 새 run 이 떠야 한다.
        # 🚨 done 으로 끝난 run 은 저장이 끝났다. 뒤에서 터졌어도 놓으면 재시도가 두 번 저장한다.
        # 🚨 publish 와 여기 사이에 await 가 없어야 한다. 화면이 failed 를 받자마자 재시도해도
        #    키가 이미 지워져 있다.
        if channel.ended_with == "failed":
            idempotency.forget_run(channel.run_id)
        channel.close()


def start(channel: RunChannel, job: Job, *, raw_text: str) -> asyncio.Task[None]:
    """job 을 뒤에서 돌리기 시작하고 바로 돌아온다. 기다리지 않는다."""
    task = asyncio.create_task(_guarded(channel, job, raw_text), name=f"run:{channel.run_id}")
    channel.task = task
    return task
