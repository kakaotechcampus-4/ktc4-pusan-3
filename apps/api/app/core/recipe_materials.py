"""레시피 재료 문자열 파서 — `matrlInfo` 처럼 한 줄로 오는 재료 목록을 `RawIngredient` 로 (#510).

```
(3인분) ▶주재료 : 오이지(80g), 쇠고기 샤브샤브용 250g ▶양념 : 다시마(1장=5X5cm, 두꺼운 것), 소금
```

`app/integrations/` 는 core 만 import 할 수 있어서 클라이언트(`integrations/nics/`)와 이후 다른
소스가 같이 쓰는 파서는 여기 둔다. 해석하지 않고 모양만 옮긴다 — 재료 이름 정리(오타 · 묶음) ·
알레르기 코드 · 식품군은 정리 표(`reference/recipe_ingredients.yaml`)와 정리 단계(AI)가 맡는다.

🚨 알레르기 판정 입력이라 **재료일 수 있는 글을 조용히 버리지 않는다.** 인분 · 다 만든 양 표기가
아닌 머리말과 낱말 하나가 아닌 구간 머리("양념장(간장)")는 재료로 읽어 정리 표 초안에 올린다.
띄어 쓴 "…재료" 머리("필수 재료")만 붙여 쓴 이름표("육수재료")와 같게 본다. 이름을 알 수 없는
조각(빈 조각 · 양만 있는 조각)은 `name=None` 항목으로 남긴다 — 정리 단계가 "모르는
재료" 로 보고 그 레시피를 `resolved=false` 로 둔다. 구조가 깨진 문자열(닫히지 않은 괄호 · 내용 없는
구간)은 해석하지 않고 `ValueError` 로 올린다. 올릴지 말지는 부르는 쪽이 정한다.
"""

import re

from app.core.mafra_recipe_raw import RawIngredient

SECTION_MARK = "▶"

_OPEN = "(（"
_CLOSE = ")）"
_HEAD_END = ":："

# 구간 머리가 낱말 하나(주재료 · 양념장 · 육수재료)면 이름표일 뿐이다. 괄호 · 띄어쓰기 · 숫자가
# 섞인 머리("양념장(간장)" · "양념 간장")는 재료가 섞였을 수 있다
_PLAIN_HEAD = re.compile(r"[가-힣A-Za-z]+")
# 띄어 쓴 한글 머리. "재료" 로 끝나면("필수 재료" · "재  료" · "고기 삶는 재료") 붙여 쓴 이름표
# ("육수재료")와 같아서 붙여 쓴다. 다른 머리("쇠고기 양념")는 음식 이름이 든 것이 많아 그대로 둔다
_SPACED_HEAD = re.compile(r"[가-힣]+(?:\s+[가-힣]+)+")
_LABEL_END = "재료"

# 첫 구간 앞의 `(3인분)` · `<2인분 기준>` · `<1L 기준>` — 재료가 아니다. 숫자와 "인분", 또는 숫자와
# 부피 단위 · "기준" 뿐인 꼬리표만 버린다("<우유 1L 기준>" 은 재료로 읽는다)
_SERVINGS = re.compile(
    r"[(\[<（]?\s*(?:\d+(?:\s*[~\-]\s*\d+)?\s*인분(?:\s*기준)?|\d[\d.,]*\s*(?:ml|mL|L|l)\s*기준)"
    r"\s*[)\]>）]?"
)

# 양 단위(긴 것이 먼저). "3분카레" 같은 글을 양으로 떼어 내지 않게 목록으로 닫아 둔다
_UNITS = (
    "큰술|작은술|티스푼|숟가락|스푼|인분|토막|덩어리|덩이|뿌리|송이|그릇|공기|접시|봉지|포기|꼬집|"
    "방울|조각|마리|줄기|kg|mg|ml|cc|cm|mm|oz|lb|Ts|ts|컵|술|개|알|장|쪽|모|단|봉|팩|캔|통|줄|대|"
    "줌|병|근|판|잎|톨|g|l|L|T|t"
)
# 분수 글자. 파이썬 `\d` 는 "½" 를 숫자로 보지 않아서 첫 자리에 따로 받는다 — 아니면 "양파(½개)" 가
# 통째로 이름에 남아 검수한 "양파" 와 다른 재료가 된다(#510)
_FRACTIONS = "½¼¾⅓⅔⅕⅙⅛"
# 대분수의 정수 부분("1 1/2T" · "2+1/2작은술"). 뒤에 분수가 올 때만 붙인다
_WHOLE = r"(?:\d+(?:\s+|\s*\+\s*)(?=\d+/\d))?"
_DIGITS = r"[\d" + _FRACTIONS + r"][\d./~" + _FRACTIONS + r"]*"
_NUMBER = r"(?:약\s*)?" + _WHOLE + _DIGITS + r"\s*(?:" + _UNITS + r")(?![가-힣A-Za-z])"
_WORDS = r"약간|적당량|조금|소량|적당히|적량"
# 괄호 속이 양인가 — "(80g)" · "(1장=5X5cm)". "(돼지고기)" 는 양이 아니라 알레르기 정보다
_SIZE = r"\d[\d.]*(?:\s*[xX×]\s*\d[\d.]*)+\s*(?:cm|mm)(?![가-힣A-Za-z])"  # 5X5cm
_TOKEN = r"(?:" + _SIZE + r"|" + _NUMBER + r"|" + _WORDS + r")"
# 괄호 속 **전체**가 양 토큰(숫자+단위 · 크기 · 약간)과 `, = +` · 공백뿐일 때만 양이다. 양으로
# 시작한다고 뒤까지 양으로 보면 "(2컵, 새우 우린 것)" 의 새우가 이름에서 빠진다 — `amount` 는
# 판정이 읽지 않아서 놓친 알레르기를 아무도 못 본다
_PAREN_AMOUNT = re.compile(_TOKEN + r"(?:(?:\s*[,=+]\s*|\s+)" + _TOKEN + r")*")
_PAREN_TAIL = re.compile(r"(?P<name>.*?)\s*[(（](?P<amount>[^()（）]*)[)）]", re.DOTALL)
_BARE_TAIL = re.compile(r"(?P<name>.+?)\s+(?P<amount>" + _NUMBER + r"|" + _WORDS + r")", re.DOTALL)
_ONLY_AMOUNT = re.compile(_NUMBER + r"|" + _WORDS)


def split_outside_parentheses(text: str, delimiter: str = ",") -> tuple[str, ...]:
    """괄호 깊이가 0 인 `delimiter` 에서만 자른다. 조각은 앞뒤 공백을 지우고 빈 조각도 남긴다.

    괄호 안의 쉼표 · 등호는 보존한다(`다시마(1장=5X5cm, 두꺼운 것)`).
    닫히지 않은 괄호는 `ValueError`.
    짝 없는 닫는 괄호는 깊이를 0 밑으로 내리지 않고 그 조각의 글자로 남긴다.
    """
    pieces: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in _OPEN:
            depth += 1
        elif char in _CLOSE:
            depth = max(depth - 1, 0)
        elif depth == 0 and text.startswith(delimiter, index):
            pieces.append(text[start:index].strip())
            index += len(delimiter)
            start = index
            continue
        index += 1
    if depth > 0:
        raise ValueError("닫히지 않은 괄호가 있다")
    pieces.append(text[start:].strip())
    return tuple(pieces)


def split_materials(text: str) -> tuple[RawIngredient, ...]:
    """재료 문자열 → 재료 목록(적힌 순서).

    `▶머리 :` 의 머리는 `role` 이다. `▶` 가 없으면 전체를 재료 목록으로 보고 `role=None` 이다.
    빈 문자열은 재료 없음(`()`)이다 — 재료를 모르는 레시피는 정리 단계가 후보에서 뺀다.
    """
    text = text.strip()
    if not text:
        return ()
    first = text.find(SECTION_MARK)
    if first < 0:
        return _ingredients(text, role=None)

    items: list[RawIngredient] = []
    prefix = text[:first].strip()
    if prefix and not _SERVINGS.fullmatch(prefix):
        # 재료일 수 있는 머리말 — 버리지 않고 재료로 읽는다. 이름 없는 재료 하나로 뭉개면 그 속의
        # 재료를 정리 표 초안에 올릴 수 없어 아무도 검수하지 못한다
        items.extend(_ingredients(prefix, role=None))
    for section in split_outside_parentheses(text[first:], SECTION_MARK)[1:]:
        head, content = _split_head(section)
        if not content.strip():
            raise ValueError("구간 머리만 있고 내용이 없다")
        head = _compact_label(head)
        if head and not _PLAIN_HEAD.fullmatch(head):
            # "양념장(간장)" · "양념 간장" — 머리에 재료가 섞였을 수 있다. role 로만 두면
            # 재료 목록 · 알레르기 판정 어디에도 안 들어가서 머리 글을 재료로도 남긴다
            items.append(RawIngredient(name=head))  # 머리 글은 양을 떼지 않고 통째로
        items.extend(_ingredients(content, role=head or None))
    return tuple(items)


def _split_head(section: str) -> tuple[str | None, str]:
    """`주재료 : 오이지, 무` → (`주재료`, `오이지, 무`). 구간 머리가 없으면 (None, 전체)."""
    depth = 0
    for index, char in enumerate(section):
        if char in _OPEN:
            depth += 1
        elif char in _CLOSE:
            depth = max(depth - 1, 0)
        elif depth == 0 and char in _HEAD_END:
            head = section[:index]
            if "," in head:
                break  # 쉼표가 든 앞부분은 구간 머리가 아니라 재료 목록이다
            return head.strip(), section[index + 1 :]
    return None, section


def _compact_label(head: str | None) -> str | None:
    """띄어 쓴 "…재료" 이름표를 붙여 쓴다(`필수 재료` → `필수재료`, `주 재료` → `주재료`). 붙여 쓴
    이름표와 같게 재료로 더하지 않고, `주 재료` 도 주재료 구간으로 읽힌다. 아니면 그대로 둔다."""
    if head and _SPACED_HEAD.fullmatch(head):
        compact = re.sub(r"\s+", "", head)
        if compact.endswith(_LABEL_END):
            return compact
    return head


def _ingredients(content: str, *, role: str | None) -> tuple[RawIngredient, ...]:
    return tuple(_ingredient(piece, role=role) for piece in split_outside_parentheses(content))


def _ingredient(piece: str, *, role: str | None) -> RawIngredient:
    if not piece:
        return RawIngredient(name=None, role=role)  # 빈 조각 — 버리지 않는다
    name, amount = _split_amount(piece)
    return RawIngredient(name=name, amount=amount, role=role)


def _split_amount(piece: str) -> tuple[str | None, str | None]:
    """이름 뒤의 명확한 양 표기를 떼어 낸다. 확실하지 않으면 조각 전체가 이름이다."""
    tail = _PAREN_TAIL.fullmatch(piece)
    if tail and _PAREN_AMOUNT.fullmatch(tail["amount"].strip()):
        return (tail["name"].strip() or None), tail["amount"].strip()
    bare = _BARE_TAIL.fullmatch(piece)
    if bare:
        return bare["name"].strip(), bare["amount"].strip()
    if _ONLY_AMOUNT.fullmatch(piece):
        return None, piece
    return piece, None
