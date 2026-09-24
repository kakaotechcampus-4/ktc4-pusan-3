"""임베딩 단계 테스트. 가짜 Embedder 와 인메모리 저장소로 돈다.

- 벡터가 없는 active 관찰만 보낸다 (이미 있는 행은 재처리하지 않는다)
- 요청은 한 번, 같은 subject 는 한 번만 보낸다
- 관찰마다 자기 subject 의 벡터가 저장된다
- 실패하면 아무것도 저장하지 않고 다음 실행에서 다시 대상이 된다
- LLMError 가 아닌 예외는 삼키지 않는다
- 빈 subject 는 보내지 않는다
"""

from collections.abc import Sequence
from uuid import UUID

import pytest

from app.agents.common.llm_client import LLMUnavailableError
from app.agents.curator.embedding.embed_step import embed_pending
from app.agents.curator.embedding.inmemory import InMemoryCuratorStore

CHILD = UUID("00000000-0000-0000-0000-000000000001")
OTHER_CHILD = UUID("00000000-0000-0000-0000-000000000002")
EXISTING = [9.0, 9.0]


class FakeEmbedder:
    """subject 마다 다른 벡터를 돌려준다. 받은 요청을 기록한다."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self._error = error

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self._error is not None:
            raise self._error
        return [self.vector(text) for text in texts]

    @staticmethod
    def vector(text: str) -> list[float]:
        return [float(len(text)), float(sum(map(ord, text)) % 1000)]


def _store() -> InMemoryCuratorStore:
    store = InMemoryCuratorStore()
    store.add_observation(child_id=CHILD, domain="food", id="1", subject="딸기")
    store.add_observation(child_id=CHILD, domain="activity", id="2", subject="레고")
    store.add_observation(child_id=CHILD, domain="food", id="3", subject="딸기")
    return store


async def test_관찰마다_자기_subject_의_벡터가_저장된다() -> None:
    store = _store()
    fake = FakeEmbedder()

    result = await embed_pending(store, fake, child_id=CHILD)

    assert result.embedded == (("food", "1"), ("activity", "2"), ("food", "3"))
    assert result.failed == result.skipped == ()
    assert store.observation("food", "1").embedding == FakeEmbedder.vector("딸기")
    assert store.observation("activity", "2").embedding == FakeEmbedder.vector("레고")
    assert store.observation("food", "3").embedding == FakeEmbedder.vector("딸기")


async def test_요청은_한_번이고_같은_subject_는_한_번만_보낸다() -> None:
    fake = FakeEmbedder()
    result = await embed_pending(_store(), fake, child_id=CHILD)

    assert fake.calls == [["딸기", "레고"]]  # 처음 나온 순서
    assert result.requests == 1


async def test_앞뒤_공백만_다른_subject_는_같은_것으로_보낸다() -> None:
    store = InMemoryCuratorStore()
    store.add_observation(child_id=CHILD, domain="food", id="1", subject="딸기")
    store.add_observation(child_id=CHILD, domain="food", id="2", subject=" 딸기 ")
    fake = FakeEmbedder()

    await embed_pending(store, fake, child_id=CHILD)

    assert fake.calls == [["딸기"]]
    assert store.observation("food", "2").embedding == FakeEmbedder.vector("딸기")


async def test_이미_벡터가_있는_관찰은_보내지_않는다() -> None:
    store = _store()
    store.add_observation(
        child_id=CHILD, domain="education", id="4", subject="한글 자모", embedding=EXISTING
    )
    fake = FakeEmbedder()

    result = await embed_pending(store, fake, child_id=CHILD)

    assert "한글 자모" not in fake.calls[0]
    assert ("education", "4") not in result.embedded
    assert store.observation("education", "4").embedding == EXISTING  # 덮어쓰지 않는다


async def test_다른_아이와_active_가_아닌_관찰은_건드리지_않는다() -> None:
    store = InMemoryCuratorStore()
    store.add_observation(child_id=CHILD, domain="food", id="1", subject="딸기")
    store.add_observation(child_id=OTHER_CHILD, domain="food", id="2", subject="우유")
    store.add_observation(
        child_id=CHILD, domain="food", id="3", subject="두유", status="stand_alone"
    )
    store.add_observation(
        child_id=CHILD, domain="food", id="4", subject="바나나", status="inactive"
    )
    fake = FakeEmbedder()

    result = await embed_pending(store, fake, child_id=CHILD)

    assert fake.calls == [["딸기"]]
    assert result.embedded == (("food", "1"),)
    for id in ("2", "3", "4"):
        assert store.observation("food", id).embedding is None


async def test_대상이_없으면_API_를_부르지_않는다() -> None:
    fake = FakeEmbedder()
    result = await embed_pending(InMemoryCuratorStore(), fake, child_id=CHILD)

    assert fake.calls == []
    assert result.requests == 0
    assert result.embedded == result.failed == result.skipped == ()


async def test_실패하면_아무것도_저장하지_않고_다음_실행에서_다시_대상이_된다() -> None:
    store = _store()

    failed = await embed_pending(store, FakeEmbedder(LLMUnavailableError("down")), child_id=CHILD)

    assert failed.failed == (("food", "1"), ("activity", "2"), ("food", "3"))
    assert failed.embedded == ()
    assert await store.list_unlinked(child_id=CHILD) == []  # 벡터가 하나도 안 들어갔다

    retried = await embed_pending(store, FakeEmbedder(), child_id=CHILD)
    assert retried.embedded == failed.failed


async def test_LLMError_가_아닌_예외는_그대로_올라온다() -> None:
    with pytest.raises(ValueError):
        await embed_pending(_store(), FakeEmbedder(ValueError("bug")), child_id=CHILD)


async def test_빈_subject_는_보내지_않고_나머지는_저장한다() -> None:
    store = _store()
    store.add_observation(child_id=CHILD, domain="food", id="blank", subject="  ")
    fake = FakeEmbedder()

    result = await embed_pending(store, fake, child_id=CHILD)

    assert result.skipped == (("food", "blank"),)
    assert fake.calls == [["딸기", "레고"]]
    assert store.observation("food", "blank").embedding is None
    assert store.observation("food", "1").embedding is not None


async def test_빈_subject_만_있으면_API_를_부르지_않는다() -> None:
    store = InMemoryCuratorStore()
    store.add_observation(child_id=CHILD, domain="food", id="blank", subject="")
    fake = FakeEmbedder()

    result = await embed_pending(store, fake, child_id=CHILD)

    assert fake.calls == []
    assert result.skipped == (("food", "blank"),)
    assert result.requests == 0
