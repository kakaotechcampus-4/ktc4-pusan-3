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
- 정확히 3개를 낸다.
- 쓰는 물건과 장소 성격을 빠짐없이 적는다. 거르는 건 코드가 한다.
- 싫어한다고 나온 활동은 다시 내지 않는다. 피해 골랐다면 무엇을 피했는지 reason 에 쓴다.
- 근거마다 note 를 쓴다. 원문을 옮기지 않는다.
- 예시는 결을 보여주는 것이다. 그대로 베끼지 않는다.
"""

from app.agents.activity.context import ActivityContext
from app.agents.activity.store.ports import ActivityDocRow


def build_system_prompt(context: ActivityContext, examples: tuple[ActivityDocRow, ...]) -> str:
    """system 메시지 본문. examples 는 search_activity_doc 결과다."""
    raise NotImplementedError("Activity Agent 실구현 때 작성")
