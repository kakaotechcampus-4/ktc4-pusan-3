"""적재 스크립트의 콘솔 출력 — Windows 콘솔(cp949)에서도 찍혀야 한다.

실제 적재가 커밋된 뒤 마지막 `print` 에서 UnicodeEncodeError 가 나면 종료 코드가 1 이라 크론이
실패로 읽는다. 인코딩 못 하는 문자(`—` · 이모지)를 요약 · 도움말에 쓰지 않는다.
"""

import pytest

from scripts.load_food_catalog import Loaded, build_parser, summary


@pytest.mark.parametrize("dry_run", [True, False])
def test_요약은_cp949_콘솔에서도_찍힌다(dry_run):
    loaded = Loaded(
        nutrition_rows=5488,
        recipe_rows=537,
        resolved_recipes=31,
        retired_recipes=2,
        nutrition_received=8807,
    )
    text = summary(loaded, dry_run=dry_run)

    text.encode("cp949")
    assert "5488" in text and "537" in text and "31" in text
    assert ("dry-run" in text) is dry_run


def test_요약은_받은_영양DB_행과_적재한_메뉴_수를_같이_보여준다():
    # 8,807행을 받아도 이름 기준 · 영양 칸이 찬 메뉴만 싣는다 — 숫자 하나만 보이면 8,807 로 오해한다
    text = summary(Loaded(nutrition_rows=5488, nutrition_received=8807), dry_run=False)

    assert "8807" in text and "5488" in text


def test_영양DB_를_안_받았으면_받은_행을_말하지_않는다():
    assert "받은" not in summary(Loaded(recipe_rows=537), dry_run=False)


def test_도움말은_cp949_콘솔에서도_찍힌다():
    build_parser().format_help().encode("cp949")


def test_요약은_식품군이_있는_메뉴_수를_같이_보여준다():
    # 영양소 분석은 식품군을 아는 메뉴가 절반이 안 되면 "살펴보지 못했어요" 다 — 적재 때 바로 본다
    text = summary(
        Loaded(nutrition_rows=5488, nutrition_received=8807, grouped_menus=1300), dry_run=False
    )

    text.encode("cp949")
    assert "식품군 있음 1300행" in text
