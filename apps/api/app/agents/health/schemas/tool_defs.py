"""모델에게 보이는 Health tool 의 이름 · 호출 조건 · argument 모델.

아직 하나도 없다 — tool 을 만드는 이슈가 정의와 핸들러를 같이 더한다.
`registry.MODEL_TOOLS` 는 명세 §1 전체를 이미 담고 있고, 열리는 것은 정의와 핸들러가 둘 다 있는
tool 뿐이다. description 에는 '무엇을 하는가'보다 '언제 부르는가'를 적는다.
"""

from app.agents.common.tool_schema import ToolDefinition

TOOL_DEFINITIONS: list[ToolDefinition] = []
