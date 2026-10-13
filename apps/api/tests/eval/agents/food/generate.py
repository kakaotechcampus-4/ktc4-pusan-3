"""Food 라이브 eval 이 FOOD_* 모델에게 시험 입력을 쓰게 하는 도우미.

모델이 쓴 글은 시험 데이터일 뿐이다 — 판정은 언제나 코드가 한다.
"""

from typing import Any

import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import BaseModel

from app.agents.common.config import AgentSettings
from app.agents.common.llm_client import LLMClient

SYSTEM = (
    "너는 한국 육아 앱의 Food 기능을 시험하는 데이터 생성기다. "
    "한국 가정 · 어린이집에서 실제로 쓰는 말투와 메뉴로 쓴다. 지시한 개수를 지킨다."
)


def require_food_model() -> None:
    """FOOD_* 키(없으면 MEMORY_*)가 없으면 건너뛴다."""
    try:
        api_key = AgentSettings().profile("food").api_key
    except Exception as exc:  # 설정 오류 문구에 값이 섞일 수 있어 예외 이름만 남긴다
        pytest.skip(f"FOOD 모델 설정을 읽지 못했다 ({type(exc).__name__})")
    if not api_key:
        pytest.skip("FOOD_API_KEY · MEMORY_API_KEY 가 없다 — apps/api/.env 에 넣고 돌린다")


async def ask(prompt: str, model: type[BaseModel]) -> Any:
    """구조화 출력으로 한 번 묻는다."""
    schema = to_strict_json_schema(model)
    response = await LLMClient(AgentSettings(), role="food").chat(
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": model.__name__, "schema": schema, "strict": True},
        },
    )
    return model.model_validate_json(response.message.content or "{}")
