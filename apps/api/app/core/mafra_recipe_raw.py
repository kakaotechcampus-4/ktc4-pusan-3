"""농식품정보원 레시피 원문 계약. 클라이언트(`integrations/mafra/`)가 싣고 레시피 정리가 읽는다.

`app/integrations/` 는 core 만 import 할 수 있어서 클라이언트와 정리 단계가 함께 보는 모양은 여기
둔다(`core/mfds_nutri_raw.py` 와 같은 자리). 값은 원문 문자열이다 — 클라이언트는 기본정보와 재료를
`RECIPE_ID` 로 잇기까지만 하고 해석하지 않는다. 재료명 정리(오타 · 부위명 → 19종 코드) · 식품군 ·
`stage_min` 은 정리 단계(AI)가 맡는다.

🚨 이용 조건(상업 이용 · 변경)을 확인했다. 원문은 정리 표(`reference/recipe_ingredients.yaml`)와
   `recipe_catalog` 로만 들어간다 — 테스트는 여전히 가짜 행으로 쓴다.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RawIngredient:
    """재료 한 행. 이름이 비면 None — 버리지 않는다.

    이름이 빈 행을 버리면 그 레시피가 재료를 다 아는 것처럼 보여 알레르기를 놓칠 수 있다. None 으로
    남기면 정리 단계가 "정리 못 한 재료" 로 보고 그 레시피를 `resolved=false` 로 둔다.
    """

    name: str | None  # IRDNT_NM
    amount: str | None = None  # IRDNT_CPCTY — "4컵" 같은 원문
    role: str | None = None  # IRDNT_TY_NM — 주재료 · 부재료 · 양념


@dataclass(frozen=True)
class RawRecipe:
    """레시피 기본정보 한 행 + 그 레시피의 재료(순번 순)."""

    recipe_id: str  # RECIPE_ID — 정수로 와도 글자로 싣는다
    name: str  # RECIPE_NM_KO
    nation: str | None = None  # NATION_NM — 한식 · 중국 · 일본 · 서양 …
    kind: str | None = None  # TY_NM — 밥 · 찌개 · 볶음 …
    ingredients: tuple[RawIngredient, ...] = ()


@dataclass(frozen=True)
class RecipeSync:
    """전체 동기화 한 번의 결과. 센 값은 PR · 운영 확인에 쓴다(원문 없이 건수만)."""

    recipes: tuple[RawRecipe, ...]
    recipe_rows: int  # 받은 기본정보 행
    ingredient_rows: int  # 받은 재료 행 (버린 행 포함)
    orphan_ingredient_rows: int  # 기본정보가 없어 버린 재료 행
    unnamed_ingredient_rows: int  # 이름이 비어 "모르는 재료" 로 남긴 행
