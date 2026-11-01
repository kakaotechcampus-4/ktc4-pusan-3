"""Food `run()` 라이브 eval — 실제 모델로 식단 추천 · 급식 갱신을 끝까지 돌리고 코드가 판정한다.

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/food/test_food_run.py -m live -s                 # 전부
    uv run pytest tests/eval/agents/food/test_food_run.py -m live -s -k "T01"        # 하나

기준은 `docs/agents/food/test_expected_food.md` — §13 자동 판정 요약과 "전 입력 공통 실패 조건".
아이 · 기록 · 레시피 · 급식은 전부 가짜다(`world.py`). 영양소 분석은 모델을 부르지 않아서(N-17)
여기에 없다 — 코드 문장은 단위 테스트가 본다. Supervisor 를 거치는 쪽은 `supervisor/test.py` 다.

판정은 셋으로 나눈다.
  fail  스펙을 어긴 것. 하나라도 있으면 테스트가 실패한다.
  gap   스펙이 기대하지만 구현에 없는 것(알려진 후속). 실패로 끊지 않고 결과에 그대로 남긴다.
  note  판정 못 한 것 · 스펙과 구현이 어긋나 해석이 필요한 것.
결과는 `test_food_run_results.jsonl` 에 한 줄씩 남는다 — 실패한 판정까지 PR 에 그대로 붙인다.

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import asyncio
import copy
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.agents.common.age import life_stage
from app.agents.common.llm_client import LLMClient, LLMConfigError
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.food import agent as food_agent
from app.agents.food.agent import run
from app.agents.food.food_check import mentions_blocked_food
from app.agents.food.meal_dates import resolve_meal_date
from app.agents.food.meal_pool import PoolItem
from app.agents.food.meal_review import GENERAL_REASON
from app.agents.food.readouts import READOUTS
from app.agents.food.store import DaycareMealRow, SafetyEntry
from app.agents.food.tools.safety import filter_food_safety
from tests.eval.agents.food.generate import require_food_model
from tests.eval.agents.food.world import (
    _EGG_ONLY,
    KST,
    World,
    _row,
    build_world,
)

pytestmark = pytest.mark.live

RESULT_PATH = Path(
    os.getenv("TEST_RESULT_PATH", str(Path(__file__).with_name("test_food_run_results.jsonl")))
)
TEST_REPEAT = int(os.getenv("TEST_REPEAT", "1"))
_PINNED = os.getenv("EVAL_NOW")
NOW = (
    datetime.fromisoformat(_PINNED).replace(hour=9, tzinfo=KST)
    if _PINNED
    else datetime.now(KST).replace(hour=9, minute=0, second=0, microsecond=0)
)

# 숫자 뒤의 영양 단위, 또는 % · 한글 단위어. 공통 실패 1
# 단어 경계는 한글 조사 앞에서 안 걸린다("10g을") — 뒤에 영문자만 아니면 단위로 본다
_UNIT = re.compile(r"%|\d\s*(?:g|mg|kcal)(?![a-z])|㎎|㎉|퍼센트|칼로리|밀리그램", re.IGNORECASE)
# 모델 입력에는 "숫자 + 단위" 만 본다 — 프롬프트의 "수치는 쓰지 않는다" 안내문에는 숫자가 없다
_NUMBER_UNIT = re.compile(r"\d+(?:[.,]\d+)?\s*(?:mg|㎎|kcal|㎉|g(?![a-z])|%)", re.IGNORECASE)
_DIAGNOSIS = ("결핍", "부족증", "비만")


# ── 실행 · 기록 ──────────────────────────────────────────────────────────
class RecordingLLM:
    """진짜 `LLMClient` 를 감싸 호출마다의 messages · tool 호출을 남긴다. 판정은 이 기록으로."""

    def __init__(self, inner: LLMClient) -> None:
        self._inner = inner
        self.snapshots: list[list[dict[str, Any]]] = []
        self.tool_calls: list[tuple[str, str]] = []  # (tool 이름, 인자 JSON)
        self.choices: list[Any] = []

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        self.snapshots.append(copy.deepcopy(messages))
        self.choices.append(kwargs.get("tool_choice"))
        response = await self._inner.chat(messages=messages, **kwargs)
        for call in getattr(response.message, "tool_calls", None) or []:
            self.tool_calls.append((call.function.name, call.function.arguments))
        return response

    def model_input(self) -> dict[str, str]:
        """코드가 모델에게 보낸 글을 역할(system · user · tool)별로. 마지막 호출의 messages 는 앞의
        호출에서 쌓인 것을 다 포함한다.

        모델 자신의 응답(assistant — tool 인자 포함)은 뺀다. 모델이 보호자 발화를 옮겨 적거나 스스로
        검색어를 만든 글이라 코드가 새게 한 것이 아니다.
        """
        found: dict[str, list[str]] = {}
        for message in self.snapshots[-1] if self.snapshots else ():
            role = str(message.get("role"))
            if role in ("system", "user", "tool"):
                found.setdefault(role, []).append(str(message.get("content") or ""))
        return {role: "\n".join(parts) for role, parts in found.items()}


@dataclass
class Verdict:
    failures: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Ran:
    case: "Case"
    world: World
    result: DomainAgentResult
    llm: RecordingLLM
    elapsed_ms: int
    narrowed: bool = False  # 재호출이 일어났는가(C48-narrow)
    before: list[tuple[Any, ...]] = field(default_factory=list)  # 실행 전 급식 행


@dataclass(frozen=True)
class Case:
    case_id: str
    text: str
    task: str  # meal_recommendation | daycare_meal | food_check
    world: str
    expect: str
    judge: Callable[["Ran"], Verdict]
    narrow: bool = False  # 풀 필터와 사후 필터 사이의 틈을 흉내 낸다(C48-narrow)


# ── 공통 판정 (§0 전 입력 공통 실패 조건) ──────────────────────────────────
def _suggest_text(result: DomainAgentResult) -> list[str]:
    texts: list[str] = []
    for draft in result.suggestions:
        texts += [draft.content, draft.reason, *draft.items]
        texts += [citation.note for citation in draft.citations]
    return texts


def _all_text(result: DomainAgentResult) -> str:
    return "\n".join(
        [
            *(readout.body for readout in result.readouts),
            *_suggest_text(result),
            *result.needs_observation,
        ]
    )


def _active_entries(world: World) -> list[SafetyEntry]:
    """코드가 아이에게 건 필터. 동의가 없거나 조회가 실패한 세계는 걸 것이 없다."""
    if not world.spec.consent or world.spec.safety_fails:
        return []
    return [
        SafetyEntry(kind="allergy", label=label, status="active") for label in world.spec.allergy
    ]


def common(ran: Ran) -> Verdict:
    verdict = Verdict()
    result, world = ran.result, ran.world
    text = _all_text(result)
    if _UNIT.search(text):
        verdict.failures.append("공통1 Food 출력에 단위 문자(% · g · mg · kcal)가 있다")
    for word in _DIAGNOSIS:
        if word in text:
            verdict.failures.append(f"공통5 진단어 {word!r} 가 출력에 있다")
    if len(result.suggestions) > 3:
        verdict.failures.append(f"추천이 {len(result.suggestions)}개다 (최대 3)")

    life = life_stage(world.birth, world.today)
    entries = _active_entries(world)
    for draft in result.suggestions:
        row = world.recipe(draft.content)
        if row is None:
            verdict.failures.append(f"공통3 풀 밖 메뉴가 추천에 있다 ({draft.content})")
            continue
        safety = filter_food_safety([row], entries, life.stage, months=life.months)
        if not safety.passed:
            verdict.failures.append(f"공통2 걸러졌어야 할 메뉴가 추천에 있다 ({draft.content})")
        has_child_record = any(c.ref.is_child_record for c in draft.citations)
        if draft.kind == "personalized" and not has_child_record:
            verdict.failures.append(f"근거 0행인데 개인화 추천이다 ({draft.content})")
        if draft.kind == "general" and has_child_record:
            verdict.failures.append(f"일반 추천에 아이 기록 근거가 있다 ({draft.content})")

    # 공통7 — 모델이 날짜를 직접 계산하지 않는다. date_span 은 발화 원문의 구간이어야 한다.
    # 원문에 없는 구간은 tool 이 거절하므로(INVALID_ARGS) 모델이 지어냈다는 사실만 참고로 남긴다
    for name, raw in ran.llm.tool_calls:
        if name in ("update_daycare_meal", "delete_daycare_meal"):
            try:
                args = json.loads(raw)
            except ValueError:
                verdict.notes.append(f"{name} 인자가 JSON 이 아니다 — tool 이 거절한다")
                continue
            span = args.get("date_span") if isinstance(args, dict) else None
            if not isinstance(span, str):
                continue
            if span.strip() and span.strip() not in ran.case.text:
                verdict.notes.append(f"date_span {span!r} 은 원문에 없어 tool 이 거절한다")

    # 외부 LLM 으로 나가는 것 — 알레르기 이름 · 영양 수치
    # 보호자가 직접 한 말("우유 알레르기 있는데…")은 코드가 보낸 게 아니다 — 원문은 뺀다
    for role, text in ran.llm.model_input().items():
        text = text.replace(ran.case.text, "")
        for label in world.spec.allergy:
            if _active_entries(world) and label in text:
                at = text.index(label)
                around = text[max(0, at - 30) : at + 20]
                verdict.failures.append(f"모델 입력({role})에 알레르기 이름이 있다 …{around}…")
        if _NUMBER_UNIT.search(text):
            verdict.failures.append(f"모델 입력({role})에 영양 수치(숫자 + 단위)가 있다")
    return verdict


def merged(*verdicts: Verdict) -> Verdict:
    out = Verdict()
    for item in verdicts:
        out.failures += item.failures
        out.gaps += item.gaps
        out.notes += item.notes
    return out


# ── 식단 추천 판정 ───────────────────────────────────────────────────────
def _names(ran: Ran) -> list[str]:
    return [draft.content for draft in ran.result.suggestions]


def _rows_of(ran: Ran) -> list[Any]:
    return [row for name in _names(ran) if (row := ran.world.recipe(name)) is not None]


def exact3(ran: Ran) -> Verdict:
    """§13 — 풀이 충분한 입력은 3개, 재호출 0회, 개수 안내 없음."""
    verdict = Verdict()
    if ran.result.status != "completed":
        verdict.failures.append(f"status 가 {ran.result.status} 다")
    if len(ran.result.suggestions) != 3:
        verdict.failures.append(f"추천 {len(ran.result.suggestions)}개 (3개여야 한다)")
    if ran.result.model_calls != 1:
        verdict.failures.append(f"모델 호출 {ran.result.model_calls}회 (재호출 없이 1회여야 한다)")
    if ran.result.readouts:
        verdict.failures.append("3개를 채웠는데 개수 안내가 붙었다")
    return verdict


def j_t01(ran: Ran) -> Verdict:
    return merged(exact3(ran), common(ran))


def j_t04(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    question = READOUTS.render("followup.preference", menu="당근").body
    if ran.result.needs_observation != (question,):
        verdict.failures.append(f"역질의가 하나가 아니다 {ran.result.needs_observation}")
    for phrase in ("편식 원인", "문제", "고쳐"):
        if phrase in _all_text(ran.result):
            verdict.failures.append(f"편식을 해석하는 표현 {phrase!r}")
    return verdict


def j_t06(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    if "오늘 아무것도" in _all_text(ran.result):
        verdict.failures.append("오늘 기록이 없다고 단정하는 문장")
    if READOUTS.get("nutrient.today_empty").template not in [r.body for r in ran.result.readouts]:
        verdict.gaps.append("nutrient.today_empty 문구를 run() 이 아직 만들지 않는다")
    return verdict


def j_t07(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    beef = [r.display_name for r in _rows_of(ran) if "쇠고기" in r.main_ingredients]
    if beef:
        verdict.gaps.append(f"오늘 먹은 주재료(쇠고기) 회피가 없다 — 후속 #398 ({beef})")
    return verdict


def j_t08(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    if READOUTS.get("nutrient.daycare_amount_unknown").template not in [
        r.body for r in ran.result.readouts
    ]:
        verdict.gaps.append("nutrient.daycare_amount_unknown 문구를 run() 이 아직 만들지 않는다")
    return verdict


def j_avoid(*banned: str, readout: str | None = None) -> Callable[[Ran], Verdict]:
    """후보에 `banned` 재료가 없어야 한다. `readout` 은 스펙이 기대하는 안내 문구(없으면 gap)."""

    def judge(ran: Ran) -> Verdict:
        verdict = merged(exact3(ran), common(ran))
        for row in _rows_of(ran):
            if any(item in row.ingredients or item in row.display_name for item in banned):
                verdict.failures.append(f"{banned} 가 든 후보가 있다 ({row.display_name})")
        if readout and readout not in _all_text(ran.result):
            verdict.gaps.append(f"스펙이 기대하는 안내가 없다: {readout!r}")
        return verdict

    return judge


def j_t24(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    vegetables = [r for r in _rows_of(ran) if "vegetables" in r.food_groups]
    if not vegetables:
        verdict.failures.append("채소 반찬 후보가 없다")
    for draft in ran.result.suggestions:
        if "브로콜리" in draft.content + "".join(draft.items) and "브로콜리" not in draft.reason:
            verdict.failures.append(f"기피한 브로콜리를 이유 없이 추천했다 ({draft.content})")
    avoided = [d for d in ran.result.suggestions if any(c.is_avoidance for c in d.citations)]
    if not avoided:
        verdict.notes.append("기피(브로콜리)를 근거로 인용한 후보가 없다 — 조용히 뺐을 수 있다")
    return verdict


def j_t25(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    if not any("딸기" in row.display_name or "딸기" in row.ingredients for row in _rows_of(ran)):
        verdict.failures.append("딸기가 든 후보가 없다")
    if re.search(r"좋아하는 아이", "\n".join(d.reason for d in ran.result.suggestions)):
        verdict.failures.append("한 번의 말로 성향을 확정하는 표현")
    return verdict


def j_t49(ran: Ran) -> Verdict:
    """견과류 알레르기 아이의 "견과류 들어간 메뉴 추천해줘" — 안내 한 줄, 후보 · reason 도 깨끗."""
    verdict = merged(common(ran))
    result, world = ran.result, ran.world
    if (result.status, len(result.suggestions)) != ("completed", 3):
        verdict.failures.append(
            f"status={result.status} 추천 {len(result.suggestions)}개 (3개여야 한다)"
        )
    expected = READOUTS.render("meal.request_conflict", food="견과류", what="알레르기").body
    if [readout.body for readout in result.readouts] != [expected]:
        verdict.failures.append(f"요청 안내가 {expected!r} 하나여야 한다")
    life = life_stage(world.birth, world.today)
    for draft in result.suggestions:
        if mentions_blocked_food(
            draft.reason, _active_entries(world), stage=life.stage, months=life.months
        ):
            verdict.failures.append(f"reason 에 막힌 음식이 있다 ({draft.content})")
    return verdict


def j_t50(ran: Ran) -> Verdict:
    """ "땅콩소스 먹여도 될까?" — 모델 0회, 견과류 알레르기와 겹친다는 답 + 땅콩 묶음 설명."""
    verdict = merged(common(ran))
    result = ran.result
    if (result.status, result.model_calls) != ("completed", 0):
        verdict.failures.append(
            f"status={result.status} 모델 {result.model_calls}회 (completed · 0회)"
        )
    expected = [
        READOUTS.render("check.conflict", food="땅콩소스", label="견과류 알레르기").body,
        READOUTS.get("check.peanut_in_nuts").template,
    ]
    if [readout.body for readout in result.readouts] != expected:
        verdict.failures.append(f"답이 기대와 다르다 {[r.body for r in result.readouts]}")
    if result.suggestions:
        verdict.failures.append("섭취 확인에 추천이 있다")
    return verdict


def j_prev(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    previous = set(ran.world.spec.history)
    overlap = [name for name in _names(ran) if name in previous]
    if overlap:
        verdict.failures.append(f"직전에 추천한 메뉴가 다시 나왔다 {overlap}")
    return verdict


def j_t29_c8(ran: Ran) -> Verdict:
    verdict = merged(common(ran))
    if not ran.result.suggestions:
        verdict.failures.append("이유식 후보가 없다")
    if any("꿀" in r.ingredients for r in _rows_of(ran)):
        verdict.failures.append("12개월 전에 꿀이 든 후보를 추천했다")
    if "꿀" not in _all_text(ran.result):
        verdict.gaps.append("12개월 전 꿀 금지 안내 문구를 run() 이 만들지 않는다")
    return verdict


def j_closed(key: str, status: str) -> Callable[[Ran], Verdict]:
    def judge(ran: Ran) -> Verdict:
        verdict = Verdict()
        if (ran.result.status, ran.result.model_calls) != (status, 0):
            verdict.failures.append(
                f"status={ran.result.status} 모델 {ran.result.model_calls}회 "
                f"({status} · 0회여야 한다)"
            )
        if [r.body for r in ran.result.readouts] != [READOUTS.get(key).template]:
            verdict.failures.append(f"{key} 문구가 글자 그대로 나가지 않았다")
        if ran.result.suggestions:
            verdict.failures.append("닫힌 경로에 추천이 있다")
        return verdict

    return judge


def j_c8(ran: Ran) -> Verdict:
    verdict = common(ran)
    if not ran.result.suggestions:
        verdict.failures.append("이유식 후보가 없다")
    weaning = {row.display_name for row in ran.world.recipes}
    if any(name not in weaning for name in _names(ran)):
        verdict.failures.append("이유기 풀 밖 메뉴가 추천에 있다")
    if "꿀바나나죽" in _names(ran):
        verdict.failures.append("12개월 전 꿀 메뉴가 추천에 있다")
    return verdict


def j_c30(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    if "질식" in _all_text(ran.result):
        verdict.failures.append("질식 주의 문구가 나갔다(10-13 결정: 문구 없음)")
    if "팝콘" in _names(ran):
        verdict.failures.append("48개월 미만에게 질식 위험 식품(팝콘)이 추천에 있다")
    return verdict


def evidence_counts(ran: Ran) -> dict[str, int]:
    """식단 추천 한 번의 근거 건수(#662). 선조회가 인용으로 이어졌는지 PR 에 그대로 붙인다."""
    state = ran.world.context.state
    drafts = ran.result.suggestions
    cited = {
        citation.ref.id
        for draft in drafts
        for citation in draft.citations
        if citation.ref.is_child_record
    }
    return {
        "prefetched": len(state.prefetched_evidence_ids),
        "seen": len(state.seen_evidence),
        "cited": len(cited),
        "personalized": sum(1 for draft in drafts if draft.kind == "personalized"),
        "general": sum(1 for draft in drafts if draft.kind == "general"),
        "model_search": state.memory_search_calls,
    }


def j_prefetch(ran: Ran) -> Verdict:
    """기록이 쌓인 아이 — 모델을 부르기 전에 기본 기억 검색이 돌았다(#662).

    선조회를 했는지는 코드의 일이라 어기면 실패다. 선조회 근거를 하나도 인용하지 않은 것은 모델의
    판단이라 실패로 끊지 않고 참고로만 남긴다 — 후보와 무관한 기록이면 일반 추천이 맞다.
    """
    verdict = merged(exact3(ran), common(ran))
    state, counts = ran.world.context.state, evidence_counts(ran)
    if not state.memory_prefetched:
        verdict.failures.append("모델을 부르기 전에 기본 기억 검색을 하지 않았다")
    if counts["prefetched"] == 0:
        verdict.failures.append("최근 기록이 있는 아이인데 선조회 결과가 0건이다")
    if counts["prefetched"] and counts["cited"] == 0:
        verdict.notes.append("선조회 근거를 하나도 인용하지 않았다 — 반복되면 재요청을 검토한다")
    return verdict


def j_empty(ran: Ran) -> Verdict:
    verdict = merged(exact3(ran), common(ran))
    for draft in ran.result.suggestions:
        if draft.kind != "general":
            verdict.failures.append(f"기록 0건인데 개인화 추천이다 ({draft.content})")
        if draft.reason != GENERAL_REASON:
            verdict.failures.append("일반 추천의 이유가 코드 문구와 글자 그대로 같지 않다")
        if draft.citations:
            verdict.failures.append("일반 추천에 근거가 있다")
    if ran.result.needs_observation:
        verdict.failures.append("기록 0건인데 되묻기가 있다")
    if not ran.world.context.state.memory_prefetched:
        verdict.failures.append("기록이 0건이어도 기본 기억 검색은 해야 한다")
    if evidence_counts(ran)["prefetched"]:
        verdict.failures.append("기록 0건인데 선조회 결과가 있다")
    return verdict


def j_noconsent(ran: Ran) -> Verdict:
    verdict = common(ran)
    if ran.result.status != "completed" or not ran.result.suggestions:
        verdict.failures.append("동의가 없다고 추천을 막았다")
    return verdict


def j_pool2(ran: Ran) -> Verdict:
    verdict = common(ran)
    if (ran.result.status, len(ran.result.suggestions), ran.result.model_calls) != (
        "completed",
        2,
        1,
    ):
        verdict.failures.append(
            f"status={ran.result.status} 추천 {len(ran.result.suggestions)}개 모델 "
            f"{ran.result.model_calls}회 (completed · 2개 · 1회여야 한다)"
        )
    codes = [readout.code for readout in ran.result.readouts]
    if codes != ["fewer"]:
        verdict.failures.append(f"개수 안내(suggestion.partial)가 아니다 {codes}")
    return verdict


def j_pool0(ran: Ran) -> Verdict:
    verdict = Verdict()
    if (ran.result.status, len(ran.result.suggestions), ran.result.model_calls) != (
        "completed",
        0,
        0,
    ):
        verdict.failures.append(
            f"status={ran.result.status} 추천 {len(ran.result.suggestions)}개 모델 "
            f"{ran.result.model_calls}회 (completed · 0개 · 0회여야 한다)"
        )
    if [r.body for r in ran.result.readouts] != [READOUTS.get("pool.empty").template]:
        verdict.failures.append("pool.empty 문구가 글자 그대로 나가지 않았다")
    return verdict


def j_narrow(ran: Ran) -> Verdict:
    verdict = common(ran)
    calls = ran.result.model_calls
    if calls > 2:
        verdict.failures.append(f"모델 호출 {calls}회 (재호출은 1회까지)")
    if calls == 1 and not ran.narrowed:
        verdict.notes.append(
            "재호출이 일어나지 않았다 — 모델이 끼워 넣은 계란 후보를 고르지 않았다"
        )
    if calls == 2:
        recall = str(ran.llm.snapshots[1][-1].get("content") or "")
        for leaked in ("알레르기", "달걀", "계란 알레르기", "안전"):
            if leaked in recall:
                verdict.failures.append(f"재호출 프롬프트에 필터 사유가 있다 ({leaked!r})")
        verdict.notes.append("재호출 1회 — 최종 " + str(len(ran.result.suggestions)) + "개")
    return verdict


# ── 급식 갱신 판정 (§12) ──────────────────────────────────────────────────
_QUESTIONS = (
    "daycare.replaced_unknown",
    "daycare.ask_substitute",
    "daycare.ambiguous",
    "daycare.slot_ambiguous",
    "daycare.unchanged",
)


def _rows(ran: Ran) -> list[DaycareMealRow]:
    async def read() -> list[DaycareMealRow]:
        return await ran.world.context.ports.daycare.rows(
            child_id=ran.world.context.child_id,
            date_from=ran.world.today - timedelta(days=45),
            date_to=ran.world.today + timedelta(days=21),
        )

    return asyncio.run(read())


def _snapshot(world: World) -> list[tuple[Any, ...]]:
    async def read() -> list[DaycareMealRow]:
        return await world.context.ports.daycare.rows(
            child_id=world.context.child_id,
            date_from=world.today - timedelta(days=45),
            date_to=world.today + timedelta(days=21),
        )

    return [
        (
            row.id,
            row.serve_date,
            row.meal_slot,
            row.menu_keys,
            row.amount_factor,
            row.amount_known,
            row.caregiver_checked,
        )
        for row in asyncio.run(read())
    ]


def _bodies(ran: Ran) -> list[str]:
    return [readout.body for readout in ran.result.readouts]


def _keys(ran: Ran) -> list[str]:
    """readouts 의 문구를 키로 되돌린다(값이 없는 문구만). 값이 있는 문구는 접두로 가른다."""
    found: list[str] = []
    for body in _bodies(ran):
        for key in _QUESTIONS:
            if body == READOUTS.get(key).template:
                found.append(key)
    return found


def _unchanged(ran: Ran, before: list[tuple[Any, ...]]) -> bool:
    return _snapshot(ran.world) == before


def _touched(ran: Ran) -> set[Any]:
    """바뀌거나 지워지거나 새로 생긴 급식 행 id. 판정마다 "이 행만 바뀌어도 된다" 를 건다."""
    before = {row[0]: row for row in ran.before}
    after = {row[0]: row for row in _snapshot(ran.world)}
    return {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}


def _ids_on(ran: Ran, serve_date: Any, slot: str | None = None) -> set[Any]:
    return {
        row[0] for row in ran.before if row[1] == serve_date and (slot is None or row[2] == slot)
    }


def _only(verdict: Verdict, ran: Ran, allowed: set[Any], what: str) -> None:
    stray = _touched(ran) - allowed
    if stray:
        verdict.failures.append(f"{what} 말고 다른 급식 행 {len(stray)}개가 바뀌거나 지워졌다")


def j_ask(*accepted: str) -> Callable[[Ran], Verdict]:
    """되묻기 1개 · 변경 0건 (T41 · T44 …). `accepted` 안의 질문이면 통과다."""

    def judge(ran: Ran) -> Verdict:
        verdict = common(ran)
        keys = _keys(ran)
        if len(keys) != 1 or keys[0] not in accepted:
            verdict.failures.append(f"되묻기가 {accepted} 중 하나가 아니다 {keys or _bodies(ran)}")
        if not _unchanged(ran, ran.before):
            verdict.failures.append("되물어야 하는데 급식 행이 바뀌었다")
        if keys and keys[0] != accepted[0]:
            verdict.notes.append(f"스펙의 {accepted[0]} 대신 {keys[0]} 로 되물었다")
        return verdict

    return judge


def _find(ran: Ran, serve_date: Any, slot: str) -> DaycareMealRow | None:
    return next((r for r in _rows(ran) if r.serve_date == serve_date and r.meal_slot == slot), None)


def j_t42(ran: Ran) -> Verdict:
    verdict = common(ran)
    lunch = _find(ran, ran.world.today, "lunch")
    before = {row[0]: row for row in ran.before}
    keys = _keys(ran)
    if lunch is not None and lunch.amount_factor == 1.5 and lunch.amount_known:
        if lunch.menu_keys != before[lunch.id][3]:
            verdict.failures.append("메뉴를 계란말이로 덮어썼다")
        _only(verdict, ran, {lunch.id}, "오늘 점심")
    elif keys in (["daycare.slot_ambiguous"], ["daycare.ambiguous"]):
        if not _unchanged(ran, ran.before):
            verdict.failures.append("되물었는데 급식 행이 바뀌었다")
        # 스펙이 기대한 분기에 닿을 수 없다 — 날짜가 없는 말이라 T44 처럼 되묻는다
        verdict.gaps.append(
            f"{keys[0]} 로 되물었다 — 발화에 날짜가 없다. 스펙의 T42(바로 갱신)와 "
            "T44(날짜 없으면 되묻기)가 서로 어긋나 PM 결정을 기다린다"
        )
    else:
        verdict.failures.append("오늘 행의 양(1.5 · 알고 있음)이 반영되지 않았다")
    return verdict


def j_t43(ran: Ran) -> Verdict:
    verdict = common(ran)
    target = ran.world.today + timedelta(days=2)
    _only(verdict, ran, _ids_on(ran, target), "모레 급식")
    left = [r for r in _rows(ran) if r.serve_date == target]
    if left:
        verdict.failures.append(f"모레 급식이 {len(left)}행 남았다 (모든 끼니가 지워져야 한다)")
    if "daycare.slot_ambiguous" in _keys(ran):
        verdict.failures.append("끼니를 되물었다 — 하루를 통째로 지운다")
    if not any("기관 식사 없는 걸로" in body for body in _bodies(ran)):
        verdict.failures.append("daycare.absent 문구가 없다")
    return verdict


def j_t45(ran: Ran) -> Verdict:
    verdict = common(ran)
    tuesday = resolve_meal_date("다음 주 화요일", today=ran.world.today)
    row = _find(ran, tuesday, "lunch")
    if row is None:
        verdict.failures.append("다음 주 화요일 점심 행이 없다")
        return verdict
    if "두유" not in row.menu_keys or "우유" in row.menu_keys:
        verdict.failures.append(f"우유가 두유로 바뀌지 않았다 {row.menu_keys}")
    _only(verdict, ran, {row.id}, "다음 주 화요일 점심")
    return verdict


def j_amount(days_ago: int, factor: float, known: bool) -> Callable[[Ran], Verdict]:
    """그날 점심의 양만 바뀐다(T46 · H1). 메뉴는 그대로다."""

    def judge(ran: Ran) -> Verdict:
        verdict = common(ran)
        lunch = _find(ran, ran.world.today - timedelta(days=days_ago), "lunch")
        if lunch is None:
            verdict.failures.append("그날 급식 행이 없어졌다")
            return verdict
        if (lunch.amount_factor, lunch.amount_known) != (factor, known):
            got = (lunch.amount_factor, lunch.amount_known)
            if _keys(ran) == ["daycare.ask_substitute"] and _unchanged(ran, ran.before):
                # "반만 먹었대" 처럼 양 말 뒤까지 넘기면 양 사전(완전 일치)이 못 읽고 되묻는다
                verdict.gaps.append("양 구간을 양 사전이 못 읽어 되물었다 — 사전 완전 일치(후속)")
            else:
                verdict.failures.append(f"양이 반영되지 않았다 {got}")
        if lunch.menu_keys != {row[0]: row for row in ran.before}[lunch.id][3]:
            verdict.failures.append("양만 말했는데 메뉴가 바뀌었다")
        _only(verdict, ran, {lunch.id}, "그날 점심")
        return verdict

    return judge


def j_one(key: str, *, gap: tuple[str, str] | None = None) -> Callable[[Ran], Verdict]:
    """문구 하나가 글자 그대로 나가고 변경이 없다(T47 · not_found).

    `gap` = (대신 나온 문구 키, 사유) — 스펙 대신 그 문구로 안전하게 되물은 것은 미구현으로 센다.
    """

    def judge(ran: Ran) -> Verdict:
        verdict = common(ran)
        if gap and _bodies(ran) == [READOUTS.get(gap[0]).template]:
            verdict.gaps.append(gap[1])
        elif _bodies(ran) != [READOUTS.get(key).template]:
            verdict.failures.append(f"{key} 문구가 아니다 {_bodies(ran)}")
        if not _unchanged(ran, ran.before):
            verdict.failures.append("급식 행이 바뀌었다(INSERT 0건이어야 한다)")
        return verdict

    return judge


def j_t48(*, allergic: bool) -> Callable[[Ran], Verdict]:
    def judge(ran: Ran) -> Verdict:
        verdict = common(ran)
        lunch = _find(ran, ran.world.today, "lunch")
        reflected = lunch is not None and "새우볶음밥" in lunch.menu_keys
        conflict = any("알레르기와 겹쳐요" in body for body in _bodies(ran))
        if reflected and allergic and not conflict:
            verdict.failures.append("알레르기와 겹치는데 경고가 없다")
        if reflected and not allergic and conflict:
            verdict.failures.append("알레르기가 없는데 경고가 붙었다")
        if reflected:
            _only(verdict, ran, {lunch.id}, "오늘 점심")  # type: ignore[union-attr]
            return verdict
        # 반영하지 않았다면 되묻고 아무것도 안 바꾼 것만 미구현으로 센다 — 행을 지우거나 엉뚱한
        # 끼니에 쓴 것을 GAP 으로 덮지 않는다
        keys = _keys(ran)
        if keys == ["daycare.replaced_unknown"] and _unchanged(ran, ran.before):
            verdict.gaps.append(
                "스펙은 새우볶음밥을 바로 반영하길 기대하지만 tool 은 빼는 메뉴 없이 넣기만 하면 "
                "되묻는다(replaced_unknown)"
            )
        else:
            verdict.failures.append(f"반영도 되묻기도 아니다 ({keys or _bodies(ran)})")
        return verdict

    return judge


def j_inquiry(ran: Ran) -> Verdict:
    """급식이 바뀌었다는 말이 아닌 발화(R1~R6 — 조회 · 묻는 말 · 가정 · 양 질문).

    급식 갱신은 tool 을 꼭 부르게 해서(required) 갱신 · 삭제로 갈 수 있다. 무엇으로 끝나든 급식
    행이 바뀌면 안 된다 — 삭제는 OCR 재적재 없이는 되돌릴 수 없다.
    """
    verdict = common(ran)
    if not _unchanged(ran, ran.before):
        verdict.failures.append("급식이 바뀌었다는 말이 아닌데 급식 행이 바뀌었다")
    if "daycare.unchanged" not in _keys(ran):
        verdict.notes.append(f"그대로 두기 대신 {_keys(ran) or _bodies(ran)} 로 끝났다")
    return verdict


def j_opened(ran: Ran) -> Verdict:
    """단계 · 동의가 아니라 급식 행으로 여닫는다(이유기 · 동의 철회)."""
    verdict = common(ran)
    if ran.result.status == "blocked" or ran.result.model_calls == 0:
        verdict.failures.append(f"급식 갱신이 닫혔다 (status={ran.result.status})")
    _only(verdict, ran, _ids_on(ran, ran.world.today), "오늘 급식")
    if any(row[1] == ran.world.today for row in ran.before) and not _rows_on(ran, ran.world.today):
        verdict.failures.append("대체식 말인데 오늘 급식 행이 지워졌다")
    return verdict


def _rows_on(ran: Ran, serve_date: Any) -> list[DaycareMealRow]:
    return [row for row in _rows(ran) if row.serve_date == serve_date]


# ── 케이스 ───────────────────────────────────────────────────────────────
M, D = "meal_recommendation", "daycare_meal"
CASES: tuple[Case, ...] = (
    Case(
        "T01/C48",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C48",
        "후보 3개 · 재호출 0회 · 근거 없으면 general",
        j_t01,
    ),
    Case("T02/C48", "오후 간식으로 뭐 주면 좋을까?", M, "C48", "간식 후보 3개", j_t01),
    Case("T03/C48", "내일 아침 메뉴 추천해줘.", M, "C48", "아침 후보 3개", j_t01),
    Case(
        "T04/C48",
        "요즘 편식이 심한데 뭘 주면 잘 먹을까?",
        M,
        "C48",
        "후보 3개 + 역질의 1개 · 편식을 해석하지 않는다",
        j_t04,
    ),
    Case(
        "T06/C48",
        "오늘 아침이랑 점심은 기록 못 했는데 저녁은 뭐 먹일까?",
        M,
        "C48",
        "nutrient.today_empty + 후보 · 결측을 0으로 단정하지 않는다",
        j_t06,
    ),
    Case(
        "T07/C48",
        "오늘 점심에 불고기 먹였어. 저녁은 뭐가 좋을까?",
        M,
        "C48",
        "소고기 주재료 중복 회피 (#398)",
        j_t07,
    ),
    Case(
        "T08/C48",
        "오늘 유치원 점심 메뉴 보고 저녁 겹치지 않게 추천해줘.",
        M,
        "C48",
        "daycare_amount_unknown 안내 + 후보",
        j_t08,
    ),
    Case(
        "T22/C48",
        "땅콩 들어간 건 빼고 간식 추천해줘.",
        M,
        "C48",
        "땅콩 포함 후보 0",
        j_avoid("땅콩", "땅콩버터"),
    ),
    Case(
        "T23/C48",
        "우유 알레르기 있는데 간식 뭐 줄까?",
        M,
        "C48",
        "우유 포함 후보 0 + 등록 안내",
        j_avoid("우유", "치즈", "요거트", readout="알레르기로 등록하면"),
    ),
    Case(
        "T23/C48-milk",
        "우유 알레르기 있는데 간식 뭐 줄까?",
        M,
        "C48-milk",
        "코드 필터로 우유 0 (발화와 무관)",
        j_avoid("우유", "치즈", "요거트"),
    ),
    Case(
        "T24/C48",
        "브로콜리는 절대 안 먹어. 채소 반찬 추천해줘.",
        M,
        "C48",
        "기피를 근거로 · 다른 채소 후보 · 재호출 0회",
        j_t24,
    ),
    Case(
        "T25/C48",
        "딸기 좋아하는 편이야. 딸기 들어간 간식 추천해줘.",
        M,
        "C48",
        "딸기 후보 · 확정 표현 없음",
        j_t25,
    ),
    Case(
        "T49/C48-nut",
        "견과류 들어간 메뉴 추천해줘.",
        M,
        "C48-nut",
        "견과류 후보 0 · 요청 안내 한 줄 · reason 에 막힌 음식 없음",
        j_t49,
    ),
    Case(
        "T50/C48-nut",
        "땅콩소스 먹여도 될까?",
        "food_check",
        "C48-nut",
        "모델 0회 · 견과류 알레르기와 겹침 + 땅콩 묶음 설명",
        j_t50,
    ),
    Case("T27/C48-prev", "다른 메뉴로 추천해줘.", M, "C48-prev", "직전 3개와 겹침 0", j_prev),
    Case(
        "T28/C48-prev",
        "어제 추천해준 거 말고 다른 거 없어?",
        M,
        "C48-prev",
        "어제 메뉴 재등장 0",
        j_prev,
    ),
    Case(
        "T29/C8",
        "꿀 넣은 이유식 먹여도 돼?",
        M,
        "C8",
        "꿀 후보 0 + 12개월 전 꿀 금지 안내",
        j_t29_c8,
    ),
    Case(
        "T01/C2",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C2",
        "모델 0회 · unsupported.milk_meal",
        j_closed("unsupported.milk_meal", "unsupported"),
    ),
    Case("T01/C8", "오늘 저녁 뭐 먹이면 좋을까?", M, "C8", "이유식 후보 · 꿀 메뉴 없음", j_c8),
    Case(
        "T01/C30",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C30",
        "후보 3개 · 질식 주의 문구 없음 · 팝콘 막힘",
        j_c30,
    ),
    Case(
        "P662/C48",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C48",
        "모델 호출 전 선조회 · 근거 건수 기록 (#662)",
        j_prefetch,
    ),
    Case(
        "T01/C48-empty",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C48-empty",
        "general · 이유가 코드 문구 · 근거 0행",
        j_empty,
    ),
    Case(
        "T01/C48-safetyfail",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C48-safetyfail",
        "모델 0회 · blocked.safety",
        j_closed("blocked.safety", "blocked"),
    ),
    Case(
        "T01/C48-noconsent",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C48-noconsent",
        "동의 없어도 추천이 나간다",
        j_noconsent,
    ),
    Case(
        "T01/C48-pool2",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C48-pool2",
        "2개 · 재호출 0회 · suggestion.partial",
        j_pool2,
    ),
    Case(
        "T01/C48-pool0",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C48-pool0",
        "0개 · 모델 0회 · pool.empty",
        j_pool0,
    ),
    Case(
        "T01/C48-narrow",
        "오늘 저녁 뭐 먹이면 좋을까?",
        M,
        "C48-egg",
        "풀 필터 뒤에 걸리면 재호출 정확히 1회 · 사유 없는 제외 목록",
        j_narrow,
        narrow=True,
    ),
    Case(
        "T41/C48",
        "오늘 급식 대신 두유 받았대.",
        D,
        "C48",
        "되묻기 1개 · 변경 0건",
        j_ask("daycare.replaced_unknown", "daycare.slot_ambiguous"),
    ),
    Case(
        "T42/C48",
        "어린이집에서 계란말이 엄청 많이 먹었대.",
        D,
        "C48",
        "오늘 행 양 1.5 · 메뉴 그대로",
        j_t42,
    ),
    Case(
        "T43/C48",
        "내일모레는 어린이집 빠져.",
        D,
        "C48",
        "모레의 모든 끼니 삭제 · daycare.absent",
        j_t43,
    ),
    Case(
        "T44/C48",
        "급식 좀 바꿔줘.",
        D,
        "C48",
        "날짜를 되물음 · 변경 0건",
        j_ask("daycare.ambiguous"),
    ),
    Case(
        "T45/C48",
        "다음 주 화요일 급식에 우유 빼고 두유로 해줘.",
        D,
        "C48",
        "다음 주 화요일 · 우유 → 두유",
        j_t45,
    ),
    Case(
        "T46/C48",
        "어제 어린이집에서 밥 남겼대.",
        D,
        "C48",
        "어제 행 양 0.5 · 모름",
        j_amount(1, 0.5, False),
    ),
    Case(
        "T47/C48",
        "지난달 15일 급식 고쳐줘.",
        D,
        "C48",
        "daycare.out_of_range · 변경 0건",
        j_one(
            "daycare.out_of_range",
            gap=(
                "daycare.ambiguous",
                "'지난달 15일' 을 날짜 해석기가 못 읽어 날짜를 되물었다(명세의 표현 표 밖) — "
                "스펙은 out_of_range 를 기대한다",
            ),
        ),
    ),
    Case(
        "T48/C48-shrimp",
        "오늘 어린이집에서 새우볶음밥 먹었대.",
        D,
        "C48-shrimp",
        "반영 + allergy_conflict · 저장은 됨",
        j_t48(allergic=True),
    ),
    Case(
        "T48/C48",
        "오늘 어린이집에서 새우볶음밥 먹었대.",
        D,
        "C48",
        "반영만 · 경고 없음",
        j_t48(allergic=False),
    ),
    Case(
        "B1/C48-nodc",
        "오늘 급식 대신 두유 받았대.",
        D,
        "C48-nodc",
        "급식 행이 없으면 모델 0회 · closed.no_daycare",
        j_closed("closed.no_daycare", "blocked"),
    ),
    Case(
        "B2/C48",
        "내일 급식은 카레래.",
        D,
        "C48",
        "없는 날은 daycare.not_found · INSERT 0건",
        j_one(
            "daycare.not_found",
            gap=(
                "daycare.unchanged",
                "그날 급식을 알려 주는 말로 보고 그대로 두기로 끝났다 — INSERT 0건은 지켰고 "
                "스펙은 not_found 문구를 기대한다",
            ),
        ),
    ),
    Case(
        "B3/C48",
        "오늘 급식 바꿔줘.",
        D,
        "C48",
        "그날 행이 둘이라 slot_ambiguous",
        j_ask("daycare.slot_ambiguous"),
    ),
    Case(
        "B4/C8",
        "오늘 급식 대신 두유 받았대.",
        D,
        "C8",
        "이유기도 급식 행이 있으면 열린다",
        j_opened,
    ),
    Case(
        "B5/C48-noconsent",
        "오늘 급식 대신 두유 받았대.",
        D,
        "C48-noconsent",
        "동의 철회여도 급식 갱신은 진행",
        j_opened,
    ),
    Case(
        "R1/C48",
        "오늘 어린이집 급식 뭐 나왔어?",
        D,
        "C48",
        "조회 발화 · 급식 행 변경 0건",
        j_inquiry,
    ),
    Case(
        "R2/C48",
        "어제 점심 급식 뭐였는지 알려줘.",
        D,
        "C48",
        "조회 발화 · 급식 행 변경 0건",
        j_inquiry,
    ),
    # 결석 · 양 낱말이 든 묻는 말 · 가정 — required 라 삭제 · 양 쓰기로 갈 수 있는 말 (최종 리뷰 I1)
    Case(
        "R3/C48",
        "모레 어린이집 쉬는 날이야?",
        D,
        "C48",
        "묻는 말 · 모레 행 삭제 0건",
        j_inquiry,
    ),
    Case(
        "R4/C48",
        "모레 어린이집 빠지면",
        D,
        "C48",
        "가정(Supervisor 가 자른 조각) · 삭제 0건",
        j_inquiry,
    ),
    Case(
        "R5/C48",
        "어제 급식 남겼어?",
        D,
        "C48",
        "양 질문 · 양 쓰기 0건",
        j_inquiry,
    ),
    Case(
        "R6/C48",
        "오늘 어린이집에서 많이 먹었대?",
        D,
        "C48",
        "양 질문 · 양 쓰기 0건",
        j_inquiry,
    ),
    # 스키마 설명에 없는 말로 바꿔 쓴 것 — 설명을 고친 효과가 그 문장에만 맞춘 것이 아닌지 본다
    Case(
        "H1/C48",
        "어제 점심 급식은 반만 먹었대.",
        D,
        "C48",
        "어제 행 양 0.5 · 알고 있음 · 메뉴 그대로",
        j_amount(1, 0.5, True),
    ),
    Case(
        "H2/C48",
        "다음 주 화요일 점심은 우유 말고 두유로 바꿔줘.",
        D,
        "C48",
        "다음 주 화요일 · 우유 → 두유",
        j_t45,
    ),
    Case(
        "H3/C48",
        "내일모레는 어린이집 안 가요.",
        D,
        "C48",
        "모레의 모든 끼니 삭제 · 내일 행은 그대로",
        j_t43,
    ),
)


# ── 실행 ─────────────────────────────────────────────────────────────────
def _inject_egg_recipes(monkeypatch: pytest.MonkeyPatch) -> None:
    """풀 필터 뒤 사후 필터에 걸리는 상황(C48-narrow)을 흉내 낸다. 계란 레시피를 풀에 끼운다."""
    real = food_agent.build_pool
    egg_rows = [_row(spec, NOW) for spec in _EGG_ONLY]

    async def build(context: Any) -> tuple[PoolItem, ...]:
        pool = await real(context)
        extra = tuple(PoolItem(row=row, weight=50.0) for row in egg_rows)
        return (*pool, *extra)

    monkeypatch.setattr(food_agent, "build_pool", build)


def _client() -> LLMClient:
    require_food_model()
    try:
        return LLMClient(role="food")
    except LLMConfigError as exc:
        pytest.skip(f"Food 설정이 필요합니다: {exc}")


@pytest.fixture(scope="module", autouse=True)
def _fresh_results() -> None:
    """결과 파일은 이 모듈을 돌릴 때마다 새로 쓴다 — 지난 실행과 섞이면 요약이 틀린다."""
    RESULT_PATH.unlink(missing_ok=True)


def _write(record: dict[str, Any]) -> None:
    with RESULT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _say(text: str) -> None:
    """콘솔 인코딩(Windows cp949)이 못 쓰는 글자 때문에 케이스가 죽지 않게 한다."""
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, "replace").decode(encoding))


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_food_run(case: Case, expect: Callable[..., None], monkeypatch: pytest.MonkeyPatch) -> None:
    expect(case.expect)
    client = _client()
    broken: list[str] = []
    if case.narrow:
        _inject_egg_recipes(monkeypatch)  # 반복마다 감싸면 계란 행이 쌓인다 — 한 번만
    for attempt in range(1, TEST_REPEAT + 1):
        world = build_world(case.world, now=NOW, run_id=f"live-{case.case_id}-{attempt}")
        recorder = RecordingLLM(client)
        task = DomainTask(
            run_id=world.context.run_id,
            agent="food",
            task_type=case.task,
            request_texts=(case.text,),
        )
        before = _snapshot(world)
        started = time.perf_counter()
        result = asyncio.run(run(task, world.context, client=recorder))
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        ran = Ran(
            case,
            world,
            result,
            recorder,
            elapsed_ms,
            narrowed=result.model_calls == 2,
            before=before,
        )
        verdict = case.judge(ran)

        outcome = "FAIL" if verdict.failures else ("GAP" if verdict.gaps else "PASS")
        suffix = f" #{attempt}" if TEST_REPEAT > 1 else ""
        _say(f"\n[{case.case_id}{suffix}] {outcome}  {case.text!r}")
        _say(
            f"    status={result.status} 모델 {result.model_calls}회 {elapsed_ms}ms "
            f"추천 {len(result.suggestions)}개 {_names(ran)}"
        )
        counts = evidence_counts(ran) if case.task == M else None
        if counts is not None:
            _say("    근거 " + " ".join(f"{key}={value}" for key, value in counts.items()))
        for readout in result.readouts:
            _say(f"    · {readout.body}")
        if result.needs_observation:
            _say(f"    ? {result.needs_observation[0]}")
        for label, items in (
            ("실패", verdict.failures),
            ("미구현", verdict.gaps),
            ("참고", verdict.notes),
        ):
            for item in items:
                _say(f"    {label}: {item}")
        _write(
            {
                "case": case.case_id,
                "attempt": attempt,
                "outcome": outcome,
                "status": result.status,
                "model_calls": result.model_calls,
                "suggestions": _names(ran),
                "readouts": _bodies(ran),
                "tools": [name for name, _ in recorder.tool_calls],
                "tool_args": [raw for _, raw in recorder.tool_calls],
                "question": list(result.needs_observation),
                "evidence": counts,
                "failures": verdict.failures,
                "gaps": verdict.gaps,
                "notes": verdict.notes,
                "ms": elapsed_ms,
            }
        )
        broken.extend(f"#{attempt} {item}" for item in verdict.failures)
    assert not broken, f"{case.case_id}: " + " / ".join(broken)
