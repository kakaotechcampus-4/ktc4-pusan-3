"""놀이 기억 검색 (모델 tool)."""

from datetime import timedelta

from app.agents.activity.context import ActivityContext
from app.agents.activity.gating import reads_affinity
from app.agents.activity.result import ErrorCode, ToolResult, fail, ok
from app.agents.activity.rules import normalize_activity
from app.agents.activity.schemas.memory import SearchActivityMemoryArgs
from app.agents.common.evidence import (
    OBSERVATION_WINDOW_DAYS,
    ObservationRow,
    RankedEvidence,
    rank_evidence,
)

# 모델에게 돌려주는 근거 수. 티어 순으로 자른다 — 순서를 모델에게 맡기지 않는다
MEMORY_LIMIT = 10

_MEMORY = "search_activity_memory"
# NO_RECORDS 의 다음 행동. 모델이 넣은 keywords 는 싣지 않는다 — message 는 로그로 흘러간다
_NO_MATCH = (
    "그 이름의 놀이 기록은 없다. keywords 를 비우고 다시 찾거나, 근거 없이 일반 추천을 낸다."
)
_NO_RECORDS = "놀이 기록이 없다. evidence 를 비우고 일반 추천을 낸다. 보호자에게 되묻지 않는다."


async def search_activity_memory(
    context: ActivityContext, args: SearchActivityMemoryArgs
) -> ToolResult:
    """아이의 최근 놀이 기록과 놀이 관심을 근거 순서대로 돌려준다.

    - 관찰은 최근 14일(`OBSERVATION_WINDOW_DAYS`)을 읽는다. 포트가 `status='active'` 만 주고,
      `affinity_id=NULL` 인 관찰도 온다 (D3 · K-7).
    - `gating.reads_affinity(gate)` 가 참일 때만 profile_affinity(domain=activity)를 읽는다.
      18개월 미만은 affinity 가 구조적으로 0행이라 조회를 건너뛴다.
    - `common/evidence.rank_evidence()` 로 줄 세운다. 순서를 모델에게 맡기지 않는다.
      기피(−1)도 근거로 남는다 — 후보에서 지우는 것은 안전 필터뿐이다 (D4).
    - `keywords` 가 있으면 이름에 그 말이 든 근거만 남긴다. 줄 세운 뒤에 거르므로 순서는 같다.
    - 상위 `MEMORY_LIMIT` 개만 돌려주고 `context.state.seen_evidence` 에 id → 근거를 적는다.
      출력 tool 이 이 표에 없는 id 를 인용하면 후보를 거절한다.
    - 결과에는 id, tier, label, polarity, 날짜만 싣는다. raw_text 는 싣지 않는다.
    - 0행이면 NO_RECORDS. 호출부는 일반 추천으로 간다 — 추천을 질문으로 대신하지 않는다.
    """
    gate = context.state.gate
    if gate is None:
        raise RuntimeError("Gate 없이 기억 검색이 불렸다 — run() 이 build_gate 를 먼저 부른다")

    today = context.today
    memory = context.ports.memory
    observed = await memory.observations(
        child_id=context.child_id,
        date_from=today - timedelta(days=OBSERVATION_WINDOW_DAYS),
        date_to=today,
    )
    affinities = await memory.affinities(child_id=context.child_id) if reads_affinity(gate) else []

    ranked = rank_evidence(
        tuple(affinities),
        tuple(
            # label 은 사람이 읽는 활동명(`activity`)이다. 병합용 정규화 값(`subject`)은
            # 모델이 why_this 에 옮겨 쓰기 어렵다. 발화 원문(raw_text)은 포트가 싣지 않는다
            ObservationRow(
                id=row.id,
                kind="observation_activity",
                subject=row.activity,
                polarity=row.polarity,
                observed_on=row.observed_on,
            )
            for row in observed
        ),
        today=today,
    )
    picked = _matching(ranked, args.keywords)[:MEMORY_LIMIT]
    if not picked:
        message = _NO_MATCH if args.keywords and ranked else _NO_RECORDS
        return fail("query", _MEMORY, ErrorCode.NO_RECORDS, message)

    for item in picked:
        context.state.seen_evidence[item.ref.id] = item
    return ok(
        "query",
        _MEMORY,
        records=[
            {
                "id": str(item.ref.id),
                "tier": item.tier,
                "label": item.label,
                "polarity": item.polarity,
                "observed_on": item.observed_on.isoformat(),
            }
            for item in picked
        ],
    )


def _matching(
    ranked: tuple[RankedEvidence, ...], keywords: list[str]
) -> tuple[RankedEvidence, ...]:
    """이름에 keywords 중 하나라도 든 근거. keywords 가 비면 전부다."""
    wanted = [key for key in map(normalize_activity, keywords) if key]
    if not wanted:
        return ranked
    return tuple(
        item for item in ranked if any(key in normalize_activity(item.label) for key in wanted)
    )
