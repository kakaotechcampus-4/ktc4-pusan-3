"""입구 link_observations 테스트. 두 단계를 묶은 뒤에도 약속이 지켜지는지 본다.

    - 한 번의 호출로 임베딩되고 연결된다
    - 임베딩이 실패해도 이미 벡터가 있는 관찰은 연결된다
    - 관찰마다 결과가 정확히 하나다
    - 판정기가 없어도 임베딩과 판정이 필요 없는 연결은 한다
    - 두 번 불러도 같은 일을 다시 하지 않는다

단계별 세부 동작은 test_embed_step.py · test_link_step.py 에서 본다.
"""

import math
from collections.abc import Sequence
from uuid import UUID

import pytest

from app.agents.common.llm_client import LLMUnavailableError
from app.agents.curator.embedding import JudgeAnswer, LinkResult, link_observations
from app.agents.curator.embedding.inmemory import InMemoryCuratorStore
from app.agents.curator.embedding.judge import NONE
from app.agents.curator.embedding.ports import CuratorDomain

CHILD = UUID("00000000-0000-0000-0000-000000000001")


def _at(deg: float) -> list[float]:
    rad = math.radians(deg)
    return [math.cos(rad), math.sin(rad)]


VECTORS = {"딸기": _at(0), "생딸기": _at(10), "레고": _at(90), "블루베리": _at(40)}
SAME = {"생딸기": "딸기"}  # 가짜 판정기가 같은 대상이라고 답할 짝


class FakeEmbedder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self._error = error

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._error is not None:
            raise self._error
        return [VECTORS[text] for text in texts]


class FakeJudge:
    """SAME 에 있는 짝이 후보에 있으면 그것을, 아니면 none 을 답한다."""

    def __init__(self) -> None:
        self.calls = 0

    async def judge(
        self, *, subject: str, domain: CuratorDomain, candidates: Sequence[str]
    ) -> JudgeAnswer:
        self.calls += 1
        match = SAME.get(subject)
        return JudgeAnswer(match if match in candidates else NONE, model="fake")


def _observe(
    store: InMemoryCuratorStore, id: str, subject: str, *, embedding: list[float] | None = None
) -> None:
    store.add_observation(
        child_id=CHILD, domain="food", id=id, subject=subject, polarity=1, embedding=embedding
    )


async def _run(
    store: InMemoryCuratorStore, embedder: FakeEmbedder, judge: FakeJudge | None = None
) -> LinkResult:
    return await link_observations(store, embedder, judge or FakeJudge(), child_id=CHILD)


def _by_id(result: LinkResult) -> dict[str, tuple[str, str | None]]:
    return {o.key[1]: (o.status, o.reason) for o in result.outcomes}


async def test_한_번의_호출로_임베딩되고_연결된다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")
    _observe(store, "2", "생딸기")
    _observe(store, "3", "레고")
    embedder = FakeEmbedder()

    result = await _run(store, embedder)

    assert embedder.calls == [["딸기", "생딸기", "레고"]]
    assert result.embed_calls == 1
    assert [(o.key[1], o.status, o.match) for o in result.outcomes] == [
        ("1", "created", "new"),  # 후보가 없어 판정기를 부르지 않았다
        ("2", "linked", "judged"),  # 판정기가 딸기와 같은 대상이라고 답했다
        ("3", "created", "new"),  # 판정기가 같은 후보가 없다고 답했다
    ]
    assert result.held == ()


async def test_임베딩이_실패해도_이미_벡터가_있는_관찰은_연결된다() -> None:
    store = InMemoryCuratorStore()
    # 지난 실행에서 벡터만 저장되고 연결 전에 멈춘 관찰
    _observe(store, "old", "딸기", embedding=VECTORS["딸기"])
    _observe(store, "new", "레고")

    result = await _run(store, FakeEmbedder(LLMUnavailableError("down")))

    assert _by_id(result) == {"old": ("created", None), "new": ("held", "embedding_failed")}
    assert store.observation("food", "new").embedding is None  # 다음 실행이 다시 집는다


async def test_빈_subject_는_empty_subject_로_나온다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")
    _observe(store, "blank", "  ")

    result = await _run(store, FakeEmbedder())

    assert _by_id(result) == {"1": ("created", None), "blank": ("held", "empty_subject")}


async def test_문제_Profile_id_가_결과까지_전달된다() -> None:
    store = InMemoryCuratorStore()
    broken = store.add_profile(
        child_id=CHILD, domain="food", merge_key=NONE, polarity=1, embedding=VECTORS["딸기"]
    )
    _observe(store, "1", "딸기")

    result = await _run(store, FakeEmbedder())

    [held] = result.held
    assert (held.reason, held.invalid_profile_ids) == ("invalid_candidate_name", (broken.id,))


async def test_관찰마다_결과가_정확히_하나다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "old", "딸기", embedding=VECTORS["딸기"])
    _observe(store, "new", "레고")
    _observe(store, "blank", "")

    result = await _run(store, FakeEmbedder(LLMUnavailableError("down")))

    keys = [o.key for o in result.outcomes]
    assert sorted(keys) == sorted([("food", "old"), ("food", "new"), ("food", "blank")])
    # 순서: 연결 단계 결과 → 임베딩 실패 → 빈 subject
    assert [o.reason for o in result.outcomes] == [None, "embedding_failed", "empty_subject"]


async def test_판정기가_없어도_임베딩과_판정이_필요_없는_연결은_한다() -> None:
    store = InMemoryCuratorStore()
    store.add_profile(
        child_id=CHILD, domain="food", merge_key="딸기", polarity=1, embedding=VECTORS["딸기"]
    )
    _observe(store, "1", "딸기")  # 이름이 같다 → 연결
    _observe(store, "2", "생딸기")  # 판정이 필요하다 → 보류
    embedder = FakeEmbedder()

    result = await link_observations(store, embedder, None, child_id=CHILD)

    assert embedder.calls == [["딸기", "생딸기"]]
    assert _by_id(result) == {"1": ("linked", None), "2": ("held", "judge_unavailable")}
    assert store.observation("food", "2").embedding is not None  # 임베딩은 저장됐다


async def test_대상이_없으면_아무것도_하지_않는다() -> None:
    embedder, judge = FakeEmbedder(), FakeJudge()
    result = await _run(InMemoryCuratorStore(), embedder, judge)

    assert result.outcomes == ()
    assert result.embed_calls == 0
    assert (embedder.calls, judge.calls) == ([], 0)


async def test_두_번_불러도_같은_일을_다시_하지_않는다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")
    _observe(store, "2", "생딸기")
    await _run(store, FakeEmbedder())

    embedder, judge = FakeEmbedder(), FakeJudge()
    again = await _run(store, embedder, judge)

    assert again.outcomes == ()
    assert (embedder.calls, judge.calls) == ([], 0)
    assert len(store.profiles) == 1


async def test_영향받은_Profile_은_중복_없이_처음_나온_순서다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")
    _observe(store, "2", "레고")
    _observe(store, "3", "생딸기")  # 1 이 만든 딸기 Profile 에 붙는다
    _observe(store, "blank", "")

    result = await _run(store, FakeEmbedder())

    strawberry, lego = (o.affinity_id for o in result.outcomes[:2])
    assert result.affected_profile_ids == (strawberry, lego)
    assert result.created_profile_ids == (strawberry, lego)
    assert [o.key[1] for o in result.held] == ["blank"]


async def test_LLMError_가_아닌_예외는_그대로_올라온다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")

    with pytest.raises(ValueError, match="bug"):
        await _run(store, FakeEmbedder(ValueError("bug")))
