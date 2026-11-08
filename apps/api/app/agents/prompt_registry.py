"""프롬프트 10곳을 한 자리에 불러 모은다 (#770).

각 Agent 는 자기 프롬프트를 `register_prompt` 로 등록한다 — 그 모듈이 import 될 때다. 평가 문서
검사(`test_prompt_history.py`)와 `make test-prompt` 는 10곳 전부가 필요하므로 여기서 한꺼번에
import 한다. 서버 실행 중에는 필요 없다 — 프롬프트를 만드는 모듈이 이미 import 돼 있다.

급식표 · 알림장 OCR 은 `providers/` 에 있어 agents 를 import 하지 못한다(레이어 경계). 그래서
그쪽 모듈은 지문 입력(`prompt_parts`)만 내놓고 등록은 여기서 한다.

새 프롬프트를 더하면 여기 `PROMPT_NAMES` 와 import 를 함께 고친다 — 평가 문서
(`docs/agents/eval/`)도 한 장 더한다.
"""

import app.agents.activity.event_schedule  # noqa: F401  (activity_event_schedule 등록)
import app.agents.activity.prompt  # noqa: F401  (activity 등록)
import app.agents.curator.embedding.judge  # noqa: F401  (curator_judge 등록)
import app.agents.food.prompt  # noqa: F401  (food 등록)
import app.agents.growth.prompt  # noqa: F401  (growth 등록)
import app.agents.health.prompt  # noqa: F401  (health 등록)
import app.agents.memory.agent  # noqa: F401  (memory 등록 — 재시도 문구가 agent.py 에 있다)
import app.agents.supervisor.prompt  # noqa: F401  (supervisor 등록)
from app.agents.common.prompt_fingerprint import PromptPrint, prompt_fingerprint, register_prompt
from app.providers.meal_plan import image_ocr
from app.providers.notice import extractor

PROMPT_NAMES: tuple[str, ...] = (
    "supervisor",
    "memory",
    "food",
    "activity",
    "activity_event_schedule",
    "growth",
    "health",
    "curator_judge",
    "meal_ocr",
    "notice_ocr",
)

register_prompt("meal_ocr", image_ocr.prompt_parts)
register_prompt("notice_ocr", extractor.prompt_parts)


def all_fingerprints() -> dict[str, PromptPrint]:
    """10곳의 현재 지문. 등록이 빠진 이름이 있으면 KeyError."""
    return {name: prompt_fingerprint(name) for name in PROMPT_NAMES}
