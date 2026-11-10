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

import itertools
from collections.abc import Sequence
from types import SimpleNamespace
from uuid import UUID

from app.agents.activity.context import ActivityContext
from app.agents.activity.gating import (
    checks_weather,
    finds_events,
    finds_places,
    weather_checked,
)
from app.agents.activity.store.ports import ActivityDocRow
from app.agents.activity.weather import WeatherBrief
from app.agents.common.gate import Gate
from app.agents.common.prompt_fingerprint import (
    fixed_context,
    fixed_gate,
    register_prompt,
    tools_text,
)

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
- 음식 · 식재료를 만지거나 먹는 활동이면 그 이름을 foods 에 하나씩 빠짐없이 적는다(밥 · 김 ·
  밀가루 · 군밤처럼). 음식이 없으면 foods 를 비운다.
- 싫어한다고 나온 활동(polarity -1)은 다시 내지 않는다. 싫어하는 것을 피해 골랐다면
  무엇을 피했는지 reason 에 쓴다.
- 언제 하는 활동인지 day 에 보호자가 쓴 날 표현을 그대로 적는다(오늘 · 내일 · 이번 주말 ·
  다음 주 주말 · 11월 20일). 보호자가 날을 말하지 않았으면 오늘이다. 날짜는 코드가 계산한다.
  바깥 활동(outdoor)은 그날 날씨로 거른다 — 다른 날 활동이면 lookup_weather 로 그날을 먼저
  본다. 주말처럼 여러 날이면 되는 날을 reason 에 쓴다.
- 장소는 search_nearby_places 결과에 있는 이름만 place_name 에 쓴다. 없으면 비운다.
  장소를 찾을 때도 그 활동의 day 를 같이 넘긴다.
- 보호자가 이번 요청에서 원하는 결(활동적인 · 조용한 · 실내 · 바깥)이나 장소 종류(키즈카페 ·
  공원 · 도서관)를 말했으면 그것이 먼저다. 기록 · 오늘 상태로 바꾸지 않고, 기록은 그 안에서
  무엇을 할지 고르는 데만 쓴다. 오늘 많이 뛰었어도 "또 신나게" 를 원하면 신나는 놀이를 낸다.
- 보호자가 결을 말하지 않았으면, 장소를 붙일 때 reason 에 먼저 왜 그 종류의 장소인지를 이
  아이의 기록 · 오늘 상태로 쓴다. 예: 오늘 많이 뛰었으면 조용한 곳(도서관), 며칠 실내에만
  있었으면 몸 쓰는 곳, 최근에 같은 종류를 여러 번 갔으면 다른 종류. 거리 · 이름만으로 고른
  것처럼 쓰지 않는다.
- 3개의 구성은 보호자의 이번 요청이 정한다. 놀이 종류 · 장소 종류 · 날 · 집 안 · 새로운 것
  같은 단서가 있으면 그대로 따른다 — 단서에 맞으면 3개가 같은 결이어도 된다(키즈카페를 물으면
  키즈카페만, 비 와서 집에서 할 걸 물으면 집 놀이만).
- 확정 관심을 살려 고른다. 다만 같은 관심 하나로 3개를 채우지 않는다 — 같은 관심은 2개까지다.
  관심이 여럿이면 서로 다른 관심을 쓴다. 보호자가 이번 요청에서 그 관심(예: 공룡)을 직접
  말했으면 3개 모두 그 관심이어도 된다.
- 관심을 살리지 않은 후보에는 content · reason 에 관심을 쓰지 않고 관심 기록을 근거로 달지 않는다.
  그 후보는 다른 기록(오늘 상태 · 최근에 한 놀이)에 맞추고, 그 기록을 근거로 단다.
- 보호자가 새롭거나 색다른 것(안 해 본 · 늘 하던 거 말고)을 원하면 확정 관심을 쓰지 않는다 —
  아이가 기록에 없는 결로 고른다.
- 단서가 없는 막연한 요청("애랑 할 만한 거" · "주말에 뭐 하지")일 때만 3개를 서로 다른 결로
  섞는다. 재료는 확정 관심을 살린 놀이 · 행사 · 장소 · 집 놀이이고, 그날 상황으로 고른다.
  - 확정 관심이 있으면 1개 이상은 그 관심을 살린다.
  - [오늘의 조건]에 행사를 찾을 수 있다고 있고 그날 바깥이 되면 행사를 찾아 본다. 가까이에
    기간이 짧은 행사가 열리면 앞에 낸다. 결과는 기간이 짧은 행사부터 온다. 1년 내내 하는
    곳뿐이면 굳이 앞에 두지 않는다.
  - [오늘의 조건]에 장소를 찾을 수 있다고 있으면 장소가 붙은 활동(키즈카페 · 도서관 · 공원 ·
    놀이터)도 재료다. 바깥이 어렵거나 늦은 시간이면 실내 장소 · 집 놀이 위주로 낸다.
- 관심을 상관없는 후보에 억지로 잇지 않는다. 이어지지 않으면 reason 에 관심을 쓰지 않고 관심
  기록을 근거로 달지 않는다. 행사 · 장소의 reason 은 거기서 할 일과 그날 · 오늘 상태로
  쓴다(야구를 좋아해서 드론쇼 — 처럼 쓰지 않는다).
- 장소 이름만 보고 그 안의 시설(볼풀 · 트램펄린 · 블록존 같은)을 짐작해 쓰지 않는다. 장소
  결과에는 이름과 종류만 있다. 그 장소에 시설이 있다고 쓰는 것은 기록에 그 장소에서 그 시설을
  쓴 일이 있을 때뿐이다. 보호자가 원하는 시설(볼풀 있는 곳)을 말했어도 그곳에 있는지는 모른다 —
  시설 이름 없이 놀이 방법만 쓰거나, 있는지는 가기 전에 확인해 달라고 쓴다.
- 기록에 장소 이름이 있으면(지난번에 간 곳) 그 장소 이름과 그곳에서 한 일을 근거 note 에
  그대로 쓴다.
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
- 보호자가 이번 요청에서 놀이 종류 · 소재 · 주제를 정했으면(물놀이 · 공놀이 · 티니핑처럼)
  requested_play 에 그 말을 적고, 그 놀이를 요청대로 만든다. 기억 근거가 없어도 [예시]를 근거로
  달지 않아도 된다 — [예시]는 이 월령에 맞는 결을 보는 참고다. 언제 · 어디서 · 얼마나만
  말했거나("비 오는 날", "자기 전에") 막연하면("뭐 하고 놀까?") requested_play 를 비운다.
- requested_play 가 비었으면, 기억 근거가 없는 후보는 [예시] 중 가장 가까운 예시의 id 를
  evidence 에 단다. note 에는 그 예시에서 무엇을 참고했는지 쓴다. 이때 evidence 를 비우지
  않는다 — 비우면 거절된다.
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
- reason 에는 이 아이에게 맞는 이유와 지금인 이유를 한두 문장으로 쓴다. 조회한 사실로만 쓴다.
- 날짜와 시간을 계산하지 않는다. 날짜가 필요하면 tool 의 day 에 보호자가 쓴 날 표현을 그대로
  넣는다.
""".strip()

_SECTIONS = (_ROLE, _DONT, _CANDIDATES, _EVIDENCE, _FAILURE, _OUTPUT)


def build_system_prompt(
    context: ActivityContext,
    examples: tuple[ActivityDocRow, ...],
    *,
    weather: WeatherBrief,
    dark: bool = False,
) -> str:
    """system 메시지 본문. examples 는 search_activity_doc 결과, weather 는 run() 의 사전 조회다.
    dark 는 지금 해가 졌는지다(`outing.dark_now`) — 오늘 놀이터만 빼라고 적는다 (#800).

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
        _conditions(weather, checked=weather_checked(gate, weather), gate=gate, dark=dark),
        f"[월령]\n아이는 {gate.stage.months}개월이다.",
        _now(context),
    ]
    return "\n\n".join([*_SECTIONS, *dynamic])


# 재호출 방향 — 빠진 이유(알레르기 · 위험 용어)와 상관없이 늘 같은 문장이다. 알레르기로 빠졌을
# 때만 주면 그 자체가 사유다 (Tool_공통.md §5-2). 같은 음식의 변형(밀 쿠키 → 다른 밀 쿠키)을 다시 내
# 0개로 끝나는 것을 막는다. "다시 쓰지 않는다" 만 쓰면 "쓰되 적지 않기" 로 읽혀서, 쓰는 재료는
# 숨기지 말고 적으라고 같이 말한다 (#582)
RECALL_HINT = (
    "빠진 활동에 쓰인 음식 · 재료 · 물건(그것으로 만든 것 · 포장 · 껍질 포함)은 다시 쓰지 않는다. "
    "이름만 빼거나 다르게 부르지 말고 그 재료 자체를 쓰지 않는다. 요청한 놀이의 방식은 살리고 "
    "재료만 다른 것으로 바꾼다(같은 만들기 · 던지기 · 꾸미기를 다른 물건으로). 빠진 활동이 음식을 "
    "쓰는 활동이었으면 음식이 들어가지 않는 놀이를 먼저 생각한다. 쓰는 음식 · 재료는 숨기지 말고 "
    "materials · foods 에 그대로 적는다."
)


def recall_message(
    request_texts: Sequence[str], *, chosen: Sequence[str], excluded: Sequence[str]
) -> str:
    """안전 필터 뒤 재호출의 user 메시지. 빠진 이유는 말하지 않는다 (Tool_공통.md §5-2).

    이미 고른 것과 빠진 것을 둘 다 "다시 내지 않는다" 로만 준다 — 이유를 알려 주면 단어만 바꿔
    같은 활동을 다시 내는 법을 배운다. 방향(`RECALL_HINT`)은 사유와 상관없이 늘 같다.
    """
    avoid = "\n".join(f"- {item}" for item in (*chosen, *excluded))
    return (
        f"{_request(request_texts)}\n\n"
        "아래 활동은 다시 내지 않는다. 다른 놀이로 후보 3개를 새로 낸다.\n"
        f"{RECALL_HINT}\n"
        f"{avoid}"
    )


def request_message(request_texts: Sequence[str], *, shown: Sequence[str] = ()) -> str:
    """처음 user 메시지 — 보호자의 요청 그대로. 앞 턴에서 보여 준 추천이 있으면 다시 내지 말라고
    붙인다 (#852). 출력 검증도 같은 놀이 · 장소 · 행사를 거절한다(`SHOWN_BEFORE`)."""
    request = _request(request_texts)
    names = [name for name in shown if name.strip()]
    if not names:
        return request
    listed = "\n".join(f"- {name}" for name in names)
    return (
        f"{request}\n\n"
        "아래는 앞에서 이미 보여 준 추천이다. 같은 놀이 · 장소 · 행사는 다시 내지 않고 다른 것으로 "
        f"낸다.\n{listed}"
    )


def _request(request_texts: Sequence[str]) -> str:
    texts = [text for text in request_texts if text.strip()]
    if not texts:
        return "오늘 아이와 해 볼 놀이를 추천해 줘."
    return "\n".join(texts)


def example_ids(examples: Sequence[ActivityDocRow]) -> dict[str, UUID]:
    """[예시]에 보여 줄 짧은 id(예1 …) → 문서 행 id.

    긴 UUID 를 그대로 보여 주면 모델이 두 행의 id 를 이어 붙인다 — 앞 8자는 한 행, 나머지는 다른
    행(라이브 eval "내일 공원"). 그러면 없는 id 라 묶음째 거절되고, 모델은 무엇이 틀렸는지 몰라
    같은 id 로 다시 내다 단계 한도에 걸린다. 짧은 id 는 이어 붙일 길이가 없다. 출력 tool 이 이
    표로 문서 행 id 로 되돌린다.
    """
    return {f"예{n}": row.id for n, row in enumerate(examples, start=1)}


def _examples(examples: tuple[ActivityDocRow, ...]) -> str:
    if not examples:
        return "[예시]\n이 월령의 예시는 아직 없다."
    lines = [
        "[예시] 결만 참고한다. 그대로 베끼지 않는다. "
        "바탕으로 했으면 그 id(예1 같은)를 evidence 에 그대로 단다."
    ]
    for short_id, row in zip(example_ids(examples), examples, strict=True):
        materials = ", ".join(row.materials) or "없음"
        lines.append(
            f"- id {short_id} · {row.title}: {row.body} (장소 {row.setting.value} · "
            f"준비물 {materials} · 보호자 {row.caregiver_role.value})"
        )
    return "\n".join(lines)


def _conditions(weather: WeatherBrief, *, checked: bool, gate: Gate, dark: bool = False) -> str:
    """오늘 날씨는 판정과 등급 라벨만 준다. 수치는 주지 않는다 (4-2).

    확인하지 못한 날은 "어렵다" 와 나눠 쓴다 — 같은 말이면 모델이 확인 안 한 날씨를 이유 문장에
    쓴다. 위치 · 날씨 어댑터가 없어 묻지 않은 날(`weather.not_asked`)도, 예보 · 특보를 못 읽은
    날(`weather.unchecked`)도 바깥 활동을 연다 — 날씨 이야기만 쓰지 않는다 (#612 멘토 리뷰).
    오늘 판정은 오늘 하는 활동에만 건다. 내일 · 주말은 그날 판정으로 거른다 (#377).
    """
    if not checks_weather(gate):
        # 묻지 않은 날 — 바깥 놀이를 막지 않는다. 확인은 보호자 몫이라 화면 문구가 알린다
        outdoor = (
            "날씨는 확인하지 않았다. 날씨 이야기를 쓰지 않는다. 바깥 활동(outdoor)도 낼 수 있다."
        )
    elif not weather.outdoor_ok:
        # 못 읽은 것이 있어도 읽은 신호가 나쁘면 막는다 — "확인하지 못했다" 보다 먼저
        outdoor = (
            "오늘은 바깥 활동이 어렵다. 오늘 하는 활동은 setting 이 indoor 나 either 인 것만 낸다."
        )
    elif not checked:
        # 못 읽은 날도 바깥 놀이를 막지 않는다
        outdoor = (
            "오늘 날씨는 확인하지 못했다. 날씨 이야기를 쓰지 않는다. "
            "바깥 활동(outdoor)도 낼 수 있다."
        )
    else:
        outdoor = "오늘은 바깥 활동을 할 수 있다."
    labels = " · ".join(f"{key} {value}" for key, value in weather.labels.items())
    if finds_places(gate) and gate.has_location:
        place = (
            "근처 장소는 search_nearby_places 로 찾을 수 있다. 보호자가 다른 지역을 말했으면 "
            "area 에 그 이름을 쓴 그대로 넣어 그 지역에서 찾는다. " + _AREA_ONLY
        )
    elif finds_places(gate):
        place = (
            "위치를 모른다. 보호자가 지역을 말했으면 search_nearby_places 의 area 에 그 이름을 "
            "쓴 그대로 넣어 그 지역 장소를 찾는다. " + _AREA_ONLY + " "
            "말하지 않았으면 특정 장소가 필요한 활동은 내지 않는다."
        )
    elif gate.has_location:
        place = "근처 장소는 찾을 수 없다. 특정 장소가 필요한 활동은 내지 않는다."
    else:
        place = "위치를 모른다. 특정 장소가 필요한 활동은 내지 않는다."
    lines = ["[오늘의 조건]", outdoor]
    if dark:
        # 해가 진 것은 바깥을 막지 않는다 — 밤에 하는 행사도 있고 공원은 보호자와 같이 걷는다 (#800)
        lines.append(
            "지금은 해가 졌다. 오늘 하는 활동이면 어두워도 할 수 있는 것을 고르고 놀이터는 내지 "
            "않는다. 다른 날 활동은 상관없다."
        )
    if labels:
        lines.append(f"날씨: {labels}")
    lines.append(place)
    if finds_events(gate):
        lines.append(
            "그날 열리는 축제 · 공연 · 행사는 search_events 로 찾을 수 있다. 결과에 있는 "
            "이름만 place_name 에 쓰고 day 는 결과의 open_days 에 있는 날을 '11월 20일' 꼴로 "
            "쓴다. 보호자가 지역을 말했으면 area 에 그 이름을 쓴 그대로 넣는다. "
            "시 · 도만 말해도(부산) 행사는 그 시 · 도 전체에서 찾는다."
        )
    return "\n".join(lines)


# 지역 이름과 글자가 같은 다른 뜻의 낱말. 코드는 글자로 가릴 수 없어서 모델이 문장 뜻으로 고른다
# (#657 — "양산 챙겨서" 를 양산시로 찾았다)
_AREA_ONLY = (
    "area 는 보호자가 그 지역에 가거나 그 지역에서 놀려고 말했을 때만 쓴다. "
    "지역 이름과 글자가 같은 물건 · 다른 뜻의 낱말(양산 챙겨서 · 화성에서 온 · 장수풍뎅이)은 "
    "지역이 아니라 비운다. area 에는 지역 이름만 넣는다 — 보호자가 명소로 말했으면(○○해수욕장 · "
    "○○역 · ○○읍) 그 앞의 지역 이름을 넣는다. 다만 그 명소가 정말 그 지역에 있을 때만이다 — "
    "지역 이름으로 시작해도 다른 곳에 있는 명소(김해공항은 부산에 있다)면 비운다."
)


def _now(context: ActivityContext) -> str:
    local = context.now.astimezone(context.timezone)
    weekday = "월화수목금토일"[local.weekday()]
    return f"현재 시각은 {local.isoformat()} ({weekday}요일) · timezone {context.timezone}."


def _fingerprint_parts() -> tuple[str, ...]:
    """프롬프트 지문(#770)의 입력 — 오늘의 조건 구획이 갈리는 조합마다 렌더링한
    system 프롬프트와 tool spec."""
    from app.agents.activity.schemas import tool_defs

    rendered = []
    # 조건 구획이 갈리는 값: 위치 · 장소 · 날씨 · 행사 어댑터, 오늘 판정, 못 읽음
    for location, places, weather, events, outdoor_ok, unread in itertools.product(
        (False, True), repeat=6
    ):
        gate = fixed_gate(has_location=location, places=places, weather=weather, events=events)
        brief = WeatherBrief(outdoor_ok=outdoor_ok, labels={"기온": "적당"}, unread=unread)
        rendered.append(
            build_system_prompt(fixed_context(gate=gate), (), weather=brief)  # type: ignore[arg-type]
        )
    from app.agents.common import tool_loop

    # user 메시지 틀 — 재호출 지시도 모델이 읽는 규칙이다
    messages = (
        request_message([]),
        request_message(["요청"]),
        recall_message(["요청"], chosen=["고른 것"], excluded=["빠진 것"]),
    )
    # 예시가 있으면 [예시] 머리말 지시("결만 참고한다 …")가 붙는다
    # — 고정 더미 행으로 그 갈래도 싣는다
    example = SimpleNamespace(
        id="id",
        title="제목",
        body="본문",
        materials=("준비물",),
        setting=SimpleNamespace(value="indoor"),
        caregiver_role=SimpleNamespace(value="guide"),
    )
    with_example = _examples((example,))  # type: ignore[arg-type]
    return (
        *rendered,
        with_example,
        *messages,
        tools_text(tool_defs.TOOL_DEFINITIONS),
        tool_loop.BAD_JSON,
    )


register_prompt("activity", _fingerprint_parts)
