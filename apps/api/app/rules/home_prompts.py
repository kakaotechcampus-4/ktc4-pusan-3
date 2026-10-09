"""홈의 시간대별 예상 질문 버튼 (F-15).

순수 함수. 시각 · 요일 · 별명만으로 정한다 — 개인화는 버튼을 누른 뒤 Agent 가 한다 (#249).
버튼에 보이는 문구는 "…추천해드릴까요?" 꼴이다.

🚨 버튼을 누르면 클라이언트는 문장이 아니라 `prompt_key` 만 보낸다. 추천 API 가
   `prompt_request(agent, key)` 로 이 표의 문장을 꺼내 Agent 의 `request_texts` 에 넣는다.
   본문의 문장을 그대로 받으면 Supervisor 안전 사전검사를 거치지 않은 문장으로 Agent 가 돈다
   (#249 · #286 리뷰). 표에 없는 조합은 None 이다.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Agent = Literal["food", "activity"]

_SHOWN = " 추천해드릴까요?"

# 🚨 키를 늘리거나 바꾸면 웹의 `PROMPT_KEYS`(apps/web/src/lib/api/types.ts)도 같이 고친다.
#    웹은 모르는 키를 버리고 키 없이 요청해서, 한쪽만 바뀌면 그 버튼의 맥락이 조용히 빠진다 (#296).
PromptKey = Literal["breakfast", "lunch", "snack", "dinner", "next_breakfast", "play"]

# (Agent, 키) → Agent 에 넘길 요청 문장. 별명은 넣지 않는다 — 화면 문구에만 쓴다.
_REQUESTS: dict[tuple[str, str], str] = {
    ("food", "breakfast"): "아침 추천해줘",
    ("food", "lunch"): "점심 추천해줘",
    ("food", "snack"): "간식 추천해줘",
    ("food", "dinner"): "저녁 추천해줘",
    ("food", "next_breakfast"): "내일 아침 추천해줘",
    ("activity", "play"): "놀이 추천해줘",
}

_PLAY: tuple[PromptKey, str] = ("play", "{w} 할 놀이")

# (시작 시각, 시간대). 끼니에 맞춰 나눈다. 20시부터 다음 날 6시 전까지는 밤이다.
_SLOTS = ((6, "morning"), (10, "lunch"), (14, "snack"), (17, "dinner"), (20, "night"))

# 시간대 → {Agent: (키, 문구 줄기)}. {s} 는 "하나가" · "민준이가", {w} 는 "하나랑" 꼴.
# 놀이는 아이가 깨어 있는 낮 시간에만 묻는다 — 주중 아침(등원 · 출근 준비)과 밤에는 묻지 않는다.
_STEMS: dict[str, dict[Agent, tuple[PromptKey, str]]] = {
    "morning": {"food": ("breakfast", "{s} 먹을 아침")},
    "lunch": {"food": ("lunch", "{s} 먹을 점심"), "activity": _PLAY},
    "snack": {"food": ("snack", "{s} 먹을 간식"), "activity": _PLAY},
    "dinner": {"food": ("dinner", "{s} 먹을 저녁"), "activity": _PLAY},
    "night": {"food": ("next_breakfast", "{s} 먹을 내일 아침")},
}
_WEEKEND_MORNING: dict[Agent, tuple[PromptKey, str]] = {
    "food": ("breakfast", "{s} 먹을 아침"),
    "activity": _PLAY,
}

# 별명 끝이 한글이 아니면 받침을 알 수 없어 조사를 맞출 수 없다 — 이름 없이 쓴다.
_NO_NICKNAME = "우리 아이"


@dataclass(frozen=True)
class AgentPrompt:
    agent: Agent
    text: str
    prompt_key: PromptKey


def agent_prompts(nickname: str, now: datetime) -> list[AgentPrompt]:
    """now 는 한국 시각이다. 주중과 주말은 아침에만 다르다 — 주말 아침에는 놀이도 묻는다."""
    subject, together = _particles(nickname)
    slot = _slot(now.hour)
    weekend = now.weekday() >= 5
    stems = _WEEKEND_MORNING if slot == "morning" and weekend else _STEMS[slot]
    return [
        AgentPrompt(agent=agent, text=stem.format(s=subject, w=together) + _SHOWN, prompt_key=key)
        for agent, (key, stem) in stems.items()
    ]


def prompt_request(agent: str, prompt_key: str) -> str | None:
    """추천 API 가 받은 (agent, prompt_key) 를 Agent 에 넘길 문장으로 바꾼다. 표에 없으면 None."""
    return _REQUESTS.get((agent, prompt_key))


def _slot(hour: int) -> str:
    current = "night"  # 0 ~ 6시 전은 전날 밤에 이어진다
    for start, name in _SLOTS:
        if hour >= start:
            current = name
    return current


def _particles(nickname: str) -> tuple[str, str]:
    """("민준이가", "민준이랑") · ("하나가", "하나랑"). 받침이 있으면 "이" 가 붙는다."""
    name = nickname.strip()
    last = name[-1] if name else ""
    if not ("가" <= last <= "힣"):
        return f"{_NO_NICKNAME}가", f"{_NO_NICKNAME}랑"
    if (ord(last) - ord("가")) % 28:
        return f"{name}이가", f"{name}이랑"
    return f"{name}가", f"{name}랑"
