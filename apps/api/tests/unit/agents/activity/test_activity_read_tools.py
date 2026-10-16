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
from app.agents.activity.tools.memory import (
    MEMORY_LIMIT,
    RECENT_RESERVED,
    search_activity_memory,
)
from app.agents.common.evidence import OBSERVATION_WINDOW_DAYS, AffinityRow

CHILD = UUID(int=1)
NOW = datetime(2026, 10, 9, 10, tzinfo=UTC)
TODAY = NOW.date()
BORN_30_MONTHS = date(2024, 4, 1)
BORN_12_MONTHS = date(2025, 10, 1)


def observation(n: int, activity: str, *, subject=None, days_ago: int = 1, polarity: int = 1):
    """subject 는 Curator 가 만드는 짧은 대조 키, activity 는 사람이 읽는 활동명이다.
    따로 주지 않으면 활동명의 첫 낱말을 subject 로 쓴다 ("모래 놀이" → "모래")."""
    return ActivityObservation(
        id=UUID(int=100 + n),
        child_id=CHILD,
        observed_on=TODAY - timedelta(days=days_ago),
        subject=subject or activity.split()[0],
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
        ctx = await context([observation(1, "레고로 성 만들기", subject="레고", polarity=-1)])
        result = await search(ctx)
        assert result.data["records"] == [
            {
                "id": str(UUID(int=101)),
                "tier": 3,
                "label": "레고로 성 만들기",
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

    async def test_keywords_는_subject_와_양쪽으로_본다(self):
        """모델은 "모래놀이" 처럼 길게, subject 는 "모래" 처럼 짧게 쓴다 (#304 리뷰)."""
        ctx = await context(affinities=[affinity(1, "모래")])
        assert labels(await search(ctx, ["모래놀이"])) == ["모래"]

    async def test_한_글자는_포함으로_걸리지_않는다(self):
        ctx = await context([observation(1, "공룡 놀이")], [affinity(1, "공")])
        assert labels(await search(ctx, ["공룡"])) == ["공룡 놀이"]
        assert labels(await search(ctx, ["공"])) == ["공"]

    async def test_keywords_는_활동명에_든_말로도_찾는다(self):
        ctx = await context([observation(1, "레고로 성 만들기", subject="레고")])
        assert labels(await search(ctx, ["성 만들기"])) == ["레고로 성 만들기"]

    async def test_같은_subject_관찰은_가장_최근_1건만(self):
        ctx = await context(
            [
                observation(1, "레고로 성 만들기", subject="레고", days_ago=5),
                observation(2, "레고로 기차 만들기", subject="레고", days_ago=2),
                observation(3, "레고 정리하기 싫어함", subject="레고", days_ago=3, polarity=-1),
            ]
        )
        assert labels(await search(ctx)) == ["레고로 기차 만들기", "레고 정리하기 싫어함"]

    async def test_최근_관찰_자리를_먼저_보장한다(self):
        """첫 관찰부터 티어 2 인 affinity 가 10개여도 최근 관찰이 남는다. 순서는 티어 순이다."""
        ctx = await context(
            [observation(n, f"놀이{n} 하기", days_ago=n) for n in range(1, 5)],
            [affinity(n, f"관심{n}", state="candidate", strength=0.5) for n in range(MEMORY_LIMIT)],
        )
        tiers = [record["tier"] for record in (await search(ctx)).data["records"]]
        assert tiers == [2] * (MEMORY_LIMIT - RECENT_RESERVED) + [3] * RECENT_RESERVED

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


def doc(n: int, *, min_month: int, max_month: int, title=None, materials=()) -> ActivityDocRow:
    return ActivityDocRow(
        id=UUID(int=300 + n),
        doc_key=f"doc_{n}",
        title=title or f"놀이 {n}",
        body="본문",
        min_month=min_month,
        max_month=max_month,
        setting=ActivitySetting.INDOOR,
        materials=tuple(materials),
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


class TestDocVariety:
    """같은 월령이 매번 같은 [예시]를 받지 않게 고른다 (#332 라이브 확인)."""

    docs = InMemoryActivityDocs([doc(n, min_month=24, max_month=36) for n in range(14)])

    async def keys(self, query="", rotation=""):
        rows = await search_activity_doc(self.docs, months=30, query=query, rotation=rotation)
        return [row.doc_key for row in rows]

    async def test_같은_아이_같은_날은_같은_예시(self):
        assert await self.keys(rotation="아이1|2026-10-11") == await self.keys(
            rotation="아이1|2026-10-11"
        )

    async def test_날이_바뀌면_예시가_바뀐다(self):
        days = [await self.keys(rotation=f"아이1|2026-10-{d}") for d in range(11, 15)]
        assert len({tuple(day) for day in days}) > 1
        assert all(len(day) == DOC_LIMIT for day in days)

    async def test_파일_앞쪽만_주지_않는다(self):
        """2주 동안 고른 예시가 한 구간 14행 중 앞 5행에 갇히지 않는다."""
        seen = set()
        for d in range(1, 15):
            seen |= set(await self.keys(rotation=f"아이1|2026-10-{d:02d}"))
        assert len(seen) > DOC_LIMIT

    async def test_요청_낱말이_든_행을_먼저_준다(self):
        docs = InMemoryActivityDocs(
            [doc(n, min_month=24, max_month=36) for n in range(10)]
            + [doc(90, min_month=24, max_month=36, title="큰 공 굴리기", materials=["공"])]
        )
        for day in range(11, 15):
            rows = await search_activity_doc(
                docs, months=30, query="공놀이 하고 싶어", rotation=f"아이1|2026-10-{day}"
            )
            assert rows[0].doc_key == "doc_90"

    async def test_흔한_낱말로는_고르지_않는다(self):
        """ "놀이" 는 모든 제목에 들어 있어 고르는 데 쓰지 않는다 — 돌림 순서 그대로다."""
        assert await self.keys(query="놀이 하자", rotation="r") == await self.keys(rotation="r")
