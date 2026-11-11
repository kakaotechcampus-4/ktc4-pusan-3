"""서버가 뜰 때 "꺼져 있는 기능" 을 한 줄로 남긴다.

설정이 비면 그 기능만 꺼지고 서버는 뜬다 (`core/config.py` 의 칸마다 그렇게 적혀 있다). 그래서
배포는 성공으로 끝나고, 무엇이 꺼진 채 도는지는 흩어진 로그를 뒤져야 알았다 — Curator 는 debug
한 줄, 날씨 · 도서는 로그도 없었다. 뜰 때 한 번 모아서 남긴다.

🚨 서버를 막지 않는다. 알리기만 한다 — 키 하나가 빠졌다고 운영이 안 뜨면 그쪽이 더 큰 사고다.
   확인하다 예외가 나도 삼킨다 (`log_at_startup`).
🚨 설정 이름만 싣는다. 값(키 · 주소 · 파일 경로)은 싣지 않는다.
🚨 서버에서 LLM · Curator 가 비면 ERROR 다 — Discord 로 간다. 재시작마다 한 건씩 온다.
"""

import logging
from collections.abc import Sequence
from typing import Protocol

from app.agents import entrypoint

log = logging.getLogger(__name__)

# 서버(prod · dev)에서는 경고, 노트북(local)에서는 안내로 남긴다 — 로컬은 꺼진 게 많은 게 정상이다
_SERVERS = ("prod", "dev")


class _Settings(Protocol):
    """이 모듈이 읽는 설정 칸. `core.config.Settings` 가 그대로 맞는다."""

    APP_ENV: str
    DATA_GO_KR_SERVICE_KEY: str
    DATA4LIBRARY_AUTH_KEY: str
    MEAL_OCR_BASE_URL: str | None
    MEAL_OCR_API_KEY: str | None
    PHOTO_UPLOAD_ACCEPT: bool
    PUSH_DEVICE_ACCEPT: bool
    FCM_SERVICE_ACCOUNT_FILE: str
    REDIS_URL: str | None

    @property
    def alert_webhook_url(self) -> str | None: ...


def _blank(value: object) -> bool:
    return not str(value or "").strip()


def disabled_features(settings: _Settings, *, llm_gaps: Sequence[str]) -> list[str]:
    """꺼져 있는 기능을 "무엇이 (어느 설정 때문에)" 로 적은 목록. 다 켜져 있으면 빈 목록.

    LLM 빈 자리(`entrypoint.llm_gaps`)를 맨 앞에 둔다 — 한 줄 입력이 통째로 실패하는 쪽이다.
    """
    found = list(llm_gaps)
    if _blank(settings.DATA_GO_KR_SERVICE_KEY):
        found.append("날씨 · 행사 · 식품 영양성분 조회 (DATA_GO_KR_SERVICE_KEY 없음)")
    if _blank(settings.DATA4LIBRARY_AUTH_KEY):
        found.append("도서 추천 (DATA4LIBRARY_AUTH_KEY 없음)")
    ocr = [n for n in ("MEAL_OCR_BASE_URL", "MEAL_OCR_API_KEY") if _blank(getattr(settings, n))]
    if ocr:
        # 키가 없으면 읽기만이 아니라 받기도 503 이다 (`routers/photos.py`)
        found.append(f"사진 읽기 · 받기 — 올리면 503 ({' / '.join(ocr)} 없음)")
    if not settings.PHOTO_UPLOAD_ACCEPT:
        found.append("사진 받기 — 올리면 503 (PHOTO_UPLOAD_ACCEPT=false)")
    if not settings.PUSH_DEVICE_ACCEPT:
        found.append("알림 기기 등록 — 토큰을 저장하지 않는다 (PUSH_DEVICE_ACCEPT=false)")
    if _blank(settings.FCM_SERVICE_ACCOUNT_FILE):
        found.append("알림 발송 (FCM_SERVICE_ACCOUNT_FILE 없음)")
    if _blank(settings.REDIS_URL):
        found.append("Redis — 프로세스 메모리로 대신한다 (REDIS_URL 없음)")
    if settings.alert_webhook_url is None:
        found.append("오류 Discord 알림 (ALERT_WEBHOOK_URL 없음, local 은 늘 꺼짐)")
    return found


def log_disabled_features(settings: _Settings, *, llm_gaps: Sequence[str]) -> None:
    found = disabled_features(settings, llm_gaps=llm_gaps)
    if not found:
        log.info("꺼져 있는 기능 없음")
        return
    if settings.APP_ENV not in _SERVERS:
        log.info("꺼져 있는 기능 %d개 — %s", len(found), " | ".join(found))
        return
    # LLM · Curator 가 비면 기억이 조용히 안 쌓인다 — 오류 없이 돌아 아무도 모른다. 그래서 ERROR 로
    # 올려 Discord 에 보낸다 (alerts.py 는 ERROR 이상만). 나머지는 일부러 끈 경우가 있어 경고로 둔다
    if llm_gaps:
        log.error(
            "꺼져 있는 기능 %d개 — %s",
            len(found),
            " | ".join(found),
            extra={
                "alert_heading": "LLM · Curator 설정이 빠진 채 서버가 떴다",
                "alert_detail": " | ".join(llm_gaps),  # 설정 이름만 — 값은 없다
            },
        )
        return
    log.warning("꺼져 있는 기능 %d개 — %s", len(found), " | ".join(found))


def log_at_startup(settings: _Settings) -> None:
    """`main.py` 가 뜰 때 한 번 부른다. 어떤 예외도 밖으로 내지 않는다.

    알리려다 서버를 못 띄우면 안 된다 — LLM 설정을 읽는 데서 예외가 나도(값 형식 오류 등) 지금까지는
    첫 입력에서 드러났다. 여기서 터지면 부팅이 막혀 동작이 달라진다.
    """
    try:
        log_disabled_features(settings, llm_gaps=entrypoint.llm_gaps())
    except Exception as exc:
        log.warning("꺼져 있는 기능을 확인하지 못했다 (%s)", type(exc).__name__)
