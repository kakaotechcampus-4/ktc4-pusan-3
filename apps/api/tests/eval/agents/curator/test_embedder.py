"""실제 임베딩 API 호출. apps/api/.env 의 EMBEDDING_* 가 필요하다.

    pytest tests/eval/agents/curator/test_embedder.py -m live

설정·주소·모델이 맞는지만 본다. 유사도 임계값은 여기서 정하지 않는다.
"""

import pytest

from app.agents.curator.embedding.embedder import EMBEDDING_DIM, Embedder

pytestmark = pytest.mark.live


async def test_한_번에_여러_개를_1536차원으로_받는다() -> None:
    vectors = await Embedder().embed(["딸기", "레고", "한글 자모"])

    assert len(vectors) == 3
    assert all(len(v) == EMBEDDING_DIM for v in vectors)
