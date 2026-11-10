"""화면 가이드를 본 기록 요청 · 응답 (#696)."""

from typing import Literal, get_args

from pydantic import Field

from app.api.schema_base import ApiSchema

TourKey = Literal[
    "home-empty",
    "home",
    "chat",
    "suggestions",
    "memories",
    "calendar",
    "profile",
    "growth",
    "settings",
]
"""가이드 이름 = 화면 하나. 프론트 `apps/web/src/lib/tour/types.ts` 의 `TOUR_KEYS` 와 같은 값이다.

🚨 화면이 늘면 여기에 더한다 (표는 text 라 마이그레이션이 없다). 이름은 바꾸지 않는다 — 바꾸면
   이미 본 보호자에게 다시 뜬다 (`docs/web/screen-tour-v1.md` §2).
"""

TOUR_KEYS: frozenset[str] = frozenset(get_args(TourKey))


class SeenTours(ApiSchema):
    seen: list[TourKey] = Field(
        description=(
            "이 보호자가 이미 본 가이드. 처음 본 순서대로. 여기 있는 화면은 들어와도 "
            "가이드를 저절로 띄우지 않는다. `home-empty` 는 기록이 없을 때의 홈, "
            "`home` 은 기록이 생긴 뒤의 홈이다"
        )
    )
