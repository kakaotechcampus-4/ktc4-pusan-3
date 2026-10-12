"""공공데이터포털 호출 공통 — 인증키 · 타임아웃 · 실패 변환.

🚨 인증키는 주소(`serviceKey=`)에 실린다. httpx 오류의 문자열에는 요청 주소가 들어가므로
   그대로 올리거나 로그에 남기지 않는다. 실패는 종류 이름만 담은 `UpstreamUnavailable` 로 바꾸고
   원래 예외는 끊는다(`from None`) — traceback 에도 주소가 남지 않게.
"""

import logging
from typing import Any
from urllib.parse import unquote

import httpx

from app.core.config import settings
from app.core.weather_raw import UpstreamUnavailable

log = logging.getLogger(__name__)

# 연결 1.0s · 읽기 2.0s · 전체 2.5s, 재시도 0회 (설계 D8). 네 조회가 병렬이라 하나가 실패해도
# 나머지는 온다. 재시도는 예산을 두 배로 쓴다
CONNECT_TIMEOUT_S = 1.0
READ_TIMEOUT_S = 2.0
TOTAL_TIMEOUT_S = 2.5

TIMEOUT = httpx.Timeout(TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S)


def service_key() -> str:
    """`.env` 의 인증키. Encoding 키(`%2B` 등)면 한 번 푼다 — httpx 가 주소에 넣을 때 다시
    인코딩해서, 풀지 않으면 이중 인코딩으로 SERVICE_KEY_IS_NOT_REGISTERED 가 온다.

    비었으면 조회 실패와 같다. 기본값으로 넘기지 않는다.
    """
    key = settings.DATA_GO_KR_SERVICE_KEY.strip()
    if not key:
        raise UpstreamUnavailable("공공데이터포털 인증키가 없다 (DATA_GO_KR_SERVICE_KEY)")
    return unquote(key) if "%" in key else key


async def get_json(
    client: httpx.AsyncClient, url: str, params: dict[str, Any], *, what: str
) -> dict[str, Any]:
    """GET 한 번. HTTP 오류 · 타임아웃 · JSON 아님은 전부 `UpstreamUnavailable(what)` 이다.

    `what` 은 로그와 예외에 남는 조회 이름이다("단기예보"). 격자 · 주소를 넣지 않는다.
    """
    try:
        response = await client.get(
            url, params={**params, "serviceKey": service_key()}, timeout=TIMEOUT
        )
        response.raise_for_status()
        payload = response.json()
    except UpstreamUnavailable:
        raise
    except (httpx.HTTPError, ValueError) as error:
        log.warning("공공데이터 조회 실패 what=%s error=%s", what, type(error).__name__)
        raise UpstreamUnavailable(f"{what} 조회 실패 ({type(error).__name__})") from None
    if not isinstance(payload, dict):
        raise UpstreamUnavailable(f"{what} 응답이 JSON 객체가 아니다")
    return payload


def kma_result_code(payload: dict[str, Any]) -> str | None:
    """기상청 응답의 `response.header.resultCode`. 모양이 다르면 None.

    키 오류는 JSON 이 아니라 XML(`OpenAPI_ServiceResponse`)로 오기도 한다 — 그때는
    `get_json` 이 JSON 아님으로 이미 실패시켰다.
    """
    header = (payload.get("response") or {}).get("header") or {}
    code = header.get("resultCode")
    return str(code) if code is not None else None
