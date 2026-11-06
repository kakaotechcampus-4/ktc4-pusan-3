"""레시피 소스 받기 — 적재 · 정리 표 초안 · 검수 순서 스크립트가 같이 쓴다 (#510).

키 읽기와 클라이언트 만들기를 한 곳에 둔다. 세 스크립트에 따로 두면 타임아웃 · 키 검사를 바꿀 때
한 곳을 빠뜨린다. 클라이언트는 부를 때 불러온다 — 스크립트를 불러오기만 해서는 httpx 를 읽지 않는다.
"""

from app.core.mafra_recipe_raw import RecipeSync
from app.core.recipe_source import RecipeSource

# `--source` 이름 → 레시피 소스. 소스를 더하면 여기도 더한다(테스트가 묶는다)
SOURCE_NAMES: dict[str, RecipeSource] = {"mafra": "mafra_recipe", "nics": "nics_recipe"}


async def fetch_mafra() -> RecipeSync:
    """농식품정보원 레시피 전체. 키(`MAFRA_RECIPE_API_KEY`)가 없으면 클라이언트가 멈춘다."""
    from app.core.config import settings
    from app.integrations.mafra.client import MafraRecipeClient, mafra_recipe_http

    async with mafra_recipe_http() as http:
        client = MafraRecipeClient(api_key=settings.MAFRA_RECIPE_API_KEY, http=http)
        return await client.recipes_with_ingredients()


async def fetch_nics() -> RecipeSync:
    """농촌진흥청 이달의 식재료 레시피 전체. 키(`NICS_RECIPE_API_KEY`)가 없으면 클라이언트가
    멈춘다."""
    from app.core.config import settings
    from app.integrations.nics.client import NicsRecipeClient, nics_recipe_http

    async with nics_recipe_http() as http:
        client = NicsRecipeClient(api_key=settings.NICS_RECIPE_API_KEY, http=http)
        return await client.recipes_with_ingredients()


async def fetch(source: RecipeSource) -> RecipeSync:
    return await (fetch_nics() if source == "nics_recipe" else fetch_mafra())
