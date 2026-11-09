"""백그라운드 러너 — 접수 창구가 띄우고 잊는 쪽.

`POST inputs` 는 Agent 를 기다리지 않는다. `start()` 가 태스크를 만들어 채널에 매달고 바로 돌아오면,
창구는 202 를 내보내고 화면은 `GET events` 로 붙는다.

🚨 껍데기의 약속 — **무슨 일이 있어도 끝 신호 하나를 내고 채널을 닫는다.** job 이 예외로 죽든,
   끝 신호 없이 돌아오든, 시간 안전망에 걸리든 `failed` 로 끝낸다. 안 그러면 화면이 "결과를 받지
   못했어요"(unconfirmed) 로 떨어지고, 그때 보호자는 무엇이 저장됐는지 모른다.

🚨 끝 신호는 `done` 또는 `failed` **하나**다. 둘 다 보내지 않는다 — 목(handlers/runs.ts)도
   failed 에서 끝나고, 화면의 `case "done"` 은 상태를 성공으로 덮는다. 끝 신호 뒤에 오는 것은
   채널(registry.RunChannel.publish)이 버린다.

🚨 태스크를 채널에 매단다. asyncio 는 태스크를 약하게만 잡고 있어서, 참조를 지역 변수로만 두면
   GC 가 도중에 거둬 run 이 조용히 사라진다.

5단계부터 접수 창구는 `agent_job` 으로 진짜 Agent 를 부른다. `fake_job` 은 LLM 없이 흐름만 볼 때
(테스트) 쓴다. 껍데기(`start` · `_guarded`)는 둘 다 같다.
"""

import asyncio
import contextlib
import logging
import traceback
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from app.agents import entrypoint
from app.api import agent_ports, idempotency
from app.api.deps.db import background_session
from app.api.runs import pending_reply, sse, suggestion_retry, translate
from app.api.runs.registry import RunChannel
from app.core import llm_usage
from app.core.logging_config import log_error_without_message
from app.domains.consent.location_log import record_location_use_if_granted
from app.domains.consent.models import LocationPurpose

log = logging.getLogger(__name__)

Job = Callable[[RunChannel], Awaitable[None]]
OnEnd = Callable[[RunChannel], Awaitable[None]]

DEMO_STEP_DELAY = 0.4
"""가짜 러너의 단계 사이 간격(초). 브라우저에서 진행 오버레이가 움직이는 게 보이라고 둔 값이다."""

STEP_LABELS = ("적어주신 말을 읽고 있어요", "관찰을 나누고 있어요", "정리하고 있어요")

RUN_DEADLINE_SECONDS = 60.0
"""run 하나의 안전망(초). 모델이 멈춘 run 만 끊는다 — 정상 run 은 3~5초이고, Memory 가 도구를 7번
다 써도 25초 안팎이다(09-23 실측). 서버가 안 끊으면 10초마다 나가는 ping 이 화면의 20초 타이머를
계속 되살려서 진행 화면이 몇 분씩 돈다.

⏰ 이건 §2 "20초 초과 시 부분 결과로 전환"(NF-06)이 아니다. 그건 pipeline 이 한다 —
   입력부터 20초(`agents/pipeline.py` RUN_DEADLINE_S) 중 Supervisor · Memory 가 쓰고 남은 시간만
   도메인 Agent 에 주고, 넘기면 그 Agent 를 끊어 `partial` 을 낸다.
   Supervisor · Memory 는 저장이 반만 남지 않게 끊지 않는다.
"""


def curator_trigger() -> entrypoint.CuratorTrigger | None:
    """Memory commit 직후 Curator 를 띄우는 콜백. 임베딩 키가 없으면 None — pipeline 이 건너뛴다.

    store · commit 과 같이 러너가 만든다. DB 세션을 여는 곳을 러너 한 곳에 모으려는 것이다 (#257).
    버튼으로 관찰을 지운 삭제 제안 확정(`routers/delete_proposals.py`, #535)도 이것을 쓴다.
    콜백은 별도 세션에서 백그라운드 task 로 돈다 — run 은 Curator 를 기다리지 않는다.
    """
    from app.domains.memory.curator.trigger import trigger_curator_background

    embedder, judge = entrypoint.curator_deps()
    if embedder is None:
        return None

    def trigger(child_id: UUID, today: date) -> None:
        trigger_curator_background(child_id, today, embedder, judge)

    return trigger


def trigger_curator_after_delete(
    child_id: UUID, today: date, *, parent_id: UUID, after: str
) -> None:
    """run 밖에서 지운 뒤(기록 삭제 · 삭제 확정) Curator 를 띄운다. commit 뒤에만 부른다.

    같은 트랜잭션에서 띄우면 Curator 실패가 삭제까지 되돌린다.
    🚨 터져도 응답은 성공이다 — 지운 것은 이미 commit 됐다 (pipeline._trigger_curator 와 같다).
    🚨 이 길은 run 밖이라 귀속이 없다. 여기서 건다 — 안 걸면 Curator 의 임베딩 · 판정 토큰이 보호자
       없이 쓰여 저장되지 않고 WARNING 만 남는다 (#710). Curator 는 trigger 가 띄우는 태스크라 이
       귀속을 복사한다. run 밖에서 Curator 를 띄우는 새 길은 이것을 부른다 — 따로 `attribute()` 를
       복사해 두지 않는다.
    `after` 는 로그에 남는 길 이름이다(고정 글귀 — 값을 넣지 않는다).
    """
    try:
        trigger = curator_trigger()
        if trigger is not None:
            with llm_usage.attribute(parent_id=parent_id, run_id=uuid4().hex, source="delete"):
                trigger(child_id, today)
    except Exception as exc:
        log.error("%s 뒤 curator trigger 실패 error=%s", after, type(exc).__name__)


def agent_job(
    *,
    child_id: UUID,
    parent_id: UUID,
    raw_text: str,
    birth_date: date | None = None,
    child_health_granted: bool | None = None,
    location: entrypoint.CoarseLocation | None = None,
    activity_weather: entrypoint.WeatherSource | None = None,
    activity_events: entrypoint.EventSource | None = None,
    health_places: entrypoint.FacilitySource | None = None,
    continuation: entrypoint.PendingMemoryContext | None = None,
    reply_to: str | None = None,
    on_event: Callable[[entrypoint.Event], None] | None = None,
) -> Job:
    """진짜 Agent(entrypoint.handle_input)를 돌리는 job 을 만든다. 진행 이벤트는 번역해서 채널로.

    - 저장소는 DB 세션으로 만든 DbMemoryStore 를 넘긴다. commit 은 session.commit 을 감싼다.
      async with 블록이 빠져나가면 commit 되지 않은 세션은 close(implicit rollback) 된다.
    - 도메인 Agent 의 기억 포트가 쓸 store 와 추천 저장 함수는 `agent_ports` 로 연다. 호출마다
      · Agent 결과 하나마다 짧은 세션이고, 저장 commit 이 돌아오면 `channel.suggestions_saved`
      를 세운다 — Memory commit 과 같이 확정된 저장이다. 일반 추천 블록의 기록 건수도 같은 곳에서
      연다(#227). Growth 의 성장 기록 어댑터도 같은 곳에서 연다 — 안 넘기면 성장 추이가 "측정
      0건" 으로 읽힌다. 도서 어댑터(`growth_book_ports`)도 같다 — 키가 없으면 넘기지 않아 도서가
      닫힌다.
    - Curator 트리거도 여기서 만든다(`curator_trigger`). 진입점은 받은 것을 pipeline 에
      넘기기만 한다.
    - 생일은 창구가 확인한 아이의 것을 넘긴다 (9단계). 식이 단계(12개월 경계)는 진입점이 계산한다.
      None 이면 진입점의 기본값을 쓴다.
    - `child_health_granted` 는 창구가 입력 한 번에 읽은 `child_health` 동의의 현재 값이다 (#372).
      그대로 진입점에 넘긴다 — 여기서 바꾸지 않는다. None 은 값이 없다는 뜻이고
      "동의 안 함"(False)이 아니다. 읽기에 실패하면 창구가 run 을 시작하지 않아서 이 job 이
      만들어지지 않는다.
    - `location` 은 창구가 위치정보 동의를 확인한 흐린 좌표, `activity_weather` 는 서버 시작 때 만든
      날씨 어댑터다(#380). 날씨 어댑터는 그대로 넘긴다. 좌표는 넘기기 직전에 쓴 사실을 남기고
      (`_hand_location`, #693), 그새 동의가 꺼졌거나 남기기에 실패하면 버린다. None 이면 Activity 는
      실내 전용이다.
      장소 어댑터는 `agent_ports.place_source` 로 연다 — 좌표가 없으면 진입점 쪽에서 닫힌다.
      `activity_events` 는 서버 시작 때 만든 행사 어댑터(TourAPI)다. 그대로 넘긴다 — None 이면
      Activity 는 행사를 찾지 않는다.
    - `health_places` 는 서버 시작 때 만든 병원 어댑터다(#713). 그대로 진입점에 넘긴다.
      🚨 `location` 은 Health 에 넘기지 않는다(`health_location` 을 안 넘긴다). 창구의 위치 동의
      확인이 약관의 판을 보지 않아서, 날씨 · 놀이 목적의 이전 판에만 동의한 보호자도 같은 좌표가
      온다. 병원 찾기 목적이 든 판(2026-11-03)을 확인하는 길이 생기면 그 값을 따로 받아 넘긴다.
    - Agent 가 스스로 낸 실패(Failed)는 예외가 아니라 이벤트로 온다. 뒤따르는 Done 은 채널이 버린다.
    - continuation 이 있으면 이전 되묻기를 이어받는 run 이다. Supervisor 를 타지 않는다.
    - 되묻기로 끝나면 PendingReply 가 이벤트로 온다. 화면에 보내지 않고 pending store 에 넣는다 —
      done 앞에 들어가야 화면이 note 를 받은 직후 보낸 답도 맥락을 찾는다.
    - 실패한 추천 맥락(RetryContext) · 0개 기록(EmptyResult)은 05 보관함(`suggestion_retry`)에
      넣는다 — PendingReply 처럼 화면 결과보다 먼저 온다.
    - 이어받기 run 이 끝나면 창구가 "사용 중" 으로 둔 reply_to 맥락을 정리한다(#564). done 이거나
      저장이 확정됐으면 지우고, 아니면(실패 · 시간 초과 · 예외) 표시만 푼다. failed 는 "저장 없음"
      이라 같은 답으로 다시 시도할 수 있어야 한다.
    - RUN_DEADLINE_SECONDS 를 넘기면 끊고 failed(timeout) 으로 끝낸다.
      끊는 순간 진행 중인 모델 호출도 취소된다.
    - `on_event` 는 이벤트를 번역한 뒤에 하나씩 받는다. 대화 기록(`chat_record`)이
      `Saved` 를 관찰 내용이 아니라 id 로 받으려고 여기서 넘긴다 (#228). 끝 신호 뒤의 것과 번역이
      터진 것은 넘기지 않는다 — 화면에 안 나간 것이다.

    - LLM 사용량(#710)은 이 job 이 보호자 · run 에 묶는다(`source="input"`).
      Curator 는 run 안에서 태스크로 떠서 귀속을 물려받으므로 run 이 끝난 뒤에 끝나도
      이 run 으로 남는다. 이어받기 run 도 같다.

    🚨 `entrypoint.handle_input` 을 모듈 이름으로 부른다 — 테스트가 진입점을 바꿔 끼울 수 있게.
    """

    async def run(channel: RunChannel) -> None:
        # commit 이 채우고 Saved 가 꺼내 쓴다 — 이 run 이 만든 관찰의 화면 모양 (#471)
        saved_rows: dict[tuple[str, str], Any] = {}
        # load_cards 가 채우고 FoundTabs · DeleteProposed 가 꺼내 쓴다 — 카드의 화면 모양 행 (#535)
        card_rows: dict[tuple[str, str], Any] = {}
        relay = translate.relay(channel, saved_rows=saved_rows, card_rows=card_rows)
        # 보관함 저장(되묻기 맥락 · 05 다시 시도, DB #877)이 도는 동안 나온 이벤트. 저장이 끝나면
        # 순서대로 내보낸다 — 질문 카드가 보이면 보호자가 바로 답할 수 있어서, 맥락이 없으면 그
        # 답이 400 이다. 05 "다시 시도" 버튼도 끝 신호 뒤에 열린다.
        # 🚨 끝 신호도 여기 기다린다. 따로 띄운 태스크에서만 내면 run() 이 먼저 끝나 _guarded 가
        #    끝 신호가 없다고 보고 failed 를 붙인다 — run() 이 handle_input 뒤에 저장을 기다린다
        held: list[entrypoint.Event] = []
        saving: asyncio.Task[None] | None = None

        async def save(store: Awaitable[object], what: str, after: asyncio.Task[None] | None):
            if after is not None:
                await after  # 저장이 여럿이면 앞 저장부터 — 마지막 저장이 잡아 둔 것을 낸다
            try:
                await store
            except Exception as exc:
                # 저장이 안 되면 그 질문에 답하거나 다시 시도할 때 400 · "이어서 할 수 없다" 일
                # 뿐이다. 이미 한 기록 · 추천을 failed 로 덮지 않는다.
                # 🚨 예외 메시지에 원문이 섞일 수 있어 종류만 남긴다 (루트 §2)
                log.error(
                    "run %s 의 %s 저장이 %s 로 실패했다", channel.run_id, what, type(exc).__name__
                )
            if asyncio.current_task() is saving:  # 마지막 저장이 끝났다
                flush_held()

        def flush_held() -> None:
            while held:
                deliver(held.pop(0))

        def keep(store: Awaitable[object], what: str) -> None:
            nonlocal saving
            saving = asyncio.create_task(save(store, what, saving))

        def emit(event: entrypoint.Event) -> None:
            if isinstance(event, entrypoint.PendingReply):
                keep(
                    pending_reply.put(
                        run_id=event.run_id,
                        parent_id=parent_id,
                        child_id=child_id,
                        context=event.context,
                    ),
                    "되묻기 맥락",
                )
                return
            if isinstance(event, entrypoint.RetryContext):
                # 원문 조각이 있다 — 화면 · 로그로 보내지 않고 05 보관함에만 둔다 (#249)
                keep(
                    suggestion_retry.put_failed(
                        run_id=event.run_id,
                        parent_id=parent_id,
                        child_id=child_id,
                        agent=event.agent,
                        task=suggestion_retry.RetryTask(
                            event.task_type,
                            event.request_texts,
                            event.sent_at,
                            original_texts=event.original_texts,
                            prior_texts=event.prior_texts,
                        ),
                    ),
                    "05 실패 맥락",
                )
                return
            if isinstance(event, entrypoint.EmptyResult):
                keep(
                    suggestion_retry.put_empty(
                        run_id=event.run_id,
                        parent_id=parent_id,
                        child_id=child_id,
                        agent=event.agent,
                        message=event.message,
                    ),
                    "05 0개 기록",
                )
                return
            if saving is not None and not saving.done():
                held.append(event)
                return
            deliver(event)

        def deliver(event: entrypoint.Event) -> None:
            # 끝 신호 뒤의 이벤트는 채널이 버린다 — 화면에 안 나간 것은 대화에도 남기지 않는다
            live = channel.ended_with is None
            sent = relay(event)
            # 번역이 끝난 뒤에 넘긴다 — 번역이 터진 이벤트는 화면에 안 나갔다. 위에서 돌아간
            # 셋(PendingReply 등)은 대화에도 남기지 않는 것이라 여기까지 오지 않아도 된다
            # 조회 카드는 행을 못 읽어 안 나갔으면 남기지 않는다 — 안 그러면 보지 못한 카드가 지난
            # 대화에만 생긴다 (#535). 삭제 카드는 늘 나간다
            if isinstance(event, entrypoint.FoundTabs) and not sent:
                return
            if on_event is not None and live:
                on_event(event)

        def mark_saved() -> None:
            # commit 이 돌아온 뒤에만 표시한다. 결과를 모르면 표시가 없어 failed 다(결정 4)
            channel.suggestions_saved = True

        async with background_session() as session:
            from app.domains.memory.store.db_store import DbMemoryStore

            store = DbMemoryStore(session, child_id=child_id)

            async def commit() -> None:
                # 고친 관찰의 기억은 여기서 한꺼번에 계산한다 — 정정과 잠금 순서가 안 섞이게 (#717)
                await store.flush_profiles()
                await session.commit()
                if store.wrote:
                    channel.memory_committed = True
                # pipeline 은 이 commit 뒤에 Saved 를 낸다 — 그때 화면에 실을 행을 읽어 둔다
                saved_rows.update(await _read_saved(session, child_id, store.created, channel))

            async def load_cards(refs: tuple[entrypoint.Ref, ...]) -> None:
                card_rows.update(await _read_cards(session, child_id, parent_id, refs, channel))
                # 삭제 카드의 expires_at. Memory commit 뒤에 불리므로 제안은 이미 저장돼 있다
                if store.proposal_id is not None:
                    key = (translate.PROPOSAL_KIND, str(store.proposal_id))
                    card_rows[key] = store.proposal_expires_at

            try:
                async with asyncio.timeout(RUN_DEADLINE_SECONDS):
                    handed_location = await _hand_location(session, parent_id, location)
                    await entrypoint.handle_input(
                        child_id=child_id,
                        parent_id=parent_id,
                        raw_text=raw_text,
                        birth_date=birth_date,
                        child_health_granted=child_health_granted,
                        location=handed_location,
                        activity_weather=activity_weather,
                        activity_places=agent_ports.place_source(),
                        activity_events=activity_events,
                        health_places=health_places,
                        run_id=channel.run_id,
                        emit=emit,
                        continuation=continuation,
                        store=store,
                        commit=commit,
                        save_suggestions=agent_ports.suggestion_saver(
                            child_id, on_commit=mark_saved
                        ),
                        count_records=agent_ports.record_counter(child_id),
                        load_cards=load_cards,
                        recent_turns=agent_ports.recent_turns_loader(parent_id, child_id),
                        curator_trigger=curator_trigger(),
                        domain_store=agent_ports.domain_store_opener(child_id),
                        **agent_ports.food_stores(),
                        **agent_ports.safety_readers(),
                        **agent_ports.growth_book_ports(),
                        growth_log=agent_ports.growth_log_reader(),
                        affinity_reader=agent_ports.affinity_reader(),
                        **agent_ports.health_ports(),
                    )
                    if saving is not None:
                        # shield — 시간 제한이 저장까지 취소하면 맥락도 잡아 둔 이벤트도 잃는다
                        await asyncio.shield(saving)
            except TimeoutError:
                log.warning(
                    "run %s 가 %s초 안에 끝나지 않아 끊었다", channel.run_id, RUN_DEADLINE_SECONDS
                )
                # 저장을 기다리다 끊겼으면 잡아 둔 것을 끝 신호보다 먼저 낸다.
                # 맥락은 shield 덕에 뒤에서 마저 저장된다
                flush_held()
                if channel.committed:
                    done = (
                        "done",
                        {"run_id": channel.run_id, "model_calls": 0, "completed": False},
                    )
                    channel.publish(done)
                else:
                    channel.publish(sse.failed_event("timeout", raw_text))
            finally:
                # 예외로 죽으면 failed 는 _guarded 가 이 뒤에 붙인다 — 그 전에 잡아 둔 것을 낸다
                flush_held()
                # 창구가 "사용 중" 으로 둔 맥락을 정리한다 (#564). 저장 없이 끝났으면 표시만 풀어
                # 같은 질문에 다시 답할 수 있게 한다 — 15분은 처음 넣은 시각 그대로 센다.
                if continuation is not None and reply_to is not None:
                    if channel.ended_with == "done" or channel.committed:
                        await pending_reply.drop(reply_to)
                    else:
                        await pending_reply.release(reply_to)

    async def job(channel: RunChannel) -> None:
        with llm_usage.attribute(parent_id=parent_id, run_id=channel.run_id, source="input"):
            await run(channel)

    return job


async def _hand_location(
    session: Any, parent_id: UUID, location: entrypoint.CoarseLocation | None
) -> entrypoint.CoarseLocation | None:
    """좌표를 Agent 에 넘기기 직전에 쓴 사실을 남기고, 넘길 좌표를 돌려준다 (위치정보법 제16조 ②).

    여기서 남기는 이유 — 창구에서 남기면 하루 한도 · 되묻기 맥락 없음 · 키 기억 실패로 run 이
    뜨지 않은 요청도 "썼다" 로 남는다 (#693 리뷰).
    - 창구가 동의를 읽은 뒤 철회됐으면 None 이다. 좌표를 버리고 실내 전용으로 돈다.
    - 🚨 남기기가 실패하면 좌표를 버리고(None) run 은 계속 돈다 — 쓰지 않았으니 기록 없는 사용이
      아니다. run 을 실패시키면 보호자가 적은 기록까지 위치 표 고장 때문에 사라진다 (#697 리뷰).
      고장은 ERROR 로 남겨 알림이 가게 한다. 커밋은 됐는데 예외가 난 경우는 "기록은 있고 안 씀"
      이라 해가 없다.
    - 바로 커밋해서 보호자 행 잠금을 놓는다. 이 뒤의 Memory 저장과 묶지 않는다. run 세션의 첫
      문장이라 실패 뒤 rollback 이 함께 버릴 다른 작업은 없다.
    """
    if location is None:
        return None
    try:
        granted = await record_location_use_if_granted(
            session, parent_id=parent_id, purpose=LocationPurpose.SUGGESTION
        )
        await session.commit()
    except Exception as exc:
        # 연결이 끊겼으면 rollback 도 예외를 낸다. 그래도 run 은 계속 돈다 (#697 리뷰)
        with contextlib.suppress(Exception):
            await session.rollback()
        log_error_without_message(log, "위치 이용 기록 실패 — 좌표 없이 계속", exc)
        return None
    return location if granted else None


async def _read_saved(
    session: Any, child_id: UUID, created: list[tuple[str, UUID]], channel: RunChannel
) -> dict[tuple[str, str], Any]:
    """commit 된 관찰을 다시 읽어 07 기록 탭과 같은 모양으로 (#471).

    키는 `Saved.refs` 의 (kind, id)다. 같은 run 에서 지운 관찰은 읽히지 않아 값이 None 이다 —
    번역기가 "지워서 없음" 과 "키가 어긋나 없음" 을 가른다.
    🚨 읽기가 실패해도 run 을 실패로 만들지 않는다 — 저장은 이미 확정됐다. failed 로 끝내면 오히려
       "저장 안 됐다" 로 보인다. `saved` 만 빠지고 ERROR 로그를 남긴다(원문 없이, 루트 §2).
    🚨 `affinity` 는 Curator 가 묶기 전(commit 직후)이라 보통 null 이다 — 04 화면은 그리지 않는다.
    """
    from app.api.quota import today_kst
    from app.api.v1.observation_outs import to_observation_outs
    from app.domains.memory.observation.repository import find_observation

    if not created:
        return {}
    try:
        records = [
            record
            for domain, oid in created
            if (
                record := await find_observation(
                    session, domain=domain, child_id=child_id, observation_id=oid
                )
            )
        ]
        outs = await to_observation_outs(session, records, today=today_kst())
    except Exception as exc:
        # ERROR 다 — 스키마가 바뀐 코드 버그도 여기로 와서, 경고면 "saved 빠짐" 으로만 묻힌다.
        # 원문 대신 예외 종류와 코드 위치만 남긴다 (_guarded 와 같은 방식)
        log.error(
            "run %s 의 저장한 관찰을 다시 읽지 못했다 (%s) — saved 를 건너뛴다\n%s",
            channel.run_id,
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        # rollback 까지 실패해도 commit() 밖으로 내보내지 않는다 — 내보내면 추천 단계가 빠진다.
        # 같은 세션을 받는 도메인 Agent 가 뒤이어 실패할 수 있어서 원인은 남긴다(종류만)
        try:
            await session.rollback()
        except Exception as rollback_exc:
            log.warning(
                "run %s 의 rollback 도 실패했다 (%s)", channel.run_id, type(rollback_exc).__name__
            )
        return {}
    deleted = {(f"observation_{domain}", str(oid)): None for domain, oid in created}
    return deleted | {(out.kind, out.id): out for out in outs}


async def _read_cards(
    session: Any,
    child_id: UUID,
    parent_id: UUID,
    refs: tuple[entrypoint.Ref, ...],
    channel: RunChannel,
) -> dict[tuple[str, str], Any]:
    """조회 · 삭제 제안 카드의 행을 화면 모양으로 읽는다 (#535).

    🚨 읽기가 실패해도 run 을 실패로 만들지 않는다 — 카드에서 행이 빠질 뿐이다. 삭제 제안은 이미
       저장돼 있고 지난 대화에서 다시 읽힌다. `_read_saved` 와 같이 ERROR 로그만 남긴다(원문 없이).
    🚨 읽기는 SAVEPOINT 안에서 한다 — 실패하면 읽기만 되감는다. run 세션을 통째로 rollback 하면
       Memory commit 전에 불렸을 때 이 run 의 기록 · 제안까지 사라지고, run 은 성공으로 끝나며
       화면에는 누르면 404 인 카드가 남는다. 부르는 순서에 기대지 않으려고 여기서 막는다.
    """
    from app.api.quota import today_kst
    from app.api.v1.card_rows import read_card_rows

    if not refs:
        return {}
    try:
        async with session.begin_nested():
            return await read_card_rows(
                session,
                child_id=child_id,
                parent_id=parent_id,
                refs=[(ref.kind, ref.id) for ref in refs],
                today=today_kst(),
            )
    except Exception as exc:
        log.error(
            "run %s 의 카드 행을 읽지 못했다 (%s) — 카드에서 뺀다\n%s",
            channel.run_id,
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        return {}


async def fake_job(channel: RunChannel, *, step_delay: float | None = None) -> None:
    """step 1/3 → 2/3 → 3/3 → done. 저장은 하지 않는다.

    LLM 없이 흐름만 볼 때 쓴다 — 테스트가 agent_job 대신 이걸 끼운다. 진짜 Agent 와 같은 이벤트
    객체(Step · Done)를 번역기로 흘린다 — 화면에 가는 모양은 translate.py 한 곳에서만 정한다.
    """
    emit = translate.relay(channel)
    delay = DEMO_STEP_DELAY if step_delay is None else step_delay
    total = len(STEP_LABELS)
    for index, label in enumerate(STEP_LABELS, start=1):
        emit(entrypoint.Step(index, total, label))
        await asyncio.sleep(delay)
    emit(entrypoint.Done(channel.run_id, 0))


async def _guarded(
    channel: RunChannel, job: Job, raw_text: str, on_end: OnEnd | None = None
) -> None:
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
    finally:
        # 🚨 러너의 약속 — 무슨 일이 있어도 끝 신호는 하나다. job 이 예외로 끝났든 끝 신호 없이
        #    돌아왔든, 아직 끝 신호가 없으면 failed 를 붙인다. 안 붙이면 화면은
        #    "확인하지 못했어요" 로 떨어지고 키도 안 풀려 다시 눌러도 닫힌 run 만 재생된다.
        #    이미 나갔으면(done 뒤 결과 기록에서 터진 경우) 붙이지 않는다 — 화면은 이미 결과를
        #    받았다.
        # 🚨 Memory 단계가 끝나면 commit 하고, 추천은 Agent 결과마다 저장 · commit 한다. commit
        #    이후에 터져도 저장된 관찰 · 추천은 되돌리지 않는다 — commit 된 run 은 done 으로
        #    끝내고 키를 놓지 않는다.
        if channel.ended_with is None:
            if channel.committed:
                # commit 이후에 터졌다. 관찰 · 추천이 이미 DB 에 있으므로 done 으로 끝낸다.
                # failed 로 끝내면 키가 풀려 재시도 시 두 번 저장된다.
                done = ("done", {"run_id": channel.run_id, "model_calls": 0, "completed": False})
                channel.publish(done)
            else:
                channel.publish(sse.failed_event("internal_error", raw_text))
        # 🚨 닫기가 키 놓기보다 먼저다. forget_run 은 Redis 오류는 삼키지만(#671) 다른 예외까지
        #    막지는 않는다 — 거기서 터지면 close 가 안 불려 원문이 실린 failed 채널이 지워지지
        #    않는다 (#536 리뷰).
        channel.close()
        # 실패로 끝난 run 만 키를 놓아준다 — 같은 키로 "다시 시도" 하면 새 run 이 떠야 한다.
        # 🚨 done 으로 끝난 run 은 저장이 끝났다. 뒤에서 터졌어도 놓으면 재시도가 두 번 저장한다.
        # 🚨 publish 와 여기 사이에 await 가 없어야 한다. 화면이 failed 를 받자마자 재시도해도
        #    키가 이미 지워져 있다. close 도 sync 라 사이에 끼워도 된다.
        if channel.ended_with == "failed":
            idempotency.forget_run(channel.run_id)
    # 닫은 뒤에 부른다 — 화면은 끝 신호까지 다 받았고, 여기서 무엇이 늦어져도 기다리지 않는다
    if on_end is not None:
        await on_end(channel)


def start(
    channel: RunChannel, job: Job, *, raw_text: str, on_end: OnEnd | None = None
) -> asyncio.Task[None]:
    """job 을 뒤에서 돌리기 시작하고 바로 돌아온다. 기다리지 않는다.

    `on_end` 는 끝 신호를 내고 채널을 닫은 뒤에 한 번 부른다. 끝 신호는 job 이 아니라 여기서
    붙기도 해서(예외 · 끝 신호 없이 돌아온 job) 끝난 모양을 보고 할 일(대화 기록)은 여기서 건다.
    🚨 `on_end` 는 예외를 밖으로 내지 않아야 한다 — 이 태스크를 기다리는 곳이 없다.
    """
    task = asyncio.create_task(
        _guarded(channel, job, raw_text, on_end), name=f"run:{channel.run_id}"
    )
    channel.task = task
    return task
