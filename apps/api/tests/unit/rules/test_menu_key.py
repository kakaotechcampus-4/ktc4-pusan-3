"""메뉴명 → `menu_key` 정규화. 정본은 `food_agent_own_table.md` §1.
공백 · 괄호 · 급식 알레르기 번호를 지운다.

떼는 것은 읽는 것이 아니라 지우는 것이다 — 급식표의 알레르기 번호는 판정에 쓰지 않는다.
"""

import json
from pathlib import Path

import pytest

from app.rules.menu_key import menu_display_name, normalize_menu_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("된장찌개", "된장찌개"),
        ("된장 찌개", "된장찌개"),  # 공백
        (" 미역국 ", "미역국"),
        (
            "카레라이스(2.5.6)",
            "카레라이스",
        ),  # 급식표 알레르기 번호 — 안 떼면 카탈로그에서 못 찾는다
        ("닭갈비(5.6.15.)", "닭갈비"),
        ("우유 2,5", "우유"),
        ("우유②", "우유"),
        ("우유(200ml)", "우유"),  # 괄호
        ("두부조림[국산]", "두부조림"),
        ("3색나물", "3색나물"),  # 이름 속 숫자는 번호가 아니다
        ("ＫＩＭＣＨＩ볶음밥", "kimchi볶음밥"),  # 전각 영문, 소문자
    ],
)
def test_menu_key_정규화(raw: str, expected: str) -> None:
    assert normalize_menu_key(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "(2.5)", "①②", "---"])
def test_이름이_남지_않으면_빈_문자열(raw: str) -> None:
    # 빈 키는 호출부가 unresolved 로 둔다 — 빈 이름으로 외부를 부르지 않는다
    assert normalize_menu_key(raw) == ""


def test_같은_이름은_표기가_달라도_같은_키() -> None:
    assert normalize_menu_key("김치 볶음밥 (1.5)") == normalize_menu_key("김치볶음밥")


def test_다른_이름은_다른_키로_남는다() -> None:
    # 비슷한 이름으로 합치지 않는다 — 된장찌개와 된장찌개_두부는 다른 메뉴다
    assert normalize_menu_key("된장찌개") != normalize_menu_key("된장찌개 두부")


# ── 급식표 원문 → 화면 · 저장에 쓰는 메뉴 이름 (#908) ──
# 모델의 `name` 끝에 " Riot" · "/Optional?" · `}{"` 같은 찌꺼기가 붙어 화면 제목과 저장 키까지 갔다.
# 원문(`raw`)은 맞게 읽혔다 — 이름은 원문에서 코드가 만든다. 지우기만 하고 글자를 만들지 않는다.

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "meal_plan_2025_01.json"


def _fixture_raws() -> list[str]:
    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [it["raw"] for d in plan["days"] for m in d["meals"] for it in m["items"]]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("백김치9(백깍두기9)", "백김치(백깍두기)"),  # 대체 메뉴 괄호는 남긴다 — 모델 이름과 같다
        ("모듬버섯된장국5,6", "모듬버섯된장국"),
        ("치킨가스&소스1,5,6,15", "치킨가스&소스"),
        ("경기도과일 또는 고구마우유죽2", "경기도과일 또는 고구마우유죽"),
        ("연근조림★5,6", "연근조림"),  # 꾸밈 기호
        ("백미밥1/2", "백미밥1/2"),  # 분량은 번호가 아니다 (strip_allergen_marks 와 같다)
        ("3색나물", "3색나물"),
        ("카레라이스(2.5.6)", "카레라이스"),  # 번호만 있던 괄호는 빈 괄호로 남기지 않는다
        ("애호박국:⑤⑥", "애호박국"),
        # 아래는 모델이 원문 칸에 옮긴 군더더기 (11-11 측정 M1) — 메뉴 글자는 그대로다
        ("떡(증편),", "떡(증편)"),  # 칸 끝 쉼표
        ("빵(찐빵5,6,10,16,18),", "빵(찐빵)"),
        ("미니핫도그1,2,5,6,10,15,16,", "미니핫도그"),
        ("백김치9(백깍두기9)`", "백김치(백깍두기)"),  # 끝에 붙은 `
        ("빵(식빵2,5,6&&쨈),", "빵(식빵&쨈)"),  # & 가 겹쳐 읽힘
    ],
)
def test_메뉴_이름은_원문에서_번호와_기호를_뺀_것(raw: str, expected: str) -> None:
    assert menu_display_name(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        *_fixture_raws(),
        "백김치9(백깍두기9)`",
        "빵(식빵2,5,6&&쨈),",
        "떡(증편 Riot",  # 원문에 찌꺼기가 왔어도 지울 뿐 다른 글자를 넣지 않는다
        "(2.5)",
        "",
    ],
)
def test_메뉴_이름은_원문에_없는_글자를_담지_않는다(raw: str) -> None:
    name = menu_display_name(raw)

    assert set(name) <= set(raw)
    assert name == name.strip()


@pytest.mark.parametrize("raw", _fixture_raws())
def test_원문에서_만든_이름의_저장_키는_원문의_키와_같다(raw: str) -> None:
    # 이름을 다듬어도 저장 키(→ 카탈로그 · 알레르기 판정)가 원문과 다른 메뉴를 가리키지 않는다
    assert normalize_menu_key(menu_display_name(raw)) == normalize_menu_key(raw)
