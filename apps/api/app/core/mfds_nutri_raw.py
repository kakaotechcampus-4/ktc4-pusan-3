"""식약처 식품영양성분DB 원문 계약. 클라이언트(`app/integrations/`)가 싣고 Food 가 읽는 모양.

`app/integrations/` 는 core 만 import 할 수 있어서 클라이언트와 Agent 가 함께 보는 모양은 여기 둔다
(`core/weather_raw.py` 와 같은 자리). 값은 전부 API 원문 문자열이다 — 클라이언트는 호출 · 타임아웃 ·
필드 이름 옮기기까지만 하고 해석하지 않는다. 이름이 같은 행을 고르고(품목대표 · 상용제품) 숫자로
바꾸는 것은 Food `nutrition_source.py` 가 테스트와 함께 맡는다.

🚨 영양소 칸은 API 필드 이름(`AMT_NUM*` 등)이 아니라 **내부 이름**으로 싣는다. API 필드 이름이 어느
영양소인지 옮기는 표는 클라이언트가 갖고, 실제 응답 샘플로 계약 테스트를 건다 — 칼슘과 나트륨이
바뀌어도 숫자는 그럴듯해서 어디서도 안 걸린다. 내부 이름은 `NUTRIENT_KEYS` 다.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

# 메뉴를 해석하려면 다 차야 하는 7칸 + 있으면 쓰는 2칸. 영양 계산 설계 §6 의 항목이다.
# 당류는 비어 있는 행이 많아(김치볶음밥 · 멸치) 선택 칸이다 — 비었다고 메뉴를 떨어뜨리면 그 메뉴의
# 칼슘 · 단백질까지 합계에서 빠진다(10-11 결정). 영양 계산은 당류를 모르는 메뉴가 섞이면 당류를
# 판단하지 않고 안내만 낸다
CORE_NUTRIENT_KEYS: tuple[str, ...] = (
    "energy",
    "protein",
    "fat",
    "carbohydrate",
    "calcium",
    "iron",
    "sodium",
)
OPTIONAL_NUTRIENT_KEYS: tuple[str, ...] = ("sugars", "saturated_fat")
NUTRIENT_KEYS: tuple[str, ...] = CORE_NUTRIENT_KEYS + OPTIONAL_NUTRIENT_KEYS


@dataclass(frozen=True)
class RawNutriRow:
    """영양DB 한 행의 원문. 같은 이름이 여러 행이라 고르는 일이 Agent 몫이다."""

    food_code: str
    name: str  # 식품 이름 — 이름이 정확히 같은지는 Agent 가 본다
    db_group: str  # 음식 · 가공식품 · 원재료성 …
    db_class: str  # 품목대표 · 상용제품
    category: str | None = None  # 식품 대분류
    # 식품 대분류 코드(FOOD_CAT1_CD). 원재료성은 이름이 비고 코드만 오는 행이 있다(우유 · 달걀 ·
    # 닭고기). 코드 뜻은 DB 그룹마다 다르다 — 원재료성 09 는 육류, 음식 09 는 전·적 및 부침류다
    category_code: str | None = None
    ref_name: str | None = None  # 대표식품명
    source: str | None = None  # 출처
    researched_on: str | None = None  # 조사 일자 원문
    food_weight: str | None = None  # 식품중량 — 1인분 중량 원문 ("300g")
    nutrients: Mapping[str, str | None] = field(default_factory=dict)  # 100g 기준, 내부 이름
