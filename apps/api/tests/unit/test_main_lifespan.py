"""서버 시작 때 만드는 외부 어댑터 (main.lifespan). 부르지 않는다 — 만들고 닫히는지만 본다."""

import pytest

from app import main
from app.core.config import settings
from app.integrations.hospital.client import NmcHospitals


async def test_공공데이터_키가_있으면_병원_어댑터를_만든다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", "test-key")

    async with main.lifespan(main.app):
        assert isinstance(main.app.state.health_places, NmcHospitals)


@pytest.mark.parametrize("key", ["", "   "])
async def test_공공데이터_키가_없으면_병원_어댑터는_None_이다(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    # 키 없이 만들면 부를 때마다 UpstreamUnavailable 이라 결과는 같지만, 날씨와 같은 규칙으로 둔다
    monkeypatch.setattr(settings, "DATA_GO_KR_SERVICE_KEY", key)

    async with main.lifespan(main.app):
        assert main.app.state.health_places is None
