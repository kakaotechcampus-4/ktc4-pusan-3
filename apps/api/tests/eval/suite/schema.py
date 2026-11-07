"""eval 케이스 YAML 의 모양 (docs/eval/eval-v1.md §3-3).

기대값은 정책이라 PM · 멘토가 코드 없이 읽고 고칠 수 있게 루트 `eval/cases/` 에 YAML 로 둔다.
틀린 칸이 있으면 실행 전에 실패한다 — 오타로 엉뚱한 케이스가 도는 것을 막는다.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# apps/api/tests/eval/suite/schema.py → 저장소 루트
CASES_DIR = Path(__file__).resolve().parents[5] / "eval" / "cases"

Domain = Literal["food", "health", "education", "activity", "routine"]
Agent = Literal["food", "activity", "growth", "health"]
Ability = Literal["이해", "기록", "기억", "행동", "공지", "주입"]
Severity = Literal["safety", "quality"]
SuggestionWant = Literal["personalized", "general", "none", "any"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SeedObservation(_Strict):
    """세계에 미리 심는 관찰 한 건. 날짜는 실행일에서 days_ago 만큼 앞이다."""

    domain: Domain
    days_ago: int = Field(ge=0)
    fields: dict[str, Any]


class SeedAffinity(_Strict):
    """세계에 미리 심는 관심(profile_affinity) 한 줄.

    21일 archived 는 제품의 기억 포트가 판정한다.
    """

    domain: Domain
    merge_key: str
    state: Literal["candidate", "confirmed", "archived"]
    polarity: int | None = 1
    strength: float = 0.8
    last_days_ago: int = Field(ge=0)
    observed_days: int = Field(ge=1)


class World(_Strict):
    months: int = Field(default=48, ge=0, le=96)
    allergies: tuple[str, ...] = ()
    consent: bool = True
    rain: bool = False  # True 면 Activity 날씨가 하루 종일 강한 비 · 위치는 부산 격자
    observations: tuple[SeedObservation, ...] = ()
    affinities: tuple[SeedAffinity, ...] = ()


class Turn(_Strict):
    say: str = Field(min_length=1)
    about: Literal["child", "caregiver"] = "child"  # caregiver 면 I9 — 아이 기록 0행
    reply: bool = False  # 앞 턴의 Memory 되묻기에 답한다 (reply_to 이어받기)


class RouteExpect(_Strict):
    intent: Literal["record", "request", "mixed"] | None = None
    include: tuple[Agent, ...] = ()
    exclude: tuple[Agent, ...] = ()


class SavedExpect(_Strict):
    child_rows: int | None = None  # 이 run 이 만든 아이 관찰이 정확히 이 수
    min_rows: int | None = None
    domains: tuple[Domain, ...] = ()  # 각 도메인에 1행 이상
    days_ago: int | None = None  # 새로 만든 관찰의 observed_on 이 모두 실행일 - N 일


class Expect(_Strict):
    route: RouteExpect | None = None
    saved: SavedExpect | None = None
    suggestions: dict[Agent, SuggestionWant] = Field(default_factory=dict)
    guidance: tuple[str, ...] = ()  # 이 안내 코드가 모두 나가야 한다
    event_drafts_min: int | None = None
    questions_max: int | None = None
    never_say: tuple[str, ...] = ()  # 어떤 출력 문장에도 없어야 할 글자 (카나리아 · 단정 표현)
    must_say_any: tuple[str, ...] = ()  # 출력 문장 어딘가에 이 중 하나는 있어야 한다


class ProbeCase(_Strict):
    id: str = Field(pattern=r"^P-[A-Z]+-\d{2}$")
    ability: Ability
    rule: str
    severity: Severity  # safety → 반복 전부 통과 · quality → 3번 중 2번
    headline: int | None = Field(default=None, ge=1, le=10)
    smoke: bool = False
    pair: str | None = None  # 짝 비교(같은 입력 · 다른 기억) 이름 — 보고에서 나란히 본다
    world: World = World()
    turns: tuple[Turn, ...] = Field(min_length=1)
    expect: Expect

    @model_validator(mode="after")
    def _first_turn_is_not_reply(self) -> "ProbeCase":
        if self.turns[0].reply:
            raise ValueError(f"{self.id}: 첫 턴은 되묻기 답일 수 없다")
        return self


def load_probes_text(text: str) -> list[ProbeCase]:
    cases = [ProbeCase.model_validate(item) for item in yaml.safe_load(text)]
    ids = [case.id for case in cases]
    duplicated = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicated:
        raise ValueError(f"케이스 id 가 겹친다: {duplicated}")
    return cases


def load_probes(path: Path = CASES_DIR / "probes.yaml") -> list[ProbeCase]:
    return load_probes_text(path.read_text(encoding="utf-8"))
