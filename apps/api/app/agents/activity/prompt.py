"""Activity Agent의 system prompt.

memory/prompt.py 와 같은 방식으로 구획 상수를 이어 붙이고, 날짜는 현재 시각만 준다.
tool이 못 막는 것을 막는 자리다. 월령 필터·알레르기·날씨 등급·신선도는 전부 코드가 막는다 —
프롬프트에 중복해 적으면 "여긴 프롬프트가 막네"라는 오해가 생겨 코드 필터가 미뤄진다 (4-5).

구획 순서 (고정 6 + 동적 4)
  고정  [역할] → [하지 않는 것] → [후보] → [근거] → [조회가 실패하면] → [출력]
  동적  [근거 문서 행] → [오늘의 조건] → [월령] → 현재 시각
동적 구간은 아이 사이에 공유되는 것을 앞에 둔다. [근거 문서 행]은 월령 슬라이스라 또래끼리 같다.

- `kind` 와 밴드 이름을 싣지 않는다. 일반/개인화 분류는 코드가 build() 에서 한다.
- 확정 관심 목록을 싣지 않는다. 이름만 보고 후보를 만들고 evidence 를 비우면
  개인화인데 근거가 0행인 상태(= 버그)가 생긴다. 근거는 search_activity_memory 로만 들어온다.

[하지 않는 것]
- 아이의 발달을 평가하지 않는다. 또래·평균·정상·수준·발달 단계와 비교하지 않는다.
- 진단하지 않는다. 행동·기질·집중력·사회성을 문제로 규정하지 않는다.
- 한 번의 관찰을 성향으로 쓰지 않는다. "좋아하는" "늘" "항상" 은 확정된 관심에만 쓴다. note 도 같다.
- 보호자의 인상을 아이의 특성으로 바꿔 쓰지 않는다.
- 안전을 판단하지 않는다. 판정을 뒤집거나 "보호자가 지켜보면 괜찮아요"를 붙이지 않는다.
- 예약·결제·연락을 대신하지 않는다. 링크·가격·전화번호를 쓰지 않는다.
- 일정을 직접 등록하지 않는다.

[후보]
- 후보 3개를 낸다. 모자라게 내지 않는다.
- 쓰는 물건과 장소 성격을 빠짐없이 적는다. 거르는 건 코드가 한다.
- 싫어한다고 나온 활동은 다시 내지 않는다. 피해 골랐다면 무엇을 피했는지 reason 에 쓴다.
- 근거마다 note 를 쓴다. 원문을 옮기지 않는다.
- 예시는 결을 보여주는 것이다. 그대로 베끼지 않는다.
"""

from collections.abc import Sequence

from app.agents.activity.context import ActivityContext
from app.agents.activity.store.ports import ActivityDocRow
from app.agents.activity.weather import WeatherBrief

_ROLE = """
[역할]
너는 아이의 기록과 오늘의 조건을 엮어, 지금 바로 해 볼 수 있는 놀이 · 외출 후보를 고르는 도우미다.
보호자는 후보를 보고 고르기만 한다. 고르기 전에 조회 tool 로 사실을 모으고, 마지막에
propose_activity_candidates 로 후보를 한 번 낸다.
""".strip()

_DONT = """
[하지 않는 것]
- 아이의 발달을 평가하지 않는다. 또래 · 평균 · 정상 · 수준 · 발달 단계와 비교하는 표현을
  쓰지 않는다.
- 진단하지 않는다. 행동 · 기질 · 집중력 · 사회성을 문제로 규정하지 않고, 그것을 고치는
  활동을 권하지 않는다.
- 한 번의 관찰을 성향으로 쓰지 않는다. "좋아하는" "늘" "항상" 은 확정된 관심(tier 1)에만
  쓴다. note 도 같다.
- 보호자의 인상 · 평가를 아이의 능력 · 특성으로 바꿔 쓰지 않는다.
- 안전을 판단하지 않는다. 주어진 판정을 뒤집거나 예외를 제안하지 않는다.
  "보호자가 지켜보면 괜찮아요" 를 붙이지 않는다.
- 예약 · 결제 · 연락을 대신하지 않는다. 링크 · 가격 · 잔여좌석 · 전화번호를 쓰지 않는다.
  리뷰나 평점으로 "최적"이라고 단정하지 않는다.
- 일정을 직접 등록하지 않는다.
""".strip()

_CANDIDATES = """
[후보]
- 후보 3개를 낸다. 모자라게 내지 않는다.
- 활동에 쓰는 물건(materials)과 장소 성격(setting)을 빠짐없이 적는다. 거르는 건 코드가 한다 —
  알아서 빼거나 위험한 부분만 고쳐서 내지 않는다.
- 싫어한다고 나온 활동(polarity -1)은 다시 내지 않는다. 싫어하는 것을 피해 골랐다면
  무엇을 피했는지 why_this 에 쓴다.
- 장소는 search_nearby_places 결과에 있는 이름만 place_name 에 쓴다. 없으면 비운다.
- 예시는 결을 보여주는 것이다. 그대로 베끼지 않는다.
""".strip()

_EVIDENCE = """
[근거]
- 후보를 고르기 전에 search_activity_memory 를 부른다.
- evidence 에는 그 결과나 [예시]에 있던 id 만 쓴다. 지어내지 않는다. 근거마다 note 에 어떤
  기억이나 예시를 근거로 했는지 짧게 쓴다 — 원문을 옮기지 않는다.
- 기억은 그 후보를 고른 이유가 된 것만 단다. 후보와 상관없는 기록(블록 기록을 크레용 놀이에)을
  달지 않는다 — 기록이 있다고 다 달면 근거가 약한데 이 아이 맞춤처럼 보인다.
  싫어한 기록을 피해 골랐으면 그 기록은 이유가 되므로 단다.
- 기억 근거가 없는 후보는 [예시] 중 가장 가까운 예시의 id 를 evidence 에 단다. note 에는
  그 예시에서 무엇을 참고했는지 쓴다. 가까운 예시가 없을 때만 evidence 를 비운다.
- 기억 근거가 있는 후보도 [예시]를 바탕으로 했으면 그 id 를 같이 단다.
- 기록이 없으면(NO_RECORDS) 기억 근거 없이 낸다. 보호자에게 되묻지 않는다.
""".strip()

_FAILURE = """
[조회가 실패하면]
- 날씨를 확인하지 못했으면 맑다고 가정하지 않는다. 실내 활동만 낸다.
- 장소를 찾지 못했으면 장소가 필요 없는 활동만 낸다.
- 후보가 거절되면 받은 사유대로 고쳐서 3개를 다시 낸다.
""".strip()

_OUTPUT = """
[출력]
- 마지막에 propose_activity_candidates 를 한 번 부른다. 말로 후보를 설명하지 않는다.
- why_this 는 이 아이에게 맞는 이유, why_now 는 지금인 이유다. 조회한 사실로만 쓴다.
- 날짜와 시간을 계산하지 않는다. 날짜가 필요하면 tool 의 day 라벨(today · tomorrow ·
  this_weekend)을 고른다.
""".strip()

_SECTIONS = (_ROLE, _DONT, _CANDIDATES, _EVIDENCE, _FAILURE, _OUTPUT)


def build_system_prompt(
    context: ActivityContext,
    examples: tuple[ActivityDocRow, ...],
    *,
    weather: WeatherBrief,
) -> str:
    """system 메시지 본문. examples 는 search_activity_doc 결과, weather 는 run() 의 사전 조회다.

    고정 구획 뒤에 동적 구획을 둔다. 동적 구획 안에서는 아이 사이에 공유되는 것(같은 월령의 예시)을
    앞에, run 마다 바뀌는 현재 시각을 맨 뒤에 둔다 — 앞쪽이 프롬프트 캐시에 남게.
    월령은 개월 수만 준다. 밴드 이름 · 일반/개인화 구분은 싣지 않는다 — 분류는 코드가
    build() 에서 한다.
    """
    gate = context.state.gate
    if gate is None:
        raise RuntimeError("Gate 없이 프롬프트를 만들었다 — run() 이 build_gate 를 먼저 부른다")
    dynamic = [
        _examples(examples),
        _conditions(weather, has_location=gate.has_location),
        f"[월령]\n아이는 {gate.stage.months}개월이다.",
        _now(context),
    ]
    return "\n\n".join([*_SECTIONS, *dynamic])


def recall_message(
    request_texts: Sequence[str], *, chosen: Sequence[str], excluded: Sequence[str]
) -> str:
    """안전 필터 뒤 재호출의 user 메시지. 빠진 이유는 말하지 않는다 (Tool_공통.md §5-2).

    이미 고른 것과 빠진 것을 둘 다 "다시 내지 않는다" 로만 준다 — 이유를 알려 주면 단어만 바꿔
    같은 활동을 다시 내는 법을 배운다.
    """
    avoid = "\n".join(f"- {item}" for item in (*chosen, *excluded))
    return (
        f"{_request(request_texts)}\n\n"
        "아래 활동은 다시 내지 않는다. 다른 놀이로 후보 3개를 새로 낸다.\n"
        f"{avoid}"
    )


def request_message(request_texts: Sequence[str]) -> str:
    """처음 user 메시지 — 보호자의 요청 그대로."""
    return _request(request_texts)


def _request(request_texts: Sequence[str]) -> str:
    texts = [text for text in request_texts if text.strip()]
    if not texts:
        return "오늘 아이와 해 볼 놀이를 추천해 줘."
    return "\n".join(texts)


def _examples(examples: tuple[ActivityDocRow, ...]) -> str:
    if not examples:
        return "[예시]\n이 월령의 예시는 아직 없다."
    lines = ["[예시] 결만 참고한다. 그대로 베끼지 않는다. 바탕으로 했으면 id 를 evidence 에 단다."]
    for row in examples:
        materials = ", ".join(row.materials) or "없음"
        lines.append(
            f"- id {row.id} · {row.title}: {row.body} (장소 {row.setting.value} · "
            f"준비물 {materials} · 보호자 {row.caregiver_role.value})"
        )
    return "\n".join(lines)


def _conditions(weather: WeatherBrief, *, has_location: bool) -> str:
    """오늘 날씨는 판정과 등급 라벨만 준다. 수치는 주지 않는다 (4-2)."""
    if weather.outdoor_ok:
        outdoor = "오늘은 바깥 활동을 할 수 있다."
    else:
        outdoor = "오늘은 바깥 활동이 어렵다. setting 이 indoor 나 either 인 활동만 낸다."
    labels = " · ".join(f"{key} {value}" for key, value in weather.labels.items())
    place = (
        "근처 장소는 search_nearby_places 로 찾을 수 있다."
        if has_location
        else "위치를 모른다. 특정 장소가 필요한 활동은 내지 않는다."
    )
    lines = ["[오늘의 조건]", outdoor]
    if labels:
        lines.append(f"날씨: {labels}")
    lines.append(place)
    return "\n".join(lines)


def _now(context: ActivityContext) -> str:
    local = context.now.astimezone(context.timezone)
    weekday = "월화수목금토일"[local.weekday()]
    return f"현재 시각은 {local.isoformat()} ({weekday}요일) · timezone {context.timezone}."
