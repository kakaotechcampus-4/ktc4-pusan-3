"""파이프라인 상태(Phase) 정의.

handle_input 의 흐름을 DB 트랜잭션 경계 단위로 직렬화한다.
모델 호출(LLM API)은 별도 Phase 가 아니라 해당 Phase 안의 부작용이다.
Curator 는 모델 호출 분리를 위해 embed / link / recompute 세 Phase 로 쪼갠다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PipelinePhase(Enum):
    """파이프라인의 각 단계. 값은 로그·직렬화용 문자열.

    ── 일반 run ──────────────────────────────────────────────

    SAFETY_PRECHECK
        DB 성격: none
        트랜잭션: 없음
        실패 시: failed (저장한 것 없음)
        모델 호출: 없음

    SUPERVISE
        DB 성격: read (아이 정보 조회는 caller 가 미리 한다)
        트랜잭션: 짧은 read session
        실패 시: failed (저장한 것 없음)
        모델 호출: 포함 (Supervisor). 상태 전환 단위가 아님

    ROUTE
        DB 성격: none (Supervisor 결과를 규칙으로 분류)
        트랜잭션: 없음
        실패 시: failed (저장한 것 없음)
        모델 호출: 없음

    RECORD
        DB 성격: write
        트랜잭션: Memory 단계 전체가 하나의 transaction -> commit
        실패 시: 전부 rollback -> failed
        모델 호출: 포함 (Memory Agent). 상태 전환 단위가 아님

    RECORD_EMIT
        DB 성격: none (프로세스 메모리)
        트랜잭션: 없음 (commit 된 결과를 이벤트로 내보냄)
        실패 시: 이벤트 누락. run 자체는 계속
        모델 호출: 없음
        비고: 되묻기 맥락(PendingReply)은 프로세스 메모리에 15분 유지

    REROUTE
        DB 성격: none
        트랜잭션: 없음
        실패 시: 무시하고 기존 routing 으로 도메인 단계 진행
        모델 호출: 포함 (Supervisor 재호출). 상태 전환 단위가 아님
        비고: Memory 가 적지 않은 RECORD 조각이 있고 도메인 task 가 없을 때만 진입

    DOMAIN
        DB 성격: read + write
        트랜잭션: 포트 호출마다 짧은 session. 쓰기 포트는 호출 1회 = 즉시 commit
        실패 시: 그 Agent 만 실패 -> partial
        모델 호출: 포함 (도메인 Agent). 상태 전환 단위가 아님
        비고: 쓰는 task 를 먼저 끝낸 뒤 읽는 task 동시 실행. 20초 deadline 적용

    ── Curator (백그라운드, 3 Phase) ───────────────────────────
    Memory commit 직후 백그라운드로 실행. run 결과를 기다리지 않는다.
    현재는 한 트랜잭션이지만, 모델 호출을 트랜잭션 밖으로 빼려면
    아래 세 Phase 로 쪼개야 한다.

    CURATOR_EMBED
        DB 성격: read -> write
        트랜잭션: list_unembedded (read) → commit → embedder.embed (tx 밖)
                  → save_embeddings (write) → commit
        실패 시: 벡터 미저장, 다음 run 이 다시 집는다
        모델 호출: embedder.embed (트랜잭션 밖으로 분리 가능)

    CURATOR_LINK
        DB 성격: read -> write
        트랜잭션: list_unlinked + list_profiles (read) → commit
                  → judge.judge (tx 밖) → link + create_profile (write) → commit
        실패 시: 미연결, 다음 run 이 다시 집는다
        모델 호출: judge.judge (트랜잭션 밖으로 분리 가능)

    CURATOR_RECOMPUTE
        DB 성격: read + write
        트랜잭션: 짧은 transaction → commit
        실패 시: 상태 미갱신, 다음 run 이 다시 계산
        모델 호출: 없음

    ── 공통 ────────────────────────────────────────────────────

    FINALIZE
        DB 성격: none
        트랜잭션: 없음
        실패 시: 로그 누락
        모델 호출: 없음
        비고: 결과 집계, Failed/Partial/Done 이벤트 발행, 로그

    ── 이어받기 run (continuation) ───────────────────────────

    CONTINUATION_RECORD
        DB 성격: write
        트랜잭션: RECORD 와 동일 (하나의 transaction -> commit)
        실패 시: 전부 rollback -> failed
        모델 호출: 포함 (Memory Agent). 상태 전환 단위가 아님
        비고: Supervisor/도메인 Agent 를 타지 않음.
              RECORD_EMIT -> CURATOR_EMBED -> FINALIZE 로 이어짐
    """

    # 일반 run
    SAFETY_PRECHECK = "safety_precheck"
    SUPERVISE = "supervise"
    ROUTE = "route"
    RECORD = "record"
    RECORD_EMIT = "record_emit"
    REROUTE = "reroute"
    DOMAIN = "domain"

    # Curator (백그라운드, 3 Phase)
    CURATOR_EMBED = "curator_embed"
    CURATOR_LINK = "curator_link"
    CURATOR_RECOMPUTE = "curator_recompute"

    FINALIZE = "finalize"

    # 이어받기 run
    CONTINUATION_RECORD = "continuation_record"


# Phase 전이 그래프. 각 Phase 에서 갈 수 있는 다음 Phase 들.
# None 은 종료. 조건 분기는 런타임이 정한다.
TRANSITIONS: dict[PipelinePhase, tuple[PipelinePhase | None, ...]] = {
    # 일반 run
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
        PipelinePhase.CURATOR_EMBED,
        PipelinePhase.FINALIZE,
    ),
    PipelinePhase.REROUTE: (PipelinePhase.DOMAIN, PipelinePhase.FINALIZE),
    PipelinePhase.DOMAIN: (PipelinePhase.FINALIZE,),
    # Curator 체인
    PipelinePhase.CURATOR_EMBED: (
        PipelinePhase.CURATOR_LINK,
        PipelinePhase.FINALIZE,
    ),
    PipelinePhase.CURATOR_LINK: (
        PipelinePhase.CURATOR_RECOMPUTE,
        PipelinePhase.FINALIZE,
    ),
    PipelinePhase.CURATOR_RECOMPUTE: (PipelinePhase.FINALIZE,),
    PipelinePhase.FINALIZE: (None,),
    # 이어받기 run
    PipelinePhase.CONTINUATION_RECORD: (
        PipelinePhase.RECORD_EMIT,
        PipelinePhase.FINALIZE,
    ),
}


@dataclass(frozen=True)
class PhaseResult:
    """한 Phase 의 실행 결과."""

    phase: PipelinePhase
    ok: bool
    next_phase: PipelinePhase | None
