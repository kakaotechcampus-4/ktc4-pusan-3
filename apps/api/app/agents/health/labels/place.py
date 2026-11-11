"""`place_lookup` — 문 연 병원을 물으면 공식 창구와 119 를 알려 주고 끝난다. 모델 0회.

읽기 0 · 쓰기 0 이다. 병원을 직접 찾아 주지 않는다(위치 · 국립중앙의료원 목록). 위치 약관과 결과
화면을 붙일 시간이 없어 하지 않기로 했다(#926). 그 자리에는 응급의료포털(`place.portal`) 한 줄이
나간다 — 같은 안내를 어느 요청에나 글자 그대로 내므로 위치 · 아이 정보 · 모델이 모두 필요 없다.

건강정보 동의가 없으면 이 경로까지 오지 않는다. 닫힘은 `agent.run` ③ 이 다른 라벨과 같이 맡는다.
"""

from app.agents.common.llm_client import LLMClient
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.health.context import HealthContext
from app.agents.health.envelope import result
from app.agents.health.readouts import PLACE_PORTAL, READOUTS
from app.agents.health.schemas.task import HealthTaskType

TASK = HealthTaskType.PLACE_LOOKUP


async def run_place_lookup(
    task: DomainTask, context: HealthContext, client: LLMClient | None
) -> DomainAgentResult:
    return result(TASK, "completed", readouts=(READOUTS.render(PLACE_PORTAL),))
