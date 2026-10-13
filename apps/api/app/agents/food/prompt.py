"""Food Agent의 system prompt.

memory/prompt.py 와 같은 방식으로 구획 상수를 이어 붙이고, 날짜는 현재 시각만 준다.
tool이 못 막는 것을 막는 자리다. 코드로 막을 수 있는 것은 registry·후처리가 막는다.

🚨 알레르기·금지식품 라벨은 두 task 모두 프롬프트에 넣지 않는다(Food_Tool_명세 §1, 10-11 갱신).
후보 풀 · 식품군 안내는 이미 `filter_food_safety` 를 거친 뒤에 모델에게 가므로 모델이 라벨
자체를 알 필요가 없다 — 알레르기 판정이 코드 몫이라는 원칙과 같다(루트 CLAUDE.md §3).

구획 순서 (고정 5 + 동적 2)
  고정  [역할] → [하지 않는 것] → [근거] → [날짜] → [지시]
  동적  [예시] → 현재 시각
[예시]를 맨 뒤에 두는 이유는 memory/prompt.py 의 현재 시각과 같다 — run 마다 바뀌는 구간을
뒤에 둬야 앞부분이 프롬프트 캐시에 남는다(RAG_plan §3).
"""

from app.agents.food.context import FoodContext
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store.ports import FoodDocRow

_ROLE = """
[역할]
너는 Child Memory 의 Food Agent 다. 주어진 task(식단 추천 또는 영양소 분석) 하나만 한다.
""".strip()

_OUT_OF_SCOPE = """
[하지 않는 것]
- 질병·영양 결핍을 진단하지 않는다.
- 치료식을 처방하지 않는다. 약·영양제를 권하지 않는다.
- 정확한 영양 수치를 만들지 않는다. 식단에 담긴 영양소의 비중만 말한다.
  수치(%·g·mg·kcal)는 만들지도 쓰지도 않는다.
- 알레르기를 추론하거나 확정하지 않는다. 주어진 알레르기·금지식품 목록 밖을 판단하지 않는다.
- 정확한 레시피를 쓰지 않는다.
""".strip()

_EVIDENCE = """
[근거]
- evidence 에는 이번 대화의 조회 결과에 있던 kind·id 만 쓴다. 지어내지 않는다.
- 근거가 없으면 evidence 를 비운다 — 코드가 일반 추천으로 분류한다. 개인화인 척하지 않는다.
- 6개월 지난 선호(is_stale)는 단독 근거로 쓰지 않는다.
- 근거가 없어 되물을 때는 질문을 하나만 한다.
""".strip()

_DATE = """
[날짜]
- 날짜를 계산하지 않는다.
  기간은 today / yesterday / last_3_days / this_week / last_7_days 라벨로만 고른다.
""".strip()

_OUTPUT_TOOL: dict[FoodTaskType, str] = {
    FoodTaskType.MEAL_RECOMMENDATION: "propose_meal_candidates",
    FoodTaskType.NUTRIENT_ANALYSIS: "report_nutrient_analysis",
    FoodTaskType.DAYCARE_MEAL: "update_daycare_meal 또는 delete_daycare_meal",
}


def _instructions(task_type: FoodTaskType) -> str:
    tool = _OUTPUT_TOOL[task_type]
    lines = [
        "[지시]",
        '- 숫자 뒤에 %·g·mg·kcal 이 붙은 표현과 "퍼센트"·"칼로리" 를 쓰지 않는다. '
        "구간(적은 편 · 적당 · 많은 편)으로만 말한다.",
        "- 수치는 내부 계산에만 쓴다. 화면에는 구간과 식품군만 나간다.",
    ]
    if task_type == FoodTaskType.MEAL_RECOMMENDATION:
        lines.append(
            "- 메뉴는 보여 준 후보 목록에서 menu_key 로만 고른다. 메뉴 이름과 재료를 직접 쓰지 "
            "않는다 — 코드가 채운다. 목록에 없는 menu_key 는 거절된다."
        )
        lines.append(
            "- reason 에는 이 아이에게 맞는 이유와 지금인 이유를 한두 문장으로 쓴다. 싫어하는 것을 "
            "피해 골랐으면 무엇을 피했는지 쓴다. 몸 상태나 수치는 쓰지 않는다."
        )
    if task_type == FoodTaskType.NUTRIENT_ANALYSIS:
        lines.append(
            "- findings 의 주어는 아이가 아니라 먹은 식사다. '기록된 식사를 보면 …' · "
            "'최근 식단에서 …' 처럼 쓴다. '아이 몸' · '부족' · '모자라' 같은 말은 쓰지 않는다."
        )
        lines.append(
            "- compare_diet_balance 가 준 food_group_actions 에 없는 음식을 제안하지 않는다. "
            "안내를 넣을 때는 그 문구를 그대로 쓴다(바꿔 쓰면 거절될 수 있다)."
        )
    lines.append(f"- 마지막에 tool 을 한 번 부른다. 이번 task 의 출력 tool 은 {tool} 다.")
    return "\n".join(lines)


def _examples(examples: tuple[FoodDocRow, ...]) -> str:
    if not examples:
        return "[예시]\n(참고할 문서 없음)"
    body = "\n".join(f"- {doc.title}: {doc.body}" for doc in examples)
    return f"[예시]\n{body}"


_SECTIONS = (_ROLE, _OUT_OF_SCOPE, _EVIDENCE, _DATE)


def build_system_prompt(
    context: FoodContext,
    task_type: FoodTaskType,
    examples: tuple[FoodDocRow, ...] = (),
) -> str:
    """system 메시지 본문.

    `examples` 는 `search_food_doc` 결과가 들어갈 자리다 — 모델에게 문서 검색 tool 은 없고,
    run 시작에 코드가 채워 넘기는 모양으로 설계했다(Food_Tool_명세 §3). 🚨 `agent.run()` 은
    아직 이 인자를 채우지 않는다(#321 파트 2 범위 밖, final-review Important #4) — 지금은
    항상 빈 튜플이 들어와 `[예시]` 구획이 "(참고할 문서 없음)" 으로만 나간다. 현재 시각은
    맨 뒤에 둔다 — now 는 run 마다 바뀌어서 앞에 두면 그 뒤가 전부 프롬프트 캐시에서
    빠진다(memory/prompt.py 와 같은 이유).
    """
    header = f"현재 시각은 {context.now.isoformat()} 이고 timezone 은 {context.timezone} 다."
    return "\n\n".join([*_SECTIONS, _instructions(task_type), _examples(examples), header])
