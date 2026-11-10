"""서버가 뜰 때 "꺼져 있는 기능" 을 경고 한 줄로 남긴다 — `app/api/startup_report.py`.

설정이 비면 기능이 조용히 꺼진다(서버는 뜬다). Curator 는 debug 로그 한 줄, 날씨 · 도서는 로그도
없었다. 배포가 성공으로 끝난 뒤에 무엇이 꺼진 채 도는지 한 곳에서 보이게 한다.
"""

import logging
from types import SimpleNamespace

import pytest

from app.api import startup_report

ALL_ON = {
    "APP_ENV": "prod",
    "DATA_GO_KR_SERVICE_KEY": "채움",
    "DATA4LIBRARY_AUTH_KEY": "채움",
    "MEAL_OCR_BASE_URL": "https://ocr.test/v1",
    "MEAL_OCR_API_KEY": "채움",
    "PHOTO_UPLOAD_ACCEPT": True,
    "PUSH_DEVICE_ACCEPT": True,
    "FCM_SERVICE_ACCOUNT_FILE": "/run/secrets/fcm.json",
    "REDIS_URL": "redis://redis.test:6379/0",
    "alert_webhook_url": "https://discord.test/hook",
}


def _settings(**over: object) -> SimpleNamespace:
    return SimpleNamespace(**{**ALL_ON, **over})


def test_다_켜져_있으면_목록이_비어_있다() -> None:
    assert startup_report.disabled_features(_settings(), llm_gaps=()) == []


@pytest.mark.parametrize(
    ("off", "named"),
    [
        ({"DATA_GO_KR_SERVICE_KEY": "  "}, "DATA_GO_KR_SERVICE_KEY"),
        ({"DATA4LIBRARY_AUTH_KEY": ""}, "DATA4LIBRARY_AUTH_KEY"),
        ({"MEAL_OCR_API_KEY": None}, "MEAL_OCR_API_KEY"),
        ({"MEAL_OCR_BASE_URL": None}, "MEAL_OCR_BASE_URL"),
        ({"PHOTO_UPLOAD_ACCEPT": False}, "PHOTO_UPLOAD_ACCEPT"),
        ({"PUSH_DEVICE_ACCEPT": False}, "PUSH_DEVICE_ACCEPT"),
        ({"FCM_SERVICE_ACCOUNT_FILE": ""}, "FCM_SERVICE_ACCOUNT_FILE"),
        ({"REDIS_URL": None}, "REDIS_URL"),
        ({"alert_webhook_url": None}, "ALERT_WEBHOOK_URL"),
    ],
)
def test_하나가_꺼지면_그_설정_이름과_함께_한_줄이_나온다(off: dict, named: str) -> None:
    found = startup_report.disabled_features(_settings(**off), llm_gaps=())

    assert len(found) == 1
    assert named in found[0]


def test_LLM_빈_자리가_맨_앞에_온다() -> None:
    # 한 줄 입력이 통째로 실패하는 쪽이라 먼저 보여야 한다
    found = startup_report.disabled_features(
        _settings(PHOTO_UPLOAD_ACCEPT=False), llm_gaps=("LLM memory (MEMORY_MODEL 없음)",)
    )

    assert found[0] == "LLM memory (MEMORY_MODEL 없음)"
    assert len(found) == 2


def test_설정_값은_싣지_않는다() -> None:
    # 켜져 있는 쪽의 주소 · 키가 목록에 딸려 나오지 않는다
    found = startup_report.disabled_features(_settings(PHOTO_UPLOAD_ACCEPT=False), llm_gaps=())

    text = " ".join(found)
    assert "discord.test" not in text
    assert "ocr.test" not in text
    assert "redis.test" not in text


def test_꺼진_게_있으면_경고_한_줄로_남긴다(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.api.startup_report"):
        startup_report.log_disabled_features(
            _settings(PHOTO_UPLOAD_ACCEPT=False, PUSH_DEVICE_ACCEPT=False), llm_gaps=()
        )

    assert [r.levelname for r in caplog.records] == ["WARNING"]
    message = caplog.records[0].getMessage()
    assert "2개" in message
    assert "PHOTO_UPLOAD_ACCEPT" in message and "PUSH_DEVICE_ACCEPT" in message


def test_꺼진_게_없으면_경고를_남기지_않는다(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.api.startup_report"):
        startup_report.log_disabled_features(_settings(), llm_gaps=())

    assert [r.levelname for r in caplog.records if r.levelname == "WARNING"] == []


def test_노트북에서는_경고가_아니라_안내로_남긴다(caplog: pytest.LogCaptureFixture) -> None:
    # local 은 사진 · 알림 · Redis 가 꺼져 있는 게 정상이라 띄울 때마다 경고가 뜨면 소음이다
    with caplog.at_level(logging.INFO, logger="app.api.startup_report"):
        startup_report.log_disabled_features(
            _settings(APP_ENV="local", PHOTO_UPLOAD_ACCEPT=False), llm_gaps=()
        )

    assert [r.levelname for r in caplog.records] == ["INFO"]


def test_확인하다_예외가_나도_서버를_막지_않는다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """LLM 설정을 읽다 터져도 부팅은 계속된다 — 지금까지는 첫 입력에서 드러나던 오류다."""

    def boom() -> tuple[str, ...]:
        raise ValueError("LLM_TIMEOUT_S=비밀값 형식 오류")

    monkeypatch.setattr(startup_report.entrypoint, "llm_gaps", boom)

    with caplog.at_level(logging.INFO, logger="app.api.startup_report"):
        startup_report.log_at_startup(_settings())  # 예외가 새면 여기서 테스트가 실패한다

    assert [r.levelname for r in caplog.records] == ["WARNING"]
    message = caplog.records[0].getMessage()
    assert "ValueError" in message
    # 예외 문구에는 설정 값이 섞일 수 있어 종류 이름만 남긴다
    assert "비밀값" not in message


def test_서버에서_LLM_Curator_가_비면_Discord_로_가는_ERROR_로_남긴다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.core.alerts import format_alert

    gap = "Curator 꺼짐 — 기억을 묶지 않는다 (EMBEDDING_API_KEY 없음)"
    with caplog.at_level(logging.INFO, logger="app.api.startup_report"):
        startup_report.log_disabled_features(_settings(PHOTO_UPLOAD_ACCEPT=False), llm_gaps=(gap,))

    assert [r.levelname for r in caplog.records] == ["ERROR"]
    card = format_alert(caplog.records[0], env="prod")["description"]
    assert "EMBEDDING_API_KEY" in card  # 무엇이 빠졌는지 카드에서 바로 보인다
    assert "discord.test" not in card


def test_노트북에서는_LLM_이_비어도_ERROR_가_아니다(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.api.startup_report"):
        startup_report.log_disabled_features(
            _settings(APP_ENV="local"), llm_gaps=("LLM memory (MEMORY_MODEL 없음)",)
        )

    assert [r.levelname for r in caplog.records] == ["INFO"]
