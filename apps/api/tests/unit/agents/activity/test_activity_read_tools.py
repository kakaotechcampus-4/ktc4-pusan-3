"""읽기 tool — 놀이 기억 검색 · 문서 행 조회.

- 기억은 rank_evidence 순서 그대로, 상위 몇 개만, id 와 함께 돌려준다. 돌려준 것만 run state 에
  남는다 — 출력 검증이 이 표에 없는 id 를 거절한다.
- 18개월 미만은 profile_affinity 를 읽지 않는다. 관찰로만 근거를 만든다.
- 0행은 NO_RECORDS 다. 일반 추천으로 가라는 신호이고 되묻기가 아니다.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.result import ErrorCode
from app.agents.activity.schemas.common import ActivitySetting, CaregiverRole, Intensity
from app.agents.activity.schemas.memory import SearchActivityMemoryArgs
from app.agents.activity.store.inmemory import (
    InMemoryActivityDocs,
    InMemoryActivityMemory,
    in_memory_ports,
)
from app.agents.activity.store.ports import ActivityDocRow, ActivityObservation
from app.agents.activity.tools.docs import DOC_LIMIT, search_activity_doc
from app.agents.activity.tools.memory import MEMORY_LIMIT, search_activity_memory
from app.agents.common.evidence import OBSERVATION_WINDOW_DAYS, AffinityRow

CHILD = UUID(int=1)
NOW = datetime(2026, 10, 9, 10, tzinfo=UTC)
TODAY = NOW.date()
BORN_30_MONTHS = date(2024, 4, 1)
BORN_12_MONTHS = date(2025, 10, 1)


def observation(n: int, activity: str, *, days_ago: int = 1, polarity: int = 1):
    return ActivityObservation(
        id=UUID(int=100 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=activity.replace(" ", ""),
        activity=activity,
        polarity=polarity,
    )


def affinity(n: int, label: str, *, state="confirmed", polarity=1, strength=0.9):
    return AffinityRow(
        id=UUID(int=200 + n),
        merge_key=label,
        state=state,
        polarity=polarity,
        strength=strength,
        last_observed_on=TODAY - timedelta(days=30),
    )


async def context(observations=(), affinities=(), *, born=BORN_30_MONTHS) -> ActivityContext:
    memory = InMemoryActivityMemory({CHILD: affinities}, {CHILD: observations})
    ctx = ActivityContext(
        child_id=CHILD,
        run_id="run-1",
        now=NOW,
        timezone=UTC,
        ports=in_memory_ports(CHILD, born, memory=memory),
    )
    ctx.state.gate = await build_gate(ctx, outdoor_ok=True)
    return ctx


async def search(ctx, keywords=()):
    return await search_activity_memory(ctx, SearchActivityMemoryArgs(keywords=list(keywords)))


def labels(result) -> list[str]:
    return [record["label"] for record in result.data["records"]]


class TestMemory:
    async def test_티어_순서로_돌려준다(self):
        ctx = await context(
            [observation(1, "모래 놀이")],
            [affinity(1, "블록", state="candidate", strength=0.6), affinity(2, "그림")],
        )
        result = await search(ctx)
        assert result.success is True
        assert labels(result) == ["그림", "블록", "모래 놀이"]
        assert [record["tier"] for record in result.data["records"]] == [1, 2, 3]

    async def test_id_와_필요한_칸만_싣는다(self):
        """raw_text 는 포트가 싣지 않고, 병합용 subject 대신 사람이 읽는 활동명을 준다."""
        result = await search(await context([observation(1, "레고 조립", polarity=-1)]))
        assert result.data["records"] == [
            {
                "id": str(UUID(int=101)),
                "tier": 3,
                "label": "레고 조립",
                "polarity": -1,
                "observed_on": (TODAY - timedelta(days=1)).isoformat(),
            }
        ]

    async def test_돌려준_근거만_run_state_에_남긴다(self):
        rows = [observation(n, f"놀이{n}", days_ago=1 + n % 5) for n in range(MEMORY_LIMIT + 3)]
        ctx = await context(rows)
        result = await search(ctx)
        returned = {UUID(record["id"]) for record in result.data["records"]}
        assert len(returned) == MEMORY_LIMIT
        assert set(ctx.state.seen_evidence) == returned

    async def test_관찰은_최근_14일만_읽는다(self):
        ctx = await context(
            [
                observation(1, "최근", days_ago=OBSERVATION_WINDOW_DAYS),
                observation(2, "오래됨", days_ago=OBSERVATION_WINDOW_DAYS + 1),
            ]
        )
        assert labels(await search(ctx)) == ["최근"]

    async def test_기피도_근거로_남는다(self):
        """후보에서 지우는 것은 안전 필터뿐이다. 기피 대상은 출력 검증이 거절한다 (D4)."""
        result = await search(await context([observation(1, "물감 놀이", polarity=-1)]))
        assert result.data["records"][0]["polarity"] == -1

    async def test_18개월_미만은_affinity_를_읽지_않는다(self):
        ctx = await context([observation(1, "까꿍")], [affinity(1, "블록")], born=BORN_12_MONTHS)
        assert labels(await search(ctx)) == ["까꿍"]

    async def test_keywords_는_이름에_그_말이_든_근거만_남긴다(self):
        ctx = await context([observation(1, "모래 놀이"), observation(2, "블록 쌓기")])
        assert labels(await search(ctx, ["모래"])) == ["모래 놀이"]
        assert labels(await search(ctx, ["블록 쌓기"])) == ["블록 쌓기"]

    async def test_기록이_없으면_NO_RECORDS(self):
        result = await search(await context())
        assert result.success is False
        assert result.error["code"] == ErrorCode.NO_RECORDS
        assert "되묻지 않는다" in result.error["message"]

    async def test_keywords_에_맞는_게_없으면_비우고_다시_찾게_한다(self):
        ctx = await context([observation(1, "모래 놀이")])
        result = await search(ctx, ["수영"])
        assert result.error["code"] == ErrorCode.NO_RECORDS
        assert "keywords 를 비우고" in result.error["message"]
        assert "수영" not in result.error["message"]  # 모델이 넣은 값은 로그로 흘리지 않는다
        assert ctx.state.seen_evidence == {}

    async def test_gate_없이_불리면_예외(self):
        ctx = await context()
        ctx.state.gate = None
        with pytest.raises(RuntimeError):
            await search(ctx)


def doc(n: int, *, min_month: int, max_month: int) -> ActivityDocRow:
    return ActivityDocRow(
        id=UUID(int=300 + n),
        doc_key=f"doc_{n}",
        title=f"놀이 {n}",
        body="본문",
        min_month=min_month,
        max_month=max_month,
        setting=ActivitySetting.INDOOR,
        materials=(),
        caregiver_role=CaregiverRole.TOGETHER,
        physical_intensity=Intensity.LOW,
        involves_food=False,
    )


class TestDocs:
    async def test_월령_슬라이스_안의_행만_준다(self):
        docs = InMemoryActivityDocs(
            [doc(1, min_month=0, max_month=12), doc(2, min_month=12, max_month=24)]
        )
        rows = await search_activity_doc(docs, months=12, query="")
        assert [row.doc_key for row in rows] == ["doc_2"]

    async def test_DOC_LIMIT_에서_자른다(self):
        docs = InMemoryActivityDocs([doc(n, min_month=0, max_month=72) for n in range(8)])
        rows = await search_activity_doc(docs, months=30, query="")
        assert len(rows) == DOC_LIMIT

    async def test_0행은_실패가_아니라_빈_튜플(self):
        assert await search_activity_doc(InMemoryActivityDocs(), months=30, query="") == ()
