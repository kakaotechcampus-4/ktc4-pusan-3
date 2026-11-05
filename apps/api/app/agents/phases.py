"""파이프라인 상태(Phase) 정의 — 아직 어디서도 쓰지 않는 설계 초안.

handle_input 은 지금 async 함수 하나 안에서 if/else 로 단계를 넘긴다. 이 파일은 나중에 단계를
상태 머신으로 바꿀 때 쓸 이름과 전이만 적어 둔다. 실제 흐름의 정본은 pipeline.py · runner.py 이고,
여기 설명이 그 둘과 어긋나면 이 파일을 고친다 (#257).

나누는 기준은 DB 트랜잭션 경계다. 모델 호출(LLM API)은 별도 Phase 가 아니라
그 Phase 안의 부작용이다.

전이표는 둘이다.
- RUN_TRANSITIONS: 입력 한 건(run)의 단계. 끝 신호(Done · Partial · Failed)는 FINALIZE 에서 나간다.
- CURATOR_TRANSITIONS: Memory commit 직후 백그라운드 task 로 따로 도는 Curator. run 의 끝을
  기다리지 않고, run 도 Curator 를 기다리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PipelinePhase(Enum):
    """파이프라인의 각 단계. 값은 로그·직렬화용 문자열.

    ── run ───────────────────────────────────────────────────

    SAFETY_PRECHECK
        DB 성격: none
        트랜잭션: 없음
        실패 시: failed (저장한 것 없음)
        모델 호출: 없음

    SUPERVISE
        DB 성격: none (Supervisor 는 DB 를 쓰지 않는다. 아이 정보는 caller 가 미리 읽어 넘긴다)
        트랜잭션: 없음
        실패 시: run 은 실패하지 않는다. 강등 routing 으로 원문 전체를 Memory 에 넘기고
                 도메인 Agent 는 부르지 않는다
        모델 호출: 포함 (Supervisor). 상태 전환 단위가 아님

    ROUTE
        DB 성격: none (Supervisor 결과를 규칙으로 분류)
        트랜잭션: 없음
        실패 시: 배선 버그(예외) -> 러너가 failed (저장한 것 없음)
        모델 호출: 없음

    RECORD
        DB 성격: write
        트랜잭션: Memory 단계 전체가 러너 세션 하나. 끝나면 commit 콜백을 한 번 부른다
        실패 시: commit 전이라 러너가 세션을 되돌린다 -> failed. 반복 상한(completed=False)도
                 commit 하지 않고 failed(unparsable)
        모델 호출: 포함 (Memory Agent). 상태 전환 단위가 아님

    RECORD_EMIT
        DB 성격: none (프로세스 메모리)
        트랜잭션: 없음 (commit 된 결과를 Saved · EventDrafts · MemoryNote · PendingReply 로 낸다)
        실패 시: 이벤트 번역이 터지면 run 이 거기서 끝난다. 끝 신호는 러너가 붙인다 —
                 commit 된 쓰기가 있으면 done(completed=false), 없으면 failed (runner._guarded)
        모델 호출: 없음
        비고: 되묻기 맥락(PendingReply)은 러너가 프로세스 메모리에 15분 둔다

    REROUTE
        DB 성격: none
        트랜잭션: 없음
        실패 시: 무시하고 기존 routing 으로 도메인 단계 진행
        모델 호출: 포함 (Supervisor 재호출). 상태 전환 단위가 아님
        비고: Memory 가 적지 않은 RECORD 조각이 있고 도메인 task 가 없을 때만 진입

    DOMAIN
        DB 성격: read + write
        트랜잭션: 포트 호출마다 짧은 세션. 기억 포트(StoreFoodMemory)는 러너가 준 opener 로
                  호출마다 세션을 따로 연다 — run 세션을 같이 타지 않는다 (#257).
                  나머지 포트는 아직 DB 구현이 없다. 붙일 때 같은 원칙(호출마다 짧은 세션,
                  쓰기 포트는 호출 1회 = 즉시 commit)을 따른다
        실패 시: 그 Agent(task)만 빠진다 -> partial
        모델 호출: 포함 (도메인 Agent). 상태 전환 단위가 아님
        비고: 쓰는 task 를 먼저 끝낸 뒤 읽는 task 동시 실행. 20초 deadline 적용

    CONTINUATION_RECORD
        DB 성격: write
        트랜잭션: RECORD 와 같다
        실패 시: RECORD 와 같다
        모델 호출: 포함 (Memory Agent). 상태 전환 단위가 아님
        비고: 이어받기 run. Supervisor 를 타지 않고 RECORD_EMIT -> FINALIZE 로 간다. 도메인 Agent 도
              타지 않는다. 도메인 Agent 가 물은 질문의 답(`PendingMemoryContext.resume`)을
              저장했으면 그 task 하나만 DOMAIN 단계를 다시 돈다. 남은 시간은 입력 시각부터 센
              같은 20초다

    FINALIZE
        DB 성격: none
        트랜잭션: 없음
        실패 시: 끝 신호가 안 나가면 러너가 붙인다 — commit 된 쓰기가 있으면
                 done(completed=false), 없으면 failed (runner._guarded)
        모델 호출: 없음
        비고: 결과 집계, 끝 신호(Failed · Partial · Done) 발행, 로그

    ── Curator (백그라운드) ──────────────────────────────────
    Memory commit 직후 pipeline 이 curator_trigger(child_id, 기준일) 를 부르면, 러너가 만든 콜백이
    별도 세션에서 백그라운드 task 로 띄운다 (domains/memory/curator/trigger.py).
    지금은 세 단계가 세션 하나 · 트랜잭션 하나다(아이별 advisory lock 포함). 모델 호출을
    트랜잭션 밖으로 빼는 분리안은 공통_구현_계획 §10 K-13 에 열려 있다.

    CURATOR_EMBED
        DB 성격: read -> write
        트랜잭션(지금): Curator 트랜잭션 안. list_unembedded -> embedder.embed -> save_embeddings
        트랜잭션(분리안): 읽기 commit -> embed(트랜잭션 밖) -> 쓰기 commit
        실패 시: Curator 트랜잭션 전체 rollback. 관찰은 이미 commit 돼 있고 다음 실행이 다시 집는다
        모델 호출: embedder.embed

    CURATOR_LINK
        DB 성격: read -> write
        트랜잭션(지금): Curator 트랜잭션 안. list_unlinked + list_profiles -> judge.judge
                        -> link + create_profile
        트랜잭션(분리안): 읽기 commit -> judge(트랜잭션 밖) -> 쓰기 commit
        실패 시: CURATOR_EMBED 와 같다
        모델 호출: judge.judge (judge 가 없으면 부르지 않는다)

    CURATOR_RECOMPUTE
        DB 성격: read + write
        트랜잭션: Curator 트랜잭션 안. 연결된 프로필이 있을 때만
        실패 시: CURATOR_EMBED 와 같다
        모델 호출: 없음
    """

    # run
    SAFETY_PRECHECK = "safety_precheck"
    SUPERVISE = "supervise"
    ROUTE = "route"
    RECORD = "record"
    RECORD_EMIT = "record_emit"
    REROUTE = "reroute"
    DOMAIN = "domain"
    CONTINUATION_RECORD = "continuation_record"
    FINALIZE = "finalize"

    # Curator (백그라운드)
    CURATOR_EMBED = "curator_embed"
    CURATOR_LINK = "curator_link"
    CURATOR_RECOMPUTE = "curator_recompute"


# 각 Phase 에서 갈 수 있는 다음 Phase. None 은 종료. 조건 분기는 런타임이 정한다.
RUN_TRANSITIONS: dict[PipelinePhase, tuple[PipelinePhase | None, ...]] = {
    PipelinePhase.SAFETY_PRECHECK: (
        PipelinePhase.SUPERVISE,
        PipelinePhase.CONTINUATION_RECORD,
    ),
    PipelinePhase.SUPERVISE: (PipelinePhase.ROUTE,),
    PipelinePhase.ROUTE: (
        PipelinePhase.RECORD,
        PipelinePhase.DOMAIN,
        PipelinePhase.FINALIZE,
    ),
    PipelinePhase.RECORD: (PipelinePhase.RECORD_EMIT, PipelinePhase.FINALIZE),
    PipelinePhase.RECORD_EMIT: (
        PipelinePhase.REROUTE,
        PipelinePhase.DOMAIN,
        PipelinePhase.FINALIZE,
    ),
    PipelinePhase.REROUTE: (PipelinePhase.DOMAIN, PipelinePhase.FINALIZE),
    PipelinePhase.DOMAIN: (PipelinePhase.FINALIZE,),
    PipelinePhase.CONTINUATION_RECORD: (
        PipelinePhase.RECORD_EMIT,
        PipelinePhase.FINALIZE,
    ),
    PipelinePhase.FINALIZE: (None,),
}

# Curator 는 CURATOR_EMBED 에서 시작한다. None 은 Curator 종료(실패면 전체 rollback).
CURATOR_TRANSITIONS: dict[PipelinePhase, tuple[PipelinePhase | None, ...]] = {
    PipelinePhase.CURATOR_EMBED: (PipelinePhase.CURATOR_LINK, None),
    PipelinePhase.CURATOR_LINK: (PipelinePhase.CURATOR_RECOMPUTE, None),
    PipelinePhase.CURATOR_RECOMPUTE: (None,),
}


@dataclass(frozen=True)
class PhaseResult:
    """한 Phase 의 실행 결과."""

    phase: PipelinePhase
    ok: bool
    next_phase: PipelinePhase | None
