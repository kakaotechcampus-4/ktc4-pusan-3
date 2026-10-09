"""화면 오류 보고 — `POST /client-errors` (멘토 #267 2번, #166).

보호자의 폰 · 브라우저에서 난 화면 오류는 그 기기의 콘솔에만 남고 서버로 오지 않았다. 화면이
예외 종류 · digest · 화면 경로 · 기기 요약만 여기로 보내면, 서버가 ERROR 로 찍어 Discord 알림이
그대로 간다. DB 에는 넣지 않는다 — 처리방침 ⑥ "기기 정보를 데이터베이스에 저장하지 않는다".

🚨 로그인한 보호자만 보낼 수 있다 (protected_router). 인증 없는 창구면 아무나 팀 채널을 울려
   진짜 알림을 묻을 수 있다.
🚨 자유 글은 받지 않는다 — 네 칸 전부 모양이 정해져 있다. 화면 코드가 실수로(또는 누가 일부러)
   원문을 실어 보내도 서버 로그에 못 들어온다.
"""

import logging
import uuid

import pytest

PATH = "/api/v1/client-errors"
LOGGER = "app.api.v1.routers.client_errors"
CHILD = "0b6f5c1e-0000-4000-8000-000000000001"


def report(**overrides):
    body = {
        "name": "TypeError",
        "digest": "1234567890",
        "path": f"/children/{CHILD}/records",
        "platform": "android 14 app",
    }
    body.update(overrides)
    return body


async def test_로그인한_보호자의_화면_오류는_ERROR_로그가_된다(db_client, bearer, caplog):
    """204 로 받고 서버 로그에 ERROR 한 줄 — 종류 · 경로 · 기기 · 보호자 id 가 값으로 실린다.

    글귀(msg)에는 값이 없다 — Discord 알림은 글귀만 가져가므로 (app/core/alerts.py) 값은 서버
    로그에만 남는다.
    """
    headers, parent_id = bearer
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        response = await db_client.post(PATH, json=report(), headers=headers)

    assert response.status_code == 204, response.text
    records = [r for r in caplog.records if r.name == LOGGER]
    assert len(records) == 1
    line = records[0].getMessage()
    assert "TypeError" in line
    assert f"/children/{CHILD}/records" in line
    assert "android 14 app" in line
    assert str(parent_id) in line
    for value in ("TypeError", CHILD, "android", str(parent_id)):
        assert value not in records[0].msg


async def test_digest_는_없어도_된다(db_client, bearer):
    """클라이언트 렌더에서 터지면 digest 가 없다 (Next 가 서버 오류에만 붙인다)."""
    headers, _ = bearer
    response = await db_client.post(PATH, json=report(digest=None), headers=headers)

    assert response.status_code == 204, response.text


async def test_로그인이_없으면_401(client):
    """🚨 아무나 팀 채널을 울리지 못한다."""
    response = await client.post(PATH, json=report())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "아이가 딸기를 먹었다"},  # 자유 글
        {"name": "x" * 81},  # 너무 길다
        {"path": "/auth/callback?code=SECRET"},  # 쿼리 문자열 — 인가 코드가 실린다
        {"path": "records"},  # / 로 시작하지 않음
        {"platform": "@everyone 급해요"},  # 자유 글
        {"digest": "x" * 65},
    ],
)
async def test_자유_글은_거절한다(db_client, bearer, bad):
    """네 칸 전부 모양이 정해져 있다 — 원문이 서버 로그에 못 들어온다."""
    headers, _ = bearer
    response = await db_client.post(PATH, json=report(**bad), headers=headers)

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "validation_failed"


async def test_빠진_칸은_거절한다(db_client, bearer):
    headers, _ = bearer
    response = await db_client.post(PATH, json={"name": "TypeError"}, headers=headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"


async def test_모르는_보호자_id_는_안_받는다(db_client, bearer):
    """보호자 id 는 토큰에서 온다 — 본문으로 다른 사람인 척 못 한다 (extra 금지)."""
    headers, _ = bearer
    response = await db_client.post(
        PATH, json=report(parent_id=str(uuid.uuid4())), headers=headers
    )

    assert response.status_code == 400
