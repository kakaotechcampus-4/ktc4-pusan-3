"""백그라운드 러너 — 접수 창구가 띄우고 잊는 쪽.

`POST inputs` 는 Agent 를 기다리지 않는다. `start()` 가 태스크를 만들어 채널에 매달고 바로 돌아오면,
창구는 202 를 내보내고 화면은 `GET events` 로 붙는다.

🚨 껍데기의 약속 — **무슨 일이 있어도 채널은 닫힌다.** job 이 예외로 죽으면 `failed` 와 `done` 을
   내보내고 닫는다. 안 닫히면 화면이 20초 뒤 "결과를 받지 못했어요"(unconfirmed) 로 떨어지고,
   그때 보호자는 무엇이 저장됐는지 모른다.

🚨 태스크를 채널에 매단다. asyncio 는 태스크를 약하게만 잡고 있어서, 참조를 지역 변수로만 두면
   GC 가 도중에 거둬 run 이 조용히 사라진다.

3단계는 가짜 job(`fake_job`)이다. 5단계에서 pipeline.handle_input 으로 바뀌고 껍데기는 그대로
남는다.
"""

import asyncio
import logging
import traceback
from collections.abc import Awaitable, Callable

from app.api import idempotency
from app.api.runs.registry import RunChannel

log = logging.getLogger(__name__)

Job = Callable[[RunChannel], Awaitable[None]]

DEMO_STEP_DELAY = 0.4
"""가짜 러너의 단계 사이 간격(초). 브라우저에서 진행 오버레이가 움직이는 게 보이라고 둔 값이다."""

STEP_LABELS = ("적어주신 말을 읽고 있어요", "관찰을 나누고 있어요", "정리하고 있어요")


async def fake_job(channel: RunChannel, *, step_delay: float | None = None) -> None:
    """step 1/3 → 2/3 → 3/3 → done. 저장은 하지 않는다."""
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
        channel.publish(("failed", {"reason": "internal_error", "raw_text": raw_text}))
        channel.publish(("done", {"run_id": channel.run_id, "model_calls": 0}))
    finally:
        # 실패로 끝난 run 은 키를 놓아준다 — 같은 키로 "다시 시도" 하면 새 run 이 떠야 한다.
        # 🚨 publish 와 여기 사이에 await 가 없어야 한다. 화면이 failed 를 받자마자 재시도해도
        #    키가 이미 지워져 있다.
        if any(name == "failed" for name, _ in channel.events):
            idempotency.forget_run(channel.run_id)
        channel.close()


def start(channel: RunChannel, job: Job, *, raw_text: str) -> asyncio.Task[None]:
    """job 을 뒤에서 돌리기 시작하고 바로 돌아온다. 기다리지 않는다."""
    task = asyncio.create_task(_guarded(channel, job, raw_text), name=f"run:{channel.run_id}")
    channel.task = task
    return task
