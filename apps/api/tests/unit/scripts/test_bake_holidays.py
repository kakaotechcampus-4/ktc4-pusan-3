"""공휴일 굽기 스크립트 — 응답 읽기 · 파일 만들기. 네트워크는 부르지 않는다."""

from datetime import date

import httpx
import pytest

from app.rules.holidays_data import HOLIDAYS
from scripts.bake_holidays import BakeError, changes, fetch, parse_rest_days, render

SECRET = "test-service-key-should-not-leak"


def payload(items, *, code="00"):
    return {
        "response": {
            "header": {"resultCode": code, "resultMsg": "NORMAL SERVICE."},
            "body": {"items": items, "totalCount": 0},
        }
    }


def row(locdate: int, name: str, holiday: str = "Y") -> dict:
    return {"dateKind": "01", "dateName": name, "isHoliday": holiday, "locdate": locdate}


class TestParse:
    def test_쉬는_날만_남긴다(self):
        items = {
            "item": [
                row(20261003, "개천절"),
                row(20261009, "한글날"),
                row(20260508, "어버이날", "N"),
            ]
        }
        assert parse_rest_days(payload(items), 2026) == {
            date(2026, 10, 3): "개천절",
            date(2026, 10, 9): "한글날",
        }

    def test_한_건이면_객체로_온다(self):
        assert parse_rest_days(payload({"item": row(20261225, "기독탄신일")}), 2026) == {
            date(2026, 12, 25): "기독탄신일"
        }

    def test_같은_날_두_이름은_합친다(self):
        items = {"item": [row(20260505, "어린이날"), row(20260505, "대체공휴일")]}
        assert parse_rest_days(payload(items), 2026) == {date(2026, 5, 5): "어린이날 · 대체공휴일"}

    def test_0건이면_멈춘다(self):
        """발표 전인 해를 빈 해로 구우면 그해 공휴일을 전부 평일로 읽는다."""
        with pytest.raises(BakeError):
            parse_rest_days(payload(""), 2028)

    def test_오류_코드면_멈춘다(self):
        with pytest.raises(BakeError):
            parse_rest_days(payload({"item": []}, code="30"), 2026)

    def test_다른_해의_날짜가_오면_멈춘다(self):
        with pytest.raises(BakeError):
            parse_rest_days(payload({"item": row(20270101, "1월1일")}), 2026)


class TestRender:
    def test_만든_파일을_읽으면_같은_표가_나온다(self):
        namespace: dict = {}
        exec(render(HOLIDAYS), namespace)  # noqa: S102 — 스크립트가 만든 글을 그대로 읽어 본다
        assert namespace["HOLIDAYS"] == HOLIDAYS

    def test_같은_입력이면_같은_글이다(self):
        assert render(HOLIDAYS) == render(dict(reversed(list(HOLIDAYS.items()))))


def test_바뀐_날을_알려_준다():
    before = {date(2026, 10, 3): "개천절", date(2026, 10, 4): "잘못 넣은 날"}
    after = {date(2026, 10, 3): "개천절", date(2026, 10, 5): "대체공휴일"}
    assert changes(before, after) == ["+ 2026-10-05 대체공휴일", "- 2026-10-04 잘못 넣은 날"]


def test_호출_실패_메시지에_인증키를_넣지_않는다():
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    with httpx.Client(transport=httpx.MockTransport(boom)) as client:
        with pytest.raises(BakeError) as error:
            fetch(2026, SECRET, client)
    assert SECRET not in str(error.value)
