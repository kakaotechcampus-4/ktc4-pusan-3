"""eval 케이스 YAML 의 모양 (docs/eval/eval-v1.md §3-3).

기대값은 정책이라 PM · 멘토가 코드 없이 읽고 고칠 수 있게 루트 `eval/cases/` 에 YAML 로 둔다.
틀린 칸이 있으면 실행 전에 실패한다 — 오타로 엉뚱한 케이스가 도는 것을 막는다.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.memory.bundles import LOOKUP_EDIT, RECORD_BASE

# Memory 가 가진 tool 전부 — 기대값의 tool 이름 오타를 막는다
MEMORY_TOOLS = RECORD_BASE | LOOKUP_EDIT
MUTATING = "쓰기"  # must_not 에만 — create_ · update_ · delete_ · propose_ tool 전부

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


class ToolsExpect(_Strict):
    """Memory 가 부른 tool (테크스펙 13-1 "경로가 맞나").

    Memory 가 돌지 않은 run 은 아무것도 부르지 않은 것으로 본다 — 순수 요청형은 제품이 Memory 를
    건너뛸 수 있다. 그래서 must_not 만 적은 기대는 통과하고, must 가 있거나 memory_ran 이면
    실패한다.
    unclear 조각(보호자 자신의 얘기 · 무관한 말)은 Memory 가 돌고 저장 tool 을 거절당하는 게 설계된
    경로라(#624) must_not 대신 saved 로 본다.

    must 는 성공한 호출 수의 최소값이다 — 거절된 호출은 쓰지도 읽지도 못했다. must_not 은
    시도만 해도 위반이다. tool 이름과 "쓰기" 뜻은 `test_memory.py` 의 required_tools ·
    forbidden_tools 와 같지만, required_tools 는 시도를 센다는 점이 다르다 — 그쪽은 Memory 가
    얼마나 헤맸는지(재시도 예산)도 보는 평가다. 여러 턴 케이스는 마지막 턴의 호출만 본다.
    """

    must: dict[str, int] = Field(default_factory=dict)  # tool → 성공한 호출 최소 수
    # 부르면 안 되는 tool. "쓰기" = create_ · update_ · delete_ · propose_ 전부
    must_not: tuple[str, ...] = ()
    # Memory 가 반드시 돌아야 한다. must 가 있으면 이미 그렇다 — must 없이 "돌았는가" 만 볼 때
    memory_ran: bool = False

    @model_validator(mode="after")
    def _valid_tools(self) -> "ToolsExpect":
        unknown = sorted(set(self.must) - MEMORY_TOOLS) + sorted(
            set(self.must_not) - MEMORY_TOOLS - {MUTATING}
        )
        if unknown:
            raise ValueError(f"Memory 에 없는 tool 이름: {unknown}")
        zero = sorted(tool for tool, count in self.must.items() if count < 1)
        if zero:
            raise ValueError(f"must 의 횟수는 1 이상 — 안 불러야 하면 must_not 에: {zero}")
        both = sorted(set(self.must) & set(self.must_not))
        if both:
            raise ValueError(f"must 와 must_not 에 같은 tool: {both}")
        return self


class DraftItemsExpect(_Strict):
    """일정 초안에 붙은 준비물. 이름은 글자 포함으로 본다("도시락" 은 "점심 도시락" 에 맞는다)."""

    names: tuple[str, ...] = Field(min_length=1)
    min: int = Field(default=1, ge=1)  # names 중 이만큼은 초안에 있어야 한다

    @model_validator(mode="after")
    def _min_fits(self) -> "DraftItemsExpect":
        # 빈 이름은 모든 준비물에 맞고, 겹친 이름은 한 품목으로 min 을 채운다
        if any(not name.strip() for name in self.names):
            raise ValueError("names 에 빈 이름이 있다")
        if len(set(self.names)) != len(self.names):
            raise ValueError(f"names 가 겹친다: {self.names}")
        if self.min > len(self.names):
            raise ValueError(f"min {self.min} > names {len(self.names)}개")
        return self


class Expect(_Strict):
    route: RouteExpect | None = None
    saved: SavedExpect | None = None
    suggestions: dict[Agent, SuggestionWant] = Field(default_factory=dict)
    guidance: tuple[str, ...] = ()  # 이 안내 코드가 모두 나가야 한다
    event_drafts_min: int | None = None
    draft_items: DraftItemsExpect | None = None  # 일정 초안의 준비물
    questions_max: int | None = None
    # 삭제 후보 제안(propose_delete)이 나와야 한다(True) / 나오면 안 된다(False). 지우는 것 자체는
    # 불변식 I15 가 본다 — 여기는 "후보를 냈나" 만
    delete_proposed: bool | None = None
    never_say: tuple[str, ...] = ()  # 어떤 출력 문장에도 없어야 할 글자 (카나리아 · 단정 표현)
    must_say_any: tuple[str, ...] = ()  # 출력 문장 어딘가에 이 중 하나는 있어야 한다
    tools: ToolsExpect | None = None  # Memory 가 부른 tool


class ProbeCase(_Strict):
    id: str = Field(pattern=r"^P-[A-Z]+-\d{2}$")
    ability: Ability
    rule: str
    severity: Severity  # 둘 다 반복 전부 통과. safety 는 첫 실패에서 멈춘다
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


# ── W · 일주일 시나리오 (docs/eval/eval-v1.md §5) ─────────────────────────────

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
StepKind = Literal["say", "reply", "approve", "correct", "check"]
# correct 단계는 기록을 고친다(`week_runner._correct`). 기록에는 once_only 만 받는다 —
# "잘못된 기록" 은 기록 지우기로 바꿨고(#736), outdated 는 기억에만 쓰는 값이다.
CorrectVerdict = Literal["once_only"]
_AT = r"^(mon|tue|wed|thu|fri|sat|sun) ([01]\d|2[0-3]):[0-5]\d$"


class WeekSeed(_Strict):
    """시작 전 기록 한 건 — 주의 월요일(D)에서 days_before_D 일 앞에 DB 에 직접 심는다.

    fields 는 관찰 표의 칸 이름 그대로다(subject · activity · topic · action …). 승격(Curator)은
    첫 입력 뒤에 돌므로 같은 subject 로 적어야 한 프로필로 묶인다.
    """

    domain: Domain
    days_before_D: int = Field(ge=0)
    fields: dict[str, Any]
    tag: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")


class WeekWorld(_Strict):
    months: int = Field(default=48, ge=0, le=96)
    allergies: tuple[str, ...] = ()
    nickname: str = Field(min_length=1)  # 지어낸 별명 — I13 카나리아
    parents: tuple[str, ...] = Field(min_length=1)  # 보호자 이름 키 (Step.by)
    rain_days: tuple[Weekday, ...] = ()  # 이 요일만 강한 비 (날씨 어댑터)
    seeds: tuple[WeekSeed, ...] = ()


class WeekCheck(_Strict):
    """끝 상태 점검 — 모두 DB 를 본다."""

    confirmed: tuple[str, ...] = ()  # 이 글자를 품은 관심(merge_key)이 confirmed
    not_confirmed: tuple[str, ...] = ()  # 이 글자를 품은 관심이 confirmed 가 아니다
    no_event_except: tuple[str, ...] = ()  # 일정 행은 이 승인 단계(tag)가 만든 것뿐
    corrected_not_cited: tuple[
        CorrectVerdict, ...
    ] = ()  # 이 판정으로 고친 기록이 뒤 추천 근거에 없다
    # 이 글자를 품은 관심이 confirmed 가 된 뒤, 어떤 추천이 그 관심(profile_affinity)을 근거로
    # 인용했다 — "관심 확정 → 그 근거로 추천" (리뷰 11-08)
    cited_affinity: tuple[str, ...] = ()


class Step(_Strict):
    at: str = Field(pattern=_AT)  # "mon 08:10" — 그 주의 요일 + 시:분(KST)
    by: str
    kind: StepKind = "say"
    say: str | None = None  # {d+N} 은 주의 월요일 + N 일의 "M월 D일" 로 바뀐다
    tag: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    ability: tuple[str, ...] = ()
    about: Literal["child", "caregiver"] = "child"  # caregiver 면 I9
    location: bool = False  # True 면 입력에 흐린 좌표(부산)를 싣는다 — 날씨가 필요한 단계
    expect: Expect = Expect()
    approve_of: str | None = None  # 이 단계(tag)가 낸 일정 초안을 승인한다
    correct_of: str | None = None  # 이 단계(tag)가 저장한 기록을 고친다
    verdict: CorrectVerdict | None = None
    match: str | None = (
        None  # 고칠 기록을 정규식으로 좁힌다(원문 제외 칸에서 찾는다). 없으면 그 단계의 관찰 전부
    )
    check: WeekCheck | None = None

    @model_validator(mode="after")
    def _kind_fields(self) -> "Step":
        if self.kind in ("say", "reply") and not self.say:
            raise ValueError(f"{self.tag}: say/reply 단계는 say 가 있어야 한다")
        if self.kind == "approve" and not self.approve_of:
            raise ValueError(f"{self.tag}: approve 단계는 approve_of 가 있어야 한다")
        if self.kind == "correct" and not (self.correct_of and self.verdict):
            raise ValueError(f"{self.tag}: correct 단계는 correct_of 와 verdict 가 있어야 한다")
        if self.kind == "check" and self.check is None:
            raise ValueError(f"{self.tag}: check 단계는 check 가 있어야 한다")
        if self.expect.tools is not None or self.expect.draft_items is not None:
            # W 는 API 로 돌아 Memory tool 기록 · 초안 준비물을 모으지 않는다 — 적으면 늘 실패한다
            raise ValueError(f"{self.tag}: W 단계는 tools · draft_items 를 판정하지 않는다 (P 만)")
        return self


class WeekCase(_Strict):
    id: str = Field(pattern=r"^W-[A-Z]$")
    persona: str
    world: WeekWorld
    steps: tuple[Step, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "WeekCase":
        tags = [step.tag for step in self.steps]
        dup = sorted({t for t in tags if tags.count(t) > 1})
        if dup:
            raise ValueError(f"{self.id}: 단계 tag 가 겹친다: {dup}")
        seed_tags = [seed.tag for seed in self.world.seeds]
        if len(set(seed_tags)) != len(seed_tags):
            raise ValueError(f"{self.id}: 시작 기록 tag 가 겹친다")
        seen: set[str] = set()
        last = -1
        for step in self.steps:
            if step.by not in self.world.parents:
                raise ValueError(f"{self.id}/{step.tag}: by={step.by!r} 는 world.parents 에 없다")
            minute = WEEKDAYS.index(step.at[:3]) * 1440 + int(step.at[4:6]) * 60 + int(step.at[7:9])
            if minute < last:
                raise ValueError(f"{self.id}/{step.tag}: 단계 시각이 앞 단계보다 이르다")
            last = minute
            for ref in (step.approve_of, step.correct_of):
                if ref is not None and ref not in seen:
                    raise ValueError(f"{self.id}/{step.tag}: {ref!r} 는 앞 단계 tag 가 아니다")
            seen.add(step.tag)
        return self


def load_week_text(text: str) -> WeekCase:
    return WeekCase.model_validate(yaml.safe_load(text))


def load_week(path: Path) -> WeekCase:
    return load_week_text(path.read_text(encoding="utf-8"))


def load_weeks(directory: Path = CASES_DIR / "week") -> list[WeekCase]:
    return [load_week(path) for path in sorted(directory.glob("*.yaml"))]
