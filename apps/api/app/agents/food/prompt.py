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

식단 추천의 후보 목록은 system 이 아니라 user 메시지에 싣는다 — 샘플이 run 마다 바뀌어서 system 에
두면 [예시] 앞이 캐시에서 빠진다. 재호출은 같은 대화에 user 메시지를 더해 새 목록을 준다.
"""

from collections.abc import Sequence

from app.agents.food.context import FoodContext
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.store.ports import FoodDocRow, MenuCatalogRow

_ROLE = """
[역할]
너는 Child Memory 의 Food Agent 다. 주어진 task(식단 추천 또는 급식 갱신) 하나만 한다.
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
- [예시] 문서 행을 바탕으로 했으면 그 행의 id 를 kind=food_doc 으로 단다. 문서 행만 근거면 코드가
  일반 추천으로 분류한다.
- 근거가 없으면 evidence 를 비운다 — 코드가 고른 메뉴를 근거로 달아 일반 추천으로 분류한다.
  개인화인 척하지 않는다.
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
    FoodTaskType.DAYCARE_MEAL: (
        "update_daycare_meal · delete_daycare_meal · keep_daycare_meal 중 하나"
    ),
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
    if task_type == FoodTaskType.DAYCARE_MEAL:
        lines.append(
            "- 급식이 실제로 바뀌었다는 말이 아니면(묻는 말 · 가정이나 짐작 · 급식이 뭐였는지 "
            "알고 싶은 말) keep_daycare_meal 을 부른다. delete_daycare_meal 은 어린이집 · 유치원을 "
            "빠지거나 그 끼니가 없었다는 말일 때만 부른다."
        )
        lines.append(
            "- 바꿔 달라는 말이나 바뀌었다는 말이면 날짜 · 끼니 · 메뉴가 빠져 있어도 "
            "update_daycare_meal 을 부른다. 모르는 것은 코드가 되묻는다 — keep_daycare_meal 로 "
            "끝내지 않는다."
        )
    lines.append(f"- 마지막에 tool 을 한 번 부른다. 이번 task 의 출력 tool 은 {tool} 다.")
    return "\n".join(lines)


def _examples(examples: tuple[FoodDocRow, ...]) -> str:
    if not examples:
        return "[예시]\n(참고할 문서 없음)"
    lines = ["[예시] 결만 참고한다. 그대로 베끼지 않는다. 바탕으로 했으면 id 를 evidence 에 단다."]
    # 출처 기관을 같이 보여 준다 — 일부 행은 출처 표시가 이용 조건이다. 화면의 출처는 id 로 붙는다
    lines.extend(f"- id {doc.id} · {doc.title} ({doc.source_org}): {doc.body}" for doc in examples)
    return "\n".join(lines)


# 후보 한 줄에 싣는 재료 수. 이름만으로 고르기 어려운 메뉴를 가르는 데는 이 정도면 된다
_INGREDIENT_LIMIT = 8


def candidate_list(sample: Sequence[MenuCatalogRow]) -> str:
    """모델에게 보여 줄 후보 목록. menu_key · 이름 · 재료만 싣는다 — 영양 수치 · 알레르기는 없다."""
    lines = ["[후보 목록] 이 중에서만 menu_key 로 고른다."]
    for row in sample:
        ingredients = ", ".join(row.ingredients[:_INGREDIENT_LIMIT]) or "재료 모름"
        lines.append(f"- {row.menu_key} · {row.display_name} · 재료 {ingredients}")
    return "\n".join(lines)


def _request(request_texts: Sequence[str]) -> str:
    texts = [text for text in request_texts if text.strip()]
    return "\n".join(texts) if texts else "오늘 아이에게 먹일 식단을 추천해 줘."


def request_message(request_texts: Sequence[str], sample: Sequence[MenuCatalogRow]) -> str:
    """식단 추천의 처음 user 메시지 — 보호자의 요청 그대로와 코드가 뽑은 후보 목록."""
    return f"{_request(request_texts)}\n\n{candidate_list(sample)}"


def recall_message(
    request_texts: Sequence[str], *, avoid: Sequence[str], sample: Sequence[MenuCatalogRow]
) -> str:
    """안전 필터 뒤 재호출의 user 메시지. 빠진 이유는 말하지 않는다 (Tool_공통.md §5-2).

    `avoid` 는 이미 고른 것과 빠진 것의 `menu_key` 다 — 둘을 같은 말("다시 내지 않는다")로만 준다.
    이유를 알려 주면 단어만 바꿔 같은 메뉴를 다시 내는 법을 배운다. 앞의 후보 목록은 더 쓰지
    않는다 — 새 목록 밖 `menu_key` 는 출력 tool 이 거절한다.
    """
    exclusion = "\n".join(f"- {key}" for key in avoid)
    return (
        f"{_request(request_texts)}\n\n"
        "아래 메뉴는 다시 내지 않는다. 앞의 후보 목록은 더 쓰지 않고, 아래 새 목록에서 "
        "다른 메뉴로 3개를 새로 낸다.\n"
        f"{exclusion}\n\n"
        f"{candidate_list(sample)}"
    )


_SECTIONS = (_ROLE, _OUT_OF_SCOPE, _EVIDENCE, _DATE)


def build_system_prompt(
    context: FoodContext,
    task_type: FoodTaskType,
    examples: tuple[FoodDocRow, ...] = (),
) -> str:
    """system 메시지 본문.

    `examples` 는 `search_food_doc` 결과가 들어갈 자리다 — 모델에게 문서 검색 tool 은 없고,
    run 시작에 코드가 채워 넘긴다(Food_Tool_명세 §3). 식단 추천이 채우고, 문서가 없으면 `[예시]`
    구획이 "(참고할 문서 없음)" 으로 나간다. 현재 시각은 맨 뒤에 둔다 — now 는 run 마다 바뀌어서
    앞에 두면 그 뒤가 전부 프롬프트 캐시에서 빠진다(memory/prompt.py 와 같은 이유).
    """
    header = f"현재 시각은 {context.now.isoformat()} 이고 timezone 은 {context.timezone} 다."
    return "\n\n".join([*_SECTIONS, _instructions(task_type), _examples(examples), header])
