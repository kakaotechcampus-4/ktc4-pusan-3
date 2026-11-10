"""서버가 뜰 때 알릴 "LLM 을 부를 수 없는 자리" 판정 — `entrypoint.llm_gaps`.

키가 빠져도 서버는 뜬다. 그래서 배포는 성공으로 끝나고, 첫 입력에서야 `llm_misconfigured` 로
드러난다. 뜰 때 한 번 알리려면 "어디가 비었는가" 를 실제 폴백 규칙(`AgentSettings.profile`)과
같은 눈으로 봐야 한다 — 역할의 값이 비면 MEMORY_* 를 빌려 쓴다.
"""

import pytest

from app.agents import entrypoint
from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMConfigError
from app.agents.curator.embedding.embedder import Embedder
from app.agents.curator.embedding.jev import JevJudge
from app.core.agent_config import AGENT_ROLES, LLM_FIELDS

FILLED = "채움"
MEMORY = {"MEMORY_API_KEY": FILLED, "MEMORY_BASE_URL": FILLED, "MEMORY_MODEL": FILLED}
CURATOR = {
    f"{prefix}_{field}": FILLED
    for prefix in ("EMBEDDING", "CURATOR_JUDGE")
    for field in ("API_KEY", "BASE_URL", "MODEL")
}


def _settings(**values: str) -> AgentSettings:
    """모든 칸을 비운 데서 시작한다 — 노트북의 `.env` · 환경변수가 테스트에 섞이지 않게."""
    blank = {f"{role}_{field}": "" for role in AGENT_ROLES for field in LLM_FIELDS}
    blank |= dict.fromkeys(CURATOR, "")
    return AgentSettings(_env_file=None, **{**blank, **values})


@pytest.mark.parametrize("blank", sorted(CURATOR))
def test_Curator_빈_자리는_생성자가_거절하는_것과_같다(blank: str) -> None:
    """보고가 "꺼졌다" 고 하는 것과 실제로 못 만드는 것이 같은 조건이다 (#912 리뷰).

    둘이 조건을 따로 적으면 생성자만 바뀌었을 때 보고가 틀린 말을 한다.
    """
    settings = _settings(**MEMORY, **{**CURATOR, blank: ""})
    maker = Embedder if blank.startswith("EMBEDDING") else JevJudge

    with pytest.raises(LLMConfigError):
        maker(settings)

    gaps = entrypoint.llm_gaps(settings)
    assert len(gaps) == 1
    assert blank in gaps[0]


def test_Memory_와_Curator_가_채워져_있으면_빈_자리가_없다() -> None:
    # 다른 역할은 비어 있어도 MEMORY_* 를 빌려 쓴다
    assert entrypoint.llm_gaps(_settings(**MEMORY, **CURATOR)) == ()


def test_Memory_가_비면_여섯_역할을_한_줄로_묶어_알린다() -> None:
    gaps = entrypoint.llm_gaps(_settings(**CURATOR))

    assert len(gaps) == 1
    for role in ("memory", "supervisor", "food", "activity", "growth", "health"):
        assert role in gaps[0]
    for key in MEMORY:
        assert key in gaps[0]


def test_한_역할의_짝이_안_맞으면_그_역할만_알린다() -> None:
    # MODEL 만 있고 BASE_URL 이 없다 — profile 이 AgentConfigError 를 낸다
    gaps = entrypoint.llm_gaps(_settings(**MEMORY, **CURATOR, FOOD_MODEL=FILLED))

    assert len(gaps) == 1
    assert "food" in gaps[0]
    assert "FOOD_BASE_URL" in gaps[0]
    assert "supervisor" not in gaps[0]


def test_임베딩이_비면_Curator_가_꺼진다고_알린다() -> None:
    judge = {k: v for k, v in CURATOR.items() if k.startswith("CURATOR_JUDGE")}

    gaps = entrypoint.llm_gaps(_settings(**MEMORY, **judge))

    assert len(gaps) == 1
    assert "Curator" in gaps[0]
    assert "EMBEDDING_API_KEY" in gaps[0]


def test_판정기만_비면_판정만_꺼진다고_알린다() -> None:
    embedding = {k: v for k, v in CURATOR.items() if k.startswith("EMBEDDING")}

    gaps = entrypoint.llm_gaps(_settings(**MEMORY, **embedding))

    assert len(gaps) == 1
    assert "CURATOR_JUDGE_MODEL" in gaps[0]
    assert "EMBEDDING" not in gaps[0]


def test_키_이름만_싣고_값은_싣지_않는다() -> None:
    # 로그로 나가는 글이다 — 키 · 주소가 실리면 journald 에 남는다
    secret = "sk-비밀값"
    gaps = entrypoint.llm_gaps(
        _settings(MEMORY_API_KEY=secret, MEMORY_BASE_URL="https://llm.test/v1", **CURATOR)
    )

    assert gaps
    assert all(secret not in gap and "llm.test" not in gap for gap in gaps)
