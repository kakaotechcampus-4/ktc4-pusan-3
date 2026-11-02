"""서버가 내는 딥링크는 웹에 실제로 있는 아이 경로만 (#625).

딥링크는 아이 id 를 담지 않은 상대 경로다. 웹이 `/child/{cid}/` 뒤에 붙인다.
예전 `settings/health-safety` 는 웹에 없는 경로였고, 목과 문서도 서로 다른 값을 썼다.
"""

from pathlib import Path

import pytest

from app.agents.supervisor.routing import _AGENT_LIMIT, _GUIDANCE
from app.api.v1.routers.suggestions import _GUARD_DEEPLINKS
from app.core import deeplinks

# 허용 목록 — 웹 App Router 의 아이 경로(`app/child/[childId]/` 아래)와 같아야 한다
ALLOWED = frozenset({"profile", "profile/growth", "settings"})

_WEB_CHILD_ROUTES = Path(__file__).parents[3] / "web" / "src" / "app" / "child" / "[childId]"


def _emitted() -> set[str]:
    """서버가 내보내는 딥링크 전부 — 입력 run 안내 · 05 guard · 동의 오류."""
    links = {g.deeplink for g in (*_GUIDANCE.values(), _AGENT_LIMIT)}
    links |= set(_GUARD_DEEPLINKS.values())
    links.add(deeplinks.CONSENT)
    return {link for link in links if link is not None}


def test_서버가_내는_딥링크는_허용_목록_안이다() -> None:
    assert _emitted() <= ALLOWED


def test_딥링크_상수는_전부_허용_목록_안이다() -> None:
    assert set(deeplinks.ALL) <= ALLOWED


@pytest.mark.parametrize("path", sorted(ALLOWED))
def test_허용_목록은_웹에_있는_경로다(path: str) -> None:
    if not _WEB_CHILD_ROUTES.is_dir():
        pytest.skip("apps/web 이 없는 체크아웃")
    assert (_WEB_CHILD_ROUTES / path / "page.tsx").is_file()


def test_딥링크는_아이_id_도_앞_슬래시도_담지_않는다() -> None:
    for link in _emitted():
        assert not link.startswith("/")
        assert "child" not in link.split("/")


def test_알레르기_안내와_05_알레르기_guard_는_같은_곳으로_보낸다() -> None:
    assert _GUIDANCE["safety_record"].deeplink == _GUARD_DEEPLINKS["safety_unknown"] == "profile"


def test_키_몸무게_안내는_성장_기록으로_보낸다() -> None:
    assert _GUIDANCE["growth_record"].deeplink == "profile/growth"
