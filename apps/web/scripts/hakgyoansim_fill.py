"""학교안심 날개 R 의 빈 한글을 자모 조각으로 채운다.

정본 결정: docs/web/design-system-v1.md §4 (원본의 빈 한글).
`build-hakgyoansim-subset.py` 가 부른다.

왜 필요한가
- 원본 TTF 는 한글 11,172자를 cmap 에 **전부** 등록해 두었지만 8,392자는 외곽선이 비어 있다.
  브라우저는 cmap 에 글자가 없을 때만 다음 서체로 넘어가므로, 빈 글리프는 폴백되지 않고
  **빈칸으로 보인다** (입력창에 `킼` 을 넣으면 아무것도 안 보였다).

어떻게 채우나 — 원본이 조합형으로 만들어졌다는 것을 이용한다
- 그린 2,780자가 서로 다른 외곽선 1,758개로 이루어져 있다. 같은 자모 조각을 **같은 좌표에 그대로**
  재사용한다 (각/낙/닥/락 의 ㅏ 와 받침 ㄱ 이 완전히 같은 외곽선).
- 1) 그린 글자의 외곽선마다 초성/중성/종성을 붙인다 (`label`). 그 외곽선을 쓰는 글자들이
     그 자리의 자모를 공유하는가로 좁히고, 남은 애매함은 자모별 평소 박스로 가른다.
- 2) 빈 글자의 자모마다 **자리(박스)** 를 가법 모델로 유추한다 (`BoxModel` · `FACTORS`).
- 3) 같은 자모 중 문맥이 가장 가까운 조각을 그 박스에 맞춰 넣는다 (`Composer.place`).

🚨 깔끔한 벌식이 아니다 — 글자마다 손으로 다듬었다
- 같은 모음 · 받침이라도 초성에 따라 ㅏ 모양이 최대 18가지다. 그래서 "정답 조각" 이 없는 글자가
  대부분이고, 결과는 원본 디자이너의 그림이 아니라 **가장 가까운 근사**다.
- 그래서 자동 판정(`judge`)을 통과한 글자만 채운다. 나머지는 빌드 스크립트가 cmap 에서 빼서
  Pretendard 로 떨어지게 한다 — 어느 쪽이든 빈칸은 남지 않는다.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from fontTools.ttLib import TTFont

HANGUL_FIRST, HANGUL_LAST = 0xAC00, 0xD7A3
L, V, T = 0, 1, 2

# 중성 부류 — 받침 자리와 초성 모양을 가르는 가장 큰 축이다
#   0 세로(ㅏㅐㅑㅒㅓㅔㅕㅖㅣ) · 1 가로(ㅗㅛㅜㅠㅡ) · 2 섞임(ㅘㅙㅚㅝㅞㅟㅢ)
VOWEL_CLASS = {
    v: 0 if v in (0, 1, 2, 3, 4, 5, 6, 7, 20) else 1 if v in (8, 12, 13, 17, 18) else 2
    for v in range(21)
}

# 자모를 획 묶음 단위로 푼 것. 겹모음 · 겹받침은 다른 자모와 **같은 외곽선을 공유**한다
# (긘 의 오른쪽 ㅣ 획 = 긴 의 ㅣ). 역할을 붙일 때 "같은 자모인가" 가 아니라
# "겹치는 원자가 있는가" 로 본다.
# 🚨 이게 없으면 ㅢ 의 ㅣ 획이 중성 후보에서 빠져 엉뚱한 역할로 분류되고, ㅢ 재료가 0개가 됐다.
# fmt: off
ATOMS = {
    L: [{"ㄱ"}, {"ㄱ"}, {"ㄴ"}, {"ㄷ"}, {"ㄷ"}, {"ㄹ"}, {"ㅁ"}, {"ㅂ"}, {"ㅂ"}, {"ㅅ"},
        {"ㅅ"}, {"ㅇ"}, {"ㅈ"}, {"ㅈ"}, {"ㅊ"}, {"ㅋ"}, {"ㅌ"}, {"ㅍ"}, {"ㅎ"}],
    V: [{"ㅏ"}, {"ㅏ", "ㅣ"}, {"ㅑ"}, {"ㅑ", "ㅣ"}, {"ㅓ"}, {"ㅓ", "ㅣ"}, {"ㅕ"}, {"ㅕ", "ㅣ"},
        {"ㅗ"}, {"ㅗ", "ㅏ"}, {"ㅗ", "ㅏ", "ㅣ"}, {"ㅗ", "ㅣ"}, {"ㅛ"}, {"ㅜ"}, {"ㅜ", "ㅓ"},
        {"ㅜ", "ㅓ", "ㅣ"}, {"ㅜ", "ㅣ"}, {"ㅠ"}, {"ㅡ"}, {"ㅡ", "ㅣ"}, {"ㅣ"}],
    T: [set(), {"ㄱ"}, {"ㄱ"}, {"ㄱ", "ㅅ"}, {"ㄴ"}, {"ㄴ", "ㅈ"}, {"ㄴ", "ㅎ"}, {"ㄷ"}, {"ㄹ"},
        {"ㄹ", "ㄱ"}, {"ㄹ", "ㅁ"}, {"ㄹ", "ㅂ"}, {"ㄹ", "ㅅ"}, {"ㄹ", "ㅌ"}, {"ㄹ", "ㅍ"},
        {"ㄹ", "ㅎ"}, {"ㅁ"}, {"ㅂ"}, {"ㅂ", "ㅅ"}, {"ㅅ"}, {"ㅅ"}, {"ㅇ"}, {"ㅈ"}, {"ㅊ"},
        {"ㅋ"}, {"ㅌ"}, {"ㅍ"}, {"ㅎ"}],
}
# fmt: on

# ── 재료 거르기 ──
# 같은 자모인데 외곽선 개수가 드문 조각은 역할 분류가 틀렸을 가능성이 높다.
COUNT_SHARE_MIN = 0.15
# 🚨 크기가 같은 자모의 중앙값에서 크게 벗어난 조각도 뺀다. ㄱ · ㅋ 초성은 ㅓ · ㅔ 의 왼쪽 획과
#    한 외곽선으로 이어져 있어서, 거기서 떼어 낸 "ㅔ 조각" 에는 오른쪽 세로획만 남는다.
#    개수로는 못 거른다 (ㅔ 가 1개짜리인 조각이 44%) — 폭이 절반이 되는 것으로 거른다.
SIZE_RATIO_MAX = 1.3

# ── 자동 판정 ── (값의 근거: docs/web/design-system-v1.md §4)
# 조각을 박스에 맞출 때 허용하는 배율. 1.4 까지는 획이 살짝 진해질 뿐 제 글자로 읽혔다.
# 비교 대상은 원본이 아니라 "손글씨 문장 한가운데 끼는 Pretendard 한 글자" 다.
SCALE_MIN, SCALE_MAX = 1 / 1.4, 1.4
# 서로 다른 자모 조각끼리 겹친 점의 비율. 원본은 0 이다(조각이 서로 닿지 않는다).
# 0.12 까지는 획이 살짝 닿는 정도였고, 넘으면 ㅊ · ㅎ 위에 ㅜ · ㅝ 가 얹히거나
# ㅇ 이 모음을 덮어 깨졌다.
OVERLAP_MAX = 0.12

# 원래 비어 있어야 하는 글자 — 공백류. 이것들은 cmap 에서 빼지 않는다.
BLANK_OK = {0x0009, 0x000A, 0x000D, 0x0020, 0x00A0, 0x3000, 0x3164}


Contour = tuple[tuple[tuple[int, int], ...], tuple[int, ...]]  # (좌표, on-curve 플래그)
Key = tuple[int, int, int]  # (초성, 중성, 종성)


INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
VOWELS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
FINALS = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"  # 맨 앞 공백 = 받침 없음


def syllables(initials: str = INITIALS, vowels: str = VOWELS, finals: str = FINALS) -> set[int]:
    """자모 조합으로 음절 코드포인트 집합을 만든다. `EXCLUDE` 를 규칙으로 적을 때 쓴다."""
    return {
        compose((INITIALS.index(a), VOWELS.index(b), FINALS.index(c)))
        for a in initials
        for b in vowels
        for c in finals
    }


def decompose(cp: int) -> Key:
    i = cp - HANGUL_FIRST
    return i // 588, (i % 588) // 28, i % 28


def compose(key: Key) -> int:
    return HANGUL_FIRST + key[0] * 588 + key[1] * 28 + key[2]


# 조립 결과를 눈으로 보고 손글씨로 내보내지 않기로 한 글자. 여기 든 글자는 Pretendard 로 간다.
# 🚨 **원본에서 빈 글자에만** 걸린다. 원본이 그린 글자(계 · 예 · 례 등)는 여기 들어도
#    손글씨 그대로다.
# 검수 시트: scripts/hakgyoansim-fill-sheet.py · 빌드: build-hakgyoansim-subset.py
EXCLUDE = (
    # 원본에서 ㅞ 의 ㅓ 획이 이 초성들과 한 외곽선이라, 떼어 붙이면 ㅝ · ㅟ 로 읽힌다 (궵 → 귑)
    syllables(initials="ㄱㅋㅊㅎ", vowels="ㅞ")
    # 받침 없는 ㅒ · ㅖ 는 짧은 획 하나가 빠져 ㅐ · ㅔ 로 읽힌다 (댸 → 대)
    | syllables(vowels="ㅒㅖ", finals=" ")
)


def _contours(glyph, glyf) -> list[Contour]:
    coords, ends, flags = glyph.getCoordinates(glyf)
    out, start = [], 0
    for end in ends:
        out.append(
            (
                tuple(map(tuple, coords[start : end + 1])),
                tuple(f & 1 for f in flags[start : end + 1]),
            )
        )
        start = end + 1
    return out


def _bbox(contours) -> tuple[int, int, int, int]:
    xs = [x for pts, _ in contours for x, _ in pts]
    ys = [y for pts, _ in contours for _, y in pts]
    return min(xs), min(ys), max(xs), max(ys)


def is_blank(glyph, glyf) -> bool:
    return glyph.numberOfContours == 0 or (
        glyph.numberOfContours > 0 and not glyph.getCoordinates(glyf)[0]
    )


# ─────────────────────────────── 1. 역할 붙이기 ───────────────────────────────


def _units(contours: list[Contour]) -> list[list[Contour]]:
    """구멍(다른 외곽선 박스 안에 든 외곽선)을 감싸는 외곽선과 한 덩어리로 묶는다.
    🚨 이게 없으면 ㅇ · ㅁ · ㅂ 의 속공간이 다른 자모로 분류돼, 조립한 글자가 검은 덩어리가 된다."""
    boxes = [_bbox([c]) for c in contours]

    def size(b):
        return (b[2] - b[0]) * (b[3] - b[1])

    parent: dict[int, int] = {}
    for i, a in enumerate(boxes):
        hosts = [
            j
            for j, b in enumerate(boxes)
            if j != i
            and b[0] <= a[0]
            and b[1] <= a[1]
            and b[2] >= a[2]
            and b[3] >= a[3]
            and size(b) > size(a)
        ]
        if hosts:
            parent[i] = min(hosts, key=lambda j: size(boxes[j]))

    def root(i):
        while i in parent:
            i = parent[i]
        return i

    groups: dict[int, list[Contour]] = defaultdict(list)
    for i, c in enumerate(contours):
        groups[root(i)].append(c)
    return list(groups.values())


# 역할 붙이기를 몇 번 다시 돌리나. 매번 앞 결과로 "자모별 평소 자리" 를 다시 배운다.
LABEL_ROUNDS = 3
# 한 글자에서 따져 볼 역할 배정의 상한. 덩어리가 많아 넘으면 그 글자는 재료로 쓰지 않는다.
LABEL_COMBOS_MAX = 20000


def _group(r: int, key: Key) -> tuple:
    """자모의 평소 자리 · 크기를 묶는 단위. 초성은 중성 부류와 받침 유무에 따라,
    받침은 중성 부류에 따라 크게 바뀐다."""
    if r == L:
        return (r, key[L], VOWEL_CLASS[key[V]], key[T] > 0)
    if r == V:
        return (r, key[V], key[T] > 0)
    return (r, key[T], VOWEL_CLASS[key[V]])


# 섞임 모음 → (가로 홑모음, 세로 모음). 평소 박스를 둘의 합집합으로 고정한다 (`_typical_boxes`).
MIXED_VOWELS = {
    9: (8, 0),
    10: (8, 1),
    11: (8, 20),
    14: (13, 4),
    15: (13, 5),
    16: (13, 20),
    19: (18, 20),
}


def _typical_boxes(parts) -> dict[tuple, tuple[float, ...]]:
    boxes: dict[tuple, list] = defaultdict(list)
    for key, p in parts.items():
        for r, cs in p.items():
            b = _bbox(cs)
            boxes[_group(r, key)].append(b)
            boxes[(r, key[r])].append(b)  # 묶음에 표본이 없을 때 쓰는 거친 기준
    typical = {
        g: tuple(statistics.median(b[i] for b in bs) for i in range(4)) for g, bs in boxes.items()
    }
    # 🚨 섞임 모음은 배운 값을 쓰지 않는다. ㅚ 의 ㅗ 부분이 초성으로 붙은 글자들에서 배우면,
    #    그 틀린 평소 박스가 다음 판에서 같은 오류를 다시 고른다
    #    (받침 있는 ㅚ 조각 47개가 전부 ㅣ 획뿐이었다).
    for v, (horizontal, vertical) in MIXED_VOWELS.items():
        for final in (False, True):
            a, b = typical.get((V, horizontal, final)), typical.get((V, vertical, final))
            if a and b:
                typical[(V, v, final)] = (
                    min(a[0], b[0]),
                    min(a[1], b[1]),
                    max(a[2], b[2]),
                    max(a[3], b[3]),
                )
    return typical


def label(drawn: dict[Key, list[Contour]]) -> dict[Key, dict[int, list[Contour]]]:
    """그린 글자마다 {역할: 외곽선들}. 역할을 다 채우지 못한 글자는 빠진다.

    1) 외곽선을 공유하는 글자들로 덩어리마다 가능한 역할을 좁힌다 (`ATOMS`).
    2) 후보가 하나씩만 남은 글자를 씨앗으로 자모별 평소 박스를 배운다.
    3) 나머지는 가능한 배정을 전부 따져, 역할마다 평소 박스와 가장 가까운 배정을 고른다.
       🚨 "가장 가까운 덩어리의 역할을 따라간다" 는 위치 규칙으로는 ㅢ 의 ㅡ 가 받침으로,
          귄 의 ㅣ 가 받침으로 붙었다.
    """
    users: dict[tuple, set[Key]] = defaultdict(set)
    for key, contours in drawn.items():
        for pts, _ in contours:
            users[pts].add(key)

    prepared = {}
    for key, contours in drawn.items():
        roles = (L, V, T) if key[T] else (L, V)

        def candidates(unit, roles=roles):
            # 이 덩어리를 쓰는 다른 글자들과 그 자리의 자모가 늘 원자를 공유하는 역할만 남는다
            out = list(roles)
            for pts, _ in unit:
                shared = users[pts]
                if len(shared) > 1:
                    out = [r for r in out if set.intersection(*(ATOMS[r][k[r]] for k in shared))]
            return out or list(roles)

        units = _units(contours)
        prepared[key] = (roles, units, [candidates(u) for u in units])

    def build(roles, units, combo):
        return {
            r: [c for u, rr in zip(units, combo, strict=True) if rr == r for c in u] for r in roles
        }

    parts = {
        key: build(roles, units, [c[0] for c in cand])
        for key, (roles, units, cand) in prepared.items()
        if all(len(c) == 1 for c in cand) and {c[0] for c in cand} == set(roles)
    }
    for _ in range(LABEL_ROUNDS):
        typical = _typical_boxes(parts)
        relabeled = {}
        for key, (roles, units, cand) in prepared.items():
            if math.prod(len(c) for c in cand) > LABEL_COMBOS_MAX:
                continue
            best = None
            for combo in itertools.product(*cand):
                if set(combo) != set(roles):
                    continue
                cost = 0.0
                for r, cs in build(roles, units, combo).items():
                    ref = typical.get(_group(r, key)) or typical.get((r, key[r]))
                    if ref is not None:
                        cost += sum(abs(a - b) for a, b in zip(_bbox(cs), ref, strict=True))
                if best is None or cost < best[0]:
                    best = (cost, combo)
            if best is not None:
                relabeled[key] = build(roles, units, best[1])
        parts = relabeled
    return parts


def _clean(parts):
    """분류가 틀렸을 가능성이 높은 글자를 재료에서 뺀다 (COUNT_SHARE_MIN · SIZE_RATIO_MAX).
    🚨 조각 하나만이 아니라 **글자째** 뺀다. 분류 오류는 외곽선이 한 역할에서 다른 역할로 옮겨 가는
       꼴이라, 한 조각이 너무 크면 같은 글자의 다른 조각은 그만큼 모자란다 (조각만 뺐더니
       재현 검사 하위 10% IoU 가 0.87 → 0.66 으로 떨어졌다)."""
    counts: dict[tuple, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for key, p in parts.items():
        for r, cs in p.items():
            counts[(r, key[r])][len(cs)] += 1
    usual_count = {g: max(c, key=c.get) for g, c in counts.items()}

    # 크기 기준은 **가장 흔한 외곽선 개수**의 조각들로 잡는다. 전부 섞어 중앙값을 내면
    # 합쳐진 조각(ㅔ 의 오른쪽 획만)과 정상 조각의 중간이 기준이 되어 양쪽이 다 떨어진다.
    sizes: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for key, p in parts.items():
        for r, cs in p.items():
            if len(cs) == usual_count[(r, key[r])]:
                x0, y0, x1, y1 = _bbox(cs)
                sizes[_group(r, key)].append((x1 - x0, y1 - y0))
    median = {
        g: (statistics.median(w for w, _ in s), statistics.median(h for _, h in s))
        for g, s in sizes.items()
    }

    def usual(r, key, cs):
        c = counts[(r, key[r])]
        if c[len(cs)] < COUNT_SHARE_MIN * sum(c.values()):
            return False
        want = median.get(_group(r, key))
        if want is None:
            return True
        x0, y0, x1, y1 = _bbox(cs)
        for got, ref in zip((x1 - x0, y1 - y0), want, strict=True):
            if (
                max(got, 1) / max(ref, 1) > SIZE_RATIO_MAX
                or max(ref, 1) / max(got, 1) > SIZE_RATIO_MAX
            ):
                return False
        return True

    return {k: p for k, p in parts.items() if all(usual(r, k, cs) for r, cs in p.items())}


# ─────────────────────────────── 2. 자리 유추 · 3. 조각 맞추기 ───────────────────────────────


def _context(key: Key, r: int) -> tuple[int, int]:
    return tuple(x for i, x in enumerate(key) if i != r)


def _context_distance(r: int, a: tuple[int, int], b: tuple[int, int]) -> float:
    """역할 r 의 문맥(나머지 두 자모)이 얼마나 다른가. 모음 · 받침 유무가 다르면 멀다."""
    d = 0.0
    for i, x, y in zip([i for i in (L, V, T) if i != r], a, b, strict=True):
        if x == y:
            continue
        if i == V:
            d += 2 if VOWEL_CLASS[x] == VOWEL_CLASS[y] else 6
        elif i == T:
            d += 3 if (x == 0) == (y == 0) else 8
        else:
            d += 1
    return d


def _has_final(key: Key) -> bool:
    return key[T] > 0


def _final_class(key: Key) -> int:
    """받침 부류 — 0 없음 · 1 홑받침 · 2 쌍받침(ㄲ ㅆ) · 3 겹받침."""
    t = key[T]
    if t == 0:
        return 0
    if t in (2, 20):
        return 2
    if t in (3, 5, 6, 9, 10, 11, 12, 13, 14, 15, 18):
        return 3
    return 1


# 자리(박스) 모델 — 역할마다 "박스 = 주 인자 항 + 보조 인자 항" 을 좌표별로 맞춘다.
# 🚨 1:1 유추(킥 → 킼 을 다른 초성 한 쌍에서 옮기기)를 쓰지 않는 이유: 빈 글자는 그런 짝이 없는
#    경우가 대부분이고, 짝 없이 가져온 자리가 실패의 주원인이었다. 가법 모델은 각 항을 다른
#    글자 전체에서 배운다 — 재현 검사에서 상위 10% 박스 오차가 초성 18 → 7, 종성 15 → 4 units.
# 위에서부터 쓸 수 있는 첫 모델을 쓴다. 아래로 갈수록 거칠고 `level` 이 커진다.
FACTORS: dict[int, list[tuple]] = {
    L: [
        (lambda k: (k[L], k[V], _has_final(k)), lambda k: (VOWEL_CLASS[k[V]], k[T])),
        (lambda k: (k[L], VOWEL_CLASS[k[V]], _has_final(k)), lambda k: (k[V], k[T])),
        (lambda k: (k[L], VOWEL_CLASS[k[V]], _has_final(k)), lambda k: (VOWEL_CLASS[k[V]], k[T])),
        (
            lambda k: (k[L], VOWEL_CLASS[k[V]], _has_final(k)),
            lambda k: (VOWEL_CLASS[k[V]], _final_class(k)),
        ),
    ],
    V: [
        (lambda k: (k[V], k[T]), lambda k: (k[L], _has_final(k))),
        (lambda k: (k[V], _has_final(k)), lambda k: (k[L], VOWEL_CLASS[k[V]], k[T])),
        (lambda k: (k[V], _has_final(k)), lambda k: (VOWEL_CLASS[k[V]], k[T])),
        (lambda k: (k[V], _has_final(k)), lambda k: (VOWEL_CLASS[k[V]], _final_class(k))),
    ],
    T: [
        (lambda k: (k[T], k[V]), lambda k: (k[L], VOWEL_CLASS[k[V]])),
        (lambda k: (k[T], VOWEL_CLASS[k[V]]), lambda k: (k[V], k[L])),
        (lambda k: (k[T], VOWEL_CLASS[k[V]]), lambda k: (k[V],)),
        (lambda k: (k[T],), lambda k: (k[V],)),
    ],
}
FIT_ROUNDS = 10


class BoxModel:
    def __init__(self, samples: list[tuple[Key, tuple[int, int, int, int]]], major, minor):
        self.major, self.minor = major, minor
        self.a: dict = {}
        self.b: dict = defaultdict(lambda: (0.0,) * 4)
        for _ in range(FIT_ROUNDS):  # 번갈아 평균내기 (두 인자 가법 모델의 최소제곱)
            self.a = self._mean(samples, major, lambda k: self.b[minor(k)])
            self.b = defaultdict(
                lambda: (0.0,) * 4, self._mean(samples, minor, lambda k: self.a[major(k)])
            )

    @staticmethod
    def _mean(samples, group, other):
        acc: dict = defaultdict(list)
        for key, box in samples:
            o = other(key)
            acc[group(key)].append([box[i] - o[i] for i in range(4)])
        return {
            g: tuple(statistics.fmean(v[i] for v in vs) for i in range(4)) for g, vs in acc.items()
        }

    def predict(self, key: Key):
        a, b = self.a.get(self.major(key)), self.minor(key)
        if a is None or b not in self.b:
            return None
        return tuple(round(a[i] + self.b[b][i]) for i in range(4))


@dataclass
class Piece:
    role: int
    box: tuple[int, int, int, int]
    source: list[Contour]
    level: int  # 몇 번째 모델로 자리를 정했나 (0 = 원본 그대로, 1 부터 FACTORS 순서)
    scale: tuple[float, float]


class Composer:
    def __init__(self, parts):
        # (역할, 자모) → {문맥: 외곽선}
        self.shapes: dict[tuple[int, int], dict] = defaultdict(dict)
        samples: dict[int, list] = defaultdict(list)
        for key, p in parts.items():
            for r, cs in p.items():
                self.shapes[(r, key[r])][_context(key, r)] = cs
                samples[r].append((key, _bbox(cs)))
        self.models = {r: [BoxModel(samples[r], *f) for f in FACTORS[r]] for r in (L, V, T)}

    def place(self, key: Key, r: int) -> Piece | None:
        have = self.shapes.get((r, key[r]))
        if not have:
            return None
        ctx = _context(key, r)
        if ctx in have:  # 원본에 같은 자리 · 같은 문맥의 조각이 있다
            return Piece(r, _bbox(have[ctx]), have[ctx], 0, (1.0, 1.0))

        found = next(
            (
                (level, box)
                for level, m in enumerate(self.models[r], start=1)
                if (box := m.predict(key))
            ),
            None,
        )
        if found is None:
            return None
        level, box = found

        # 모양: 문맥이 가장 가깝고, 같은 거리면 덜 늘이는 조각
        def scale_of(c):
            sx0, sy0, sx1, sy1 = _bbox(have[c])
            return (box[2] - box[0]) / max(sx1 - sx0, 1), (box[3] - box[1]) / max(sy1 - sy0, 1)

        def cost(c):
            kx, ky = scale_of(c)
            return _context_distance(r, ctx, c) + 4 * (
                abs(math.log(max(kx, 1e-3))) + abs(math.log(max(ky, 1e-3)))
            )

        shape_ctx = min(have, key=cost)
        return Piece(r, box, have[shape_ctx], level, scale_of(shape_ctx))

    def compose(self, key: Key) -> list[Piece] | None:
        roles = (L, V, T) if key[T] else (L, V)
        pieces = [self.place(key, r) for r in roles]
        return None if any(p is None for p in pieces) else pieces


def fit(piece: Piece) -> list[Contour]:
    sx0, sy0, sx1, sy1 = _bbox(piece.source)
    tx0, ty0, _, _ = piece.box
    kx, ky = piece.scale
    return [
        (tuple((round(tx0 + (x - sx0) * kx), round(ty0 + (y - sy0) * ky)) for x, y in pts), flags)
        for pts, flags in piece.source
    ]


# ─────────────────────────────── 자동 판정 ───────────────────────────────


def _inside(x: float, y: float, contours: list[Contour]) -> bool:
    """nonzero 규칙. 외곽선을 점 다각형으로 근사한다 (곡선 제어점도 꼭짓점으로)."""
    winding = 0
    for pts, _ in contours:
        n = len(pts)
        for i in range(n):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
            if y0 <= y < y1 and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) > 0:
                winding += 1
            elif y1 <= y < y0 and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) < 0:
                winding -= 1
    return winding != 0


def overlap(groups: list[list[Contour]]) -> float:
    """서로 다른 조각의 외곽선 점이 다른 조각 안쪽에 들어간 비율."""
    hit = total = 0
    boxes = [_bbox(g) for g in groups]
    for i, a in enumerate(groups):
        for pts, _ in a:
            total += len(pts)
            for j, other in enumerate(groups):
                if j == i:
                    continue
                x0, y0, x1, y1 = boxes[j]
                near = [(x, y) for x, y in pts if x0 <= x <= x1 and y0 <= y <= y1]
                hit += sum(_inside(x + 0.5, y + 0.5, other) for x, y in near)
    return hit / max(total, 1)


def judge(pieces: list[Piece]) -> str | None:
    """통과하면 None, 아니면 떨어진 이유."""
    for p in pieces:
        if not all(SCALE_MIN <= k <= SCALE_MAX for k in p.scale):
            return "scale"
    if overlap([fit(p) for p in pieces]) > OVERLAP_MAX:
        return "overlap"
    return None


# ─────────────────────────────── 진입점 ───────────────────────────────


@dataclass
class FillReport:
    filled: list[int] = field(default_factory=list)
    rejected: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))


def read_drawn(font: TTFont) -> tuple[dict[Key, list[Contour]], list[int]]:
    """그린 한글과 빈 한글 코드포인트."""
    glyf, cmap = font["glyf"], font.getBestCmap()
    drawn, blank = {}, []
    for cp in range(HANGUL_FIRST, HANGUL_LAST + 1):
        name = cmap.get(cp)
        if name is None:
            continue
        glyph = glyf[name]
        if is_blank(glyph, glyf):
            blank.append(cp)
        else:
            drawn[decompose(cp)] = _contours(glyph, glyf)
    return drawn, blank


def build_glyph(contours: list[Contour]):
    """좌표와 on-curve 플래그를 **그대로** 옮긴다.
    펜으로 다시 그리면 점이 합쳐지거나 늘어날 수 있다."""
    from array import array

    from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphCoordinates
    from fontTools.ttLib.tables.ttProgram import Program

    glyph = Glyph()
    glyph.numberOfContours = len(contours)
    glyph.coordinates = GlyphCoordinates([pt for pts, _ in contours for pt in pts])
    glyph.flags = array("B", [f for _, flags in contours for f in flags])
    ends, n = [], -1
    for pts, _ in contours:
        n += len(pts)
        ends.append(n)
    glyph.endPtsOfContours = ends
    glyph.program = Program()
    glyph.program.fromBytecode(b"")
    return glyph


def fill_hangul(font: TTFont, exclude: set[int] = frozenset()) -> FillReport:
    """빈 한글 글리프에 조립한 외곽선을 넣는다. 판정에서 떨어진 글자는 빈 채로 둔다."""
    drawn, blank = read_drawn(font)
    composer = Composer(_clean(label(drawn)))
    glyf, cmap, hmtx = font["glyf"], font.getBestCmap(), font["hmtx"]
    report = FillReport()

    # 🚨 글자 폭(advance)도 원본에서 가져온다. 빈 글리프는 폭이 1000 이라, 그대로 두면 채운 글자
    #    뒤마다 띄어쓰기만 한 틈이 생긴다 (킼킼킼 이 킼 킼 킼 처럼 보였다). 원본 한글의 폭은
    #    비례폭(708~820)이고 **거의 중성 하나로 정해진다**. 다른 값은 원본 2,780자 중 쌍자음 16자
    #    (꺠 · 뺘 · 쩨 · 쪄 등, 최대 49 units) 뿐이라, 중성마다 다수 쪽 값을 쓴다.
    by_vowel: dict[int, list[int]] = defaultdict(list)
    for key in drawn:
        by_vowel[key[V]].append(hmtx[cmap[compose(key)]][0])
    advance = {v: statistics.median_low(widths) for v, widths in by_vowel.items()}

    for cp in blank:
        if cp in exclude:
            report.rejected["exclude"].append(cp)
            continue
        pieces = composer.compose(decompose(cp))
        if pieces is None:
            report.rejected["no-source"].append(cp)
            continue
        reason = judge(pieces)
        if reason:
            report.rejected[reason].append(cp)
            continue

        name = cmap[cp]
        glyph = build_glyph([c for p in pieces for c in fit(p)])
        glyph.recalcBounds(glyf)
        glyf[name] = glyph
        hmtx[name] = (advance[decompose(cp)[V]], glyph.xMin)
        report.filled.append(cp)
    return report


def drop_blank_codepoints(font: TTFont) -> list[int]:
    """외곽선이 빈 글자를 cmap 에서 뺀다 — 그래야 브라우저가 다음 서체(Pretendard)로 넘어간다."""
    glyf = font["glyf"]
    dropped = sorted(
        cp
        for cp, name in font.getBestCmap().items()
        if cp not in BLANK_OK and is_blank(glyf[name], glyf)
    )
    gone = set(dropped)
    for table in font["cmap"].tables:
        table.cmap = {cp: name for cp, name in table.cmap.items() if cp not in gone}
    return dropped
