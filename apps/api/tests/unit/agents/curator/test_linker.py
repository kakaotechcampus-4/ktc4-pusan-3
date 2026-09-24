"""입구 link_observations 테스트. 두 단계를 묶은 뒤에도 약속이 지켜지는지 본다.

    - 한 번의 호출로 임베딩되고 연결된다
    - 임베딩이 실패해도 이미 벡터가 있는 관찰은 연결된다
    - 관찰마다 결과가 정확히 하나다
    - 잘못된 threshold 는 API 를 부르기 전에 거절한다
    - 두 번 불러도 같은 일을 다시 하지 않는다

단계별 세부 동작은 test_embed_step.py · test_link_step.py 에서 본다.
"""

import math
from collections.abc import Sequence
from uuid import UUID

import pytest

from app.agents.common.llm_client import LLMUnavailableError
from app.agents.curator.embedding import LinkResult, link_observations
from app.agents.curator.embedding.inmemory import InMemoryCuratorStore

CHILD = UUID("00000000-0000-0000-0000-000000000001")


def _at(deg: float) -> list[float]:
    rad = math.radians(deg)
    return [math.cos(rad), math.sin(rad)]


# subject → 벡터. 딸기·생딸기는 가깝고(유사도 ≈ 0.98) 레고는 멀다
VECTORS = {"딸기": _at(0), "생딸기": _at(10), "레고": _at(90), "한글 자모": _at(180)}


class FakeEmbedder:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self._error = error

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._error is not None:
            raise self._error
        return [VECTORS[text] for text in texts]


def _observe(
    store: InMemoryCuratorStore, id: str, subject: str, *, embedding: list[float] | None = None
) -> None:
    store.add_observation(
        child_id=CHILD, domain="food", id=id, subject=subject, polarity=1, embedding=embedding
    )


async def _run(store: InMemoryCuratorStore, fake: FakeEmbedder, **kwargs: float) -> LinkResult:
    return await link_observations(store, fake, child_id=CHILD, **kwargs)


def _by_id(result: LinkResult) -> dict[str, tuple[str, str | None]]:
    return {o.key[1]: (o.status, o.reason) for o in result.outcomes}


async def test_한_번의_호출로_임베딩되고_연결된다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")
    _observe(store, "2", "생딸기")
    _observe(store, "3", "레고")
    fake = FakeEmbedder()

    result = await _run(store, fake)

    assert fake.calls == [["딸기", "생딸기", "레고"]]
    assert result.embed_calls == 1
    assert [(o.key[1], o.status, o.match) for o in result.outcomes] == [
        ("1", "created", "new"),
        ("2", "linked", "similar"),  # 딸기에 붙는다
        ("3", "created", "new"),
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
        child_id=CHILD, domain="food", merge_key="생딸기", polarity=1, embedding=None
    )
    _observe(store, "1", "딸기")

    result = await _run(store, FakeEmbedder())

    [held] = result.held
    assert (held.reason, held.invalid_profile_ids) == ("invalid_profile_embedding", (broken.id,))


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


@pytest.mark.parametrize("threshold", [math.nan, 1.5])
async def test_잘못된_threshold_는_API_를_부르기_전에_거절한다(threshold: float) -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")
    fake = FakeEmbedder()

    with pytest.raises(ValueError, match="threshold"):
        await _run(store, fake, threshold=threshold)

    assert fake.calls == []
    assert store.observation("food", "1").embedding is None


async def test_대상이_없으면_아무것도_하지_않는다() -> None:
    fake = FakeEmbedder()
    result = await _run(InMemoryCuratorStore(), fake)

    assert result.outcomes == ()
    assert result.embed_calls == 0
    assert fake.calls == []


async def test_두_번_불러도_같은_일을_다시_하지_않는다() -> None:
    store = InMemoryCuratorStore()
    _observe(store, "1", "딸기")
    await _run(store, FakeEmbedder())

    fake = FakeEmbedder()
    again = await _run(store, fake)

    assert again.outcomes == ()
    assert fake.calls == []
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
