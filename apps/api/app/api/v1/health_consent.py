"""건강정보 동의(`child_health`)가 없을 때 내는 오류.

알레르기 등록 · 고치기 · 추천 채택이 같이 쓴다 — 같은 코드 · 같은 상세(scopes + deeplink)로
동의 화면을 열게 하려는 것이다. ⚠️ 복약(`routers/medications.py`)은 아직 deeplink 없는 봉투를
따로 만든다 — 그쪽을 손볼 때 이 함수로 옮긴다.
"""

from app.api.errors import ApiError
from app.core import deeplinks
from app.domains.consent.models import ConsentScope


def health_consent_required() -> ApiError:
    """건강정보 동의가 없을 때. 동의는 10 설정에서 켠다 — 딥링크는 아이 id 를 담지 않는다 (#625)."""
    return ApiError(
        403,
        "consent_required",
        "건강 정보 동의가 필요해요",
        {"scopes": [ConsentScope.CHILD_HEALTH.value], "deeplink": deeplinks.CONSENT},
    )
