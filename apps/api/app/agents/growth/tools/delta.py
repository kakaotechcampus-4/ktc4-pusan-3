"""`growth_review` — 키 · 몸무게 기록을 판정 없이 수치 그대로 정리한다. 모델을 부르지 않는다.

**"얼마나 컸는지"는 말하지만 "잘 크고 있는지"는 말하지 않는다.** 속도 · 경향 · 전망을 만들지 않고,
백분위 · 또래 비교 · 안심 문장도 넣지 않는다. 결과는 `Readout(authored_by="code")` 라 화면에 그대로
가고 모델 입력에도 넣지 않는다 (Growth_Tool_명세.md §3).

- 측정 **전부**를 시간순으로, **측정일을 빠짐없이** 적는다. 측정 출처(보호자 · 검진)를 저장하지
  않아서 언제 잰 기록인지가 유일한 신뢰도 단서다.
- **최소 간격도 연령 축도 없다.** 2주 만에 다시 쟀으면 그 0.2cm 를 그대로 적는다 — 간격이 짧다고
  "말하기 어려워요" 로 덮으면 보호자가 넣은 기록을 시스템이 숨기는 셈이다.
- 키와 몸무게는 **지표별로** 센다. 그 값이 있는 행만으로 "2건 이상" 을 보고 차분한다. "직전 대비" 는
  같은 지표의 바로 앞 측정과 비교한다. 한쪽만 잰 날은 그 값만 적고 빈 칸을 채우지 않는다.
- 값은 `Decimal` 로 읽고 뺀다. float 로 빼면 `104.2 - 95.5` 가 `8.700000000000003` 이 된다.
  반올림도 보정도 하지 않는다.
- 키 측정이 24개월 전과 후에 모두 있으면 재는 자세가 바뀌는 시기라는 단서 한 줄을 붙인다.
  숫자는 고치지 않고 "괜찮아요" 같은 안심 문장도 넣지 않는다.

증감 문구("늘었어요" · "직전 대비")는 G-9 로 협의 중이다 — 정해지는 대로 아래 템플릿에서 증감만 빼면
측정 나열은 그대로 남는다. 감소값은 부호 그대로 적는다.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app.agents.common.readout import Readout
from app.agents.common.refs import Ref
from app.agents.growth.readouts import (
    DELTA_CHECKUP_HINT,
    DELTA_NEED_MORE,
    DELTA_ONE_METRIC,
    DELTA_POSTURE_HINT,
    READOUTS,
)
from app.agents.growth.store.ports import GrowthMeasurement
from app.rules.age import months_between

# 누워서 재다가 서서 재기 시작하는 월령 (G-8). 이 월령 **이상**이 "후"다
POSTURE_CHANGE_MONTH = 24

# 판정을 묻는 질문의 표지. 놓쳐도 수치만 나갈 뿐이라 해가 없고, 잘못 걸려도 검진 안내가 붙을 뿐이다
_JUDGEMENT_MARKERS = (
    "잘크고",
    "잘크나",
    "잘자라",
    "잘자란",
    "많이컸",
    "많이큰",
    "큰편",
    "작은편",
    "빠른편",
    "느린편",
    "또래",
    "정상",
    "괜찮",
    "늦",
)


def asks_judgement(request_texts: Sequence[str]) -> bool:
    """ "잘 크고 있어?" · "많이 큰 거야?" · "또래보다 작은 편이야?" 처럼 판정을 묻는가.

    판정을 물어도 판정하지 않는다 — 같은 추이 서술에 검진 안내(`delta.checkup_hint`)만 더한다.
    """
    normalized = ["".join(text.split()) for text in request_texts]
    return any(marker in text for text in normalized for marker in _JUDGEMENT_MARKERS)


def compute_growth_delta(
    measurements: Sequence[GrowthMeasurement],
    *,
    birth_date: date,
    asks_judgement: bool = False,
) -> Readout:
    """측정 전부를 시간순으로 정리한 `growth_delta` readout.

    둘 다 1건 이하면 `delta.need_more` 한 줄이다 — 숫자를 만들지 않는다. 한 지표만 2건 이상이면 그
    지표만 요약하고 `delta.one_metric` 을 붙인다. 같은 날 여러 번 잰 기록은 입력 순서대로
    따로 적는다.
    """
    rows = sorted(measurements, key=lambda m: m.measured_on)  # 안정 정렬 — 같은 날은 입력 순서
    heights = [m for m in rows if m.height_cm is not None]
    weights = [m for m in rows if m.weight_kg is not None]
    has_height, has_weight = len(heights) >= 2, len(weights) >= 2

    lines: list[str] = []
    if not has_height and not has_weight:
        lines.append(_text(DELTA_NEED_MORE))
    else:
        lines.extend(_summary(heights if has_height else [], weights if has_weight else []))
        lines.extend(_measurement_lines(rows))
        if not has_height or not has_weight:
            lines.append(_text(DELTA_ONE_METRIC, metric="키" if not has_height else "몸무게"))
        if _crosses_posture_change(heights, birth_date):
            lines.append(_text(DELTA_POSTURE_HINT))
    if asks_judgement:
        lines.append(_text(DELTA_CHECKUP_HINT))

    return Readout(
        kind="growth_delta",
        body="\n".join(lines),
        authored_by="code",
        source_refs=tuple(Ref(kind="child_growth_log", id=m.id) for m in rows),
    )


def _text(key: str, **values: str) -> str:
    return READOUTS.render(key, **values).body


def _summary(heights: list[GrowthMeasurement], weights: list[GrowthMeasurement]) -> list[str]:
    """지표별 요약. 두 지표의 첫 · 마지막 측정일이 같으면 한 줄로 묶는다."""
    height = _span(heights, lambda m: m.height_cm) if heights else None
    weight = _span(weights, lambda m: m.weight_kg) if weights else None
    if height and weight:
        if height[:2] == weight[:2]:
            first, last, h_delta = height
            return [
                f"{_when(first)}부터 {_when(last)}까지 {_length(first, last)} "
                f"키 {_num(h_delta)}cm · 몸무게 {_num(weight[2])}kg 늘었어요."
            ]
        return [
            f"{_when(height[0])}부터 {_when(height[1])}까지 {_length(height[0], height[1])} "
            f"키 {_num(height[2])}cm,",
            f"{_when(weight[0])}부터 {_when(weight[1])}까지 {_length(weight[0], weight[1])} "
            f"몸무게 {_num(weight[2])}kg 늘었어요.",
        ]
    if height:
        return [
            f"{_when(height[0])}부터 {_when(height[1])}까지 {_length(height[0], height[1])} "
            f"키 {_num(height[2])}cm 늘었어요."
        ]
    assert weight is not None  # 둘 다 없으면 need_more 로 이미 나갔다
    return [
        f"{_when(weight[0])}부터 {_when(weight[1])}까지 {_length(weight[0], weight[1])} "
        f"몸무게 {_num(weight[2])}kg 늘었어요."
    ]


def _span(rows, pick) -> tuple[date, date, Decimal]:
    """(첫 측정일, 마지막 측정일, 마지막 값 − 첫 값)."""
    first, last = rows[0], rows[-1]
    return first.measured_on, last.measured_on, pick(last) - pick(first)


def _measurement_lines(rows: Sequence[GrowthMeasurement]) -> list[str]:
    """측정마다 한 줄. 그날 잰 값만 적고, 직전 대비는 같은 지표의 바로 앞 측정과 비교한다."""
    lines: list[str] = []
    previous_height: Decimal | None = None
    previous_weight: Decimal | None = None
    for m in rows:
        values: list[str] = []
        diffs: list[str] = []
        if m.height_cm is not None:
            values.append(f"키 {_num(m.height_cm)}cm")
            if previous_height is not None:
                diffs.append(f"키 {_num(m.height_cm - previous_height)}cm")
            previous_height = m.height_cm
        if m.weight_kg is not None:
            values.append(f"몸무게 {_num(m.weight_kg)}kg")
            if previous_weight is not None:
                diffs.append(f"몸무게 {_num(m.weight_kg - previous_weight)}kg")
            previous_weight = m.weight_kg
        line = f"  {_when(m.measured_on)}  " + " · ".join(values)
        if diffs:
            line += f"  (직전 대비 {' · '.join(diffs)})"
        lines.append(line)
    return lines


def _crosses_posture_change(heights: Sequence[GrowthMeasurement], birth_date: date) -> bool:
    """키 측정이 24개월 전과 후에 모두 있는가.

    생일 전 날짜(잘못된 기록)는 월령을 못 세서 건너뛴다.
    """
    ages = [
        months_between(birth_date, m.measured_on) for m in heights if m.measured_on >= birth_date
    ]
    return any(age < POSTURE_CHANGE_MONTH for age in ages) and any(
        age >= POSTURE_CHANGE_MONTH for age in ages
    )


def _when(day: date) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일"


def _length(first: date, last: date) -> str:
    """ "N개월간". 한 달이 안 되면 일수로 적는다."""
    months = months_between(first, last)
    return f"{months}개월간" if months >= 1 else f"{(last - first).days}일간"


def _num(value: Decimal) -> str:
    """반올림하지 않는다. 부호는 그대로 — 감소값은 "-0.3"."""
    return format(value, "f")
