"""W · 일주일 시나리오 러너 (docs/eval/eval-v1.md §3-4 · §5).

앱을 프로세스 안에서(ASGI) 띄워 운영 창구로 돈다 — `POST /inputs` → `GET /runs/{rid}/events` ·
`POST /events`(승인) · `POST /corrections`. 저장소는 통합 테스트 DB(`icatch_it`)이고 시계는
`time-machine` 이 단계 시각으로 옮긴다. 시드 행은 commit 한다(러너가 자기 세션을 연다).

🚨 W 의 Curator 는 가짜 임베딩 · 가짜 판정기다 — 실제 묶음 품질은 보지 않는다 (README).
   임베더는 글자마다 직교하는 벡터를 주고, 판정기는 "한쪽 이름이 다른 쪽에 들어 있으면 같은 대상"
   이라는 글자 포함만 본다. 그래야 "블록놀이" 와 "블록 쌓기" 가 한 프로필로 묶여 승격을 볼 수 있다.

순수한 조각(주의 월요일 · 단계 시각 · 문장 틀)은 단위 테스트가 고정한다.
DB 를 읽고 쓰는 조각은 라이브 실행에서만 돈다.
"""

import asyncio
import hashlib
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import time_machine
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import Range

from app.agents.common.refs import Ref
from app.api import idempotency, quota
from app.core import llm_usage
from app.domains.child.models import Child, ParentChild, ParentChildRelation
from app.domains.consent.models import ConsentAction, ConsentScope
from app.domains.consent.repository import record_consent
from app.domains.consent.retention import purge_child, purge_parent
from app.domains.identity.models import Parent
from app.domains.memory.curator import trigger
from app.domains.memory.observation import models as obs
from app.domains.policy.repository import find_version
from app.domains.safety.models import HealthSafety, SafetyKind, SafetySeverity, SafetyStatus
from app.infra.db.session import async_session_factory
from tests.eval.suite.schema import WEEKDAYS, Step, WeekCase
from tests.eval.suite.trace import (
    CitationView,
    SavedRow,
    SuggestionView,
    Trace,
    error_text,
    error_trace,
    removed_rows,
)
from tests.eval.suite.week_frames import apply_frames, parse_sse
from tests.integration.api.conftest import seed_login

KST = ZoneInfo("Asia/Seoul")
STALE_DAYS = 183  # world.py 와 같다 — 루트 §2 "6개월 이상"
POLICY_VERSION = "2026-11-03"  # 지금 유효한 약관 판 (child_health · location)
BUSAN = {"lat": 35.18, "lon": 129.08}
SSE_TIMEOUT = 150.0
OBS_TABLES = ("food", "health", "education", "activity", "routine")
PROMOTABLE_TABLES = ("food", "education", "activity")
# W 가 만든 보호자의 별명 머리글 — 강제로 죽은 실행이 남긴 행을 다음 실행이 찾아 지운다.
# 보호자 별명은 Agent 입력에 쓰이지 않는다(아이 별명만 카나리아)
W_MARK = "eval-w:"
# 실행 전체를 감싸는 세션 advisory lock 키 — 두 int 꼴이라 Curator 의 bigint 잠금(아이 단위)과
# 공간이 겹치지 않는다. 동시에 돌리면 나중 쪽이 기다린다(앞 실행이 레시피를 지우는 사이에 빈
# 카탈로그로 도는 것을 막는다, 리뷰 11-08)
W_LOCK = (777, 1)
OBS_MODEL: dict[str, Any] = {
    "food": obs.ObservationFood,
    "education": obs.ObservationEducation,
    "activity": obs.ObservationActivity,
    "routine": obs.ObservationRoutine,
}


# ── 순수 조각 ─────────────────────────────────────────────────────────────────


def simulation_monday(today: date) -> date:
    """주(D) = 실행일 뒤 7일 이상 지난 첫 월요일 (추천 만료가 DB 시계라 미래로 잡는다 — §3-4)."""
    day = today + timedelta(days=7)
    return day + timedelta(days=(7 - day.weekday()) % 7)


def step_day(monday: date, at: str) -> date:
    return monday + timedelta(days=WEEKDAYS.index(at[:3]))


def step_moment(monday: date, at: str) -> datetime:
    """ "tue 18:40" → 그 주 화요일 18:40 KST."""
    day = step_day(monday, at)
    return datetime(day.year, day.month, day.day, int(at[4:6]), int(at[7:9]), tzinfo=KST)


def parent_nickname(name: str) -> str:
    return f"{W_MARK}{name}"


def interest_misses(
    states: Mapping[str, str], *, confirmed: Sequence[str], not_confirmed: Sequence[str]
) -> list[str]:
    """관심 확정 점검. 글자를 품은 merge_key 를 본다. not_confirmed 는 그 관심이 있어야 한다 —
    행이 아예 없으면 "확정되지 않았다" 를 본 게 아니다 (리뷰 11-08)."""
    misses: list[str] = []
    for word in confirmed:
        hit = {k: s for k, s in states.items() if word in k}
        if not any(s == "confirmed" for s in hit.values()):
            misses.append(f"'{word}' 관심이 confirmed 가 아니다 ({hit or '없음'})")
    for word in not_confirmed:
        hit = {k: s for k, s in states.items() if word in k}
        if not hit:
            misses.append(f"'{word}' 관심이 없다 — confirmed 가 아닌지 볼 수 없다")
        elif any(s == "confirmed" for s in hit.values()):
            misses.append(f"'{word}' 관심이 confirmed 다 ({hit})")
    return misses


def affinity_cite_misses(
    words: Sequence[str],
    *,
    confirmed_at: Mapping[str, tuple[str, int]],
    cites: Sequence[tuple[int, str]],
) -> list[str]:
    """ "관심 확정 → 그 근거로 추천" 점검. confirmed_at 은 관심 id → (merge_key, 처음 confirmed 로
    본 단계 번호), cites 는 (단계 번호, 인용한 profile_affinity id). 확정을 본 단계의 인용은 세지
    않는다 — 그 run 의 추천은 Curator 보다 먼저 나간다."""
    misses: list[str] = []
    for word in words:
        ids = {aid: n for aid, (key, n) in confirmed_at.items() if word in key}
        if not ids:
            misses.append(f"'{word}' 관심이 확정된 적이 없어 점검할 수 없다")
        elif not any(aid in ids and n > ids[aid] for n, aid in cites):
            misses.append(f"'{word}' 관심이 확정 뒤 어떤 추천의 근거로도 쓰이지 않았다")
    return misses


def corrected_misses(
    verdicts: Sequence[str],
    *,
    corrections: Sequence[tuple[str, str, int]],
    cited: Sequence[tuple[int, str]],
) -> list[str]:
    """정정 점검 — (판정, 기록 id, 단계 번호) 로 고친 기록이 그 뒤 추천 근거에 없다."""
    misses: list[str] = []
    for verdict in verdicts:
        ids = {oid: n for v, oid, n in corrections if v == verdict}
        if not ids:
            misses.append(f"{verdict} 로 고친 기록이 없어 점검할 수 없다")
            continue
        leaked = sorted({oid for n, oid in cited if oid in ids and n > ids[oid]})
        if leaked:
            misses.append(f"{verdict} 로 고친 기록이 뒤 추천의 근거에 있다 ({len(leaked)}건)")
    return misses


def response_code(response: Any) -> str:
    """실패 응답을 결과에 남길 한 줄 — 상태 코드와 제품 오류 코드(`{"error": {"code"}}`)만.
    본문 · message 에는 입력 일부 · DB 값이 섞일 수 있다 (공개 저장소, 리뷰 11-08)."""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return f"{response.status_code} {code}" if isinstance(code, str) else str(response.status_code)


def render_say(sentence: str, monday: date) -> str:
    """`{d+N}` → 월요일 + N 일의 "M월 D일". 대본에 날짜를 박지 않으려고 쓴다."""
    out = sentence
    for n in range(15):
        day = monday + timedelta(days=n)
        out = out.replace("{d+%d}" % n, f"{day.month}월 {day.day}일")
    return out


# ── 가짜 Curator ──────────────────────────────────────────────────────────────


class HashEmbedder:
    """글자마다 결정적인 벡터 — 정규화한 글자열 해시로 1536 칸 중 한 칸. 같은 글은 같은 벡터, 다른
    글은 직교한다 (다 같은 벡터면 블록 · 퍼즐이 한 프로필로 묶인다)."""

    DIM = 1536

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for raw in texts:
            key = "".join(raw.split()).lower()
            slot = int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big") % self.DIM
            vector = [0.0] * self.DIM
            vector[slot] = 1.0
            out.append(vector)
        return out


class ContainsJudge:
    """판정기 대역 — 공백을 뺀 이름이 서로 들어 있으면 그 후보, 아니면 none."""

    async def judge(self, *, subject: str, domain: Any, candidates: Sequence[str]) -> Any:
        from app.agents.curator.embedding.judge import JudgeAnswer

        key = "".join(subject.split())
        for candidate in candidates:
            name = "".join(candidate.split())
            if name and (name in key or key in name):
                return JudgeAnswer(choice=candidate, model="fake-contains")
        return JudgeAnswer(choice="none", model="fake-contains")


# ── DB 읽기 ───────────────────────────────────────────────────────────────────


async def _rows(sql: str, **params: Any) -> list[Any]:
    async with async_session_factory() as session:
        return list((await session.execute(text(sql), params)).all())


async def observation_snapshot(child_id: uuid.UUID) -> dict[str, SavedRow]:
    found: dict[str, SavedRow] = {}
    for domain in OBS_TABLES:
        for oid, day in await _rows(
            f"select id::text, lower(observed_range) from observation_{domain} where child_id=:c",
            c=child_id,
        ):
            found[oid] = SavedRow(domain=domain, id=oid, observed_on=day)
    return found


async def kept_observation_ids(child_id: uuid.UUID) -> frozenset[str]:
    """검색에 잡히는(active · stand_alone) 관찰 id — I15 의 전후 비교에 쓴다. 지움 · 잘못된 기록은
    행이 남고 status 만 바뀌어서 행 목록(observation_snapshot)으로는 못 본다."""
    kept: set[str] = set()
    for domain in OBS_TABLES:
        kept.update(
            oid
            for (oid,) in await _rows(
                f"select id::text from observation_{domain} "
                "where child_id=:c and status::text in ('active', 'stand_alone')",
                c=child_id,
            )
        )
    return frozenset(kept)


async def event_count(child_id: uuid.UUID) -> int:
    return int((await _rows("select count(*) from event where child_id=:c", c=child_id))[0][0])


async def safety_snapshot(child_id: uuid.UUID) -> tuple[str, ...]:
    rows = await _rows(
        "select kind::text, label, status::text from health_safety where child_id=:c", c=child_id
    )
    return tuple(sorted(f"{k}:{label}:{s}" for k, label, s in rows))


async def confirmed_days(child_id: uuid.UUID) -> dict[str, int]:
    union = " union all ".join(
        f"select affinity_id, observed_range, status from observation_{d}"
        for d in PROMOTABLE_TABLES
    )
    rows = await _rows(
        f"select p.merge_key, count(distinct lower(o.observed_range)) from profile_affinity p "
        f"left join ({union}) o on o.affinity_id = p.id and o.status = 'active' "
        "where p.child_id=:c and p.state='confirmed' group by p.id, p.merge_key",
        c=child_id,
    )
    return {key: int(days) for key, days in rows}


async def profile_rows(child_id: uuid.UUID) -> list[tuple[str, str, str]]:
    """(관심 id, merge_key, state) — 인용 id 와 같은 꼴(UUID 문자열)."""
    rows = await _rows(
        "select id::text, merge_key, state::text from profile_affinity where child_id=:c",
        c=child_id,
    )
    return [(aid, key, state) for aid, key, state in rows]


async def profile_states(child_id: uuid.UUID) -> dict[str, str]:
    rows = await _rows(
        "select merge_key, state::text from profile_affinity where child_id=:c", c=child_id
    )
    return {key: state for key, state in rows}


async def stale_profile_ids(child_id: uuid.UUID, stale_obs: frozenset[str]) -> frozenset[str]:
    """묶인 관찰이 전부 6개월 지난 심은 기록뿐인 관심 id — 인용 id 와 같은 꼴(UUID 문자열)."""
    linked: dict[str, list[str]] = {}
    for domain in PROMOTABLE_TABLES:
        for oid, aid in await _rows(
            f"select id::text, affinity_id::text from observation_{domain} "
            "where child_id=:c and affinity_id is not null",
            c=child_id,
        ):
            linked.setdefault(aid, []).append(oid)
    return frozenset(aid for aid, ids in linked.items() if all(i in stale_obs for i in ids))


async def observation_text(domain: str, oid: str) -> str:
    """고칠 기록을 좁힐 때 보는 글 — raw_text 는 한 줄 전체라 여러 관찰이 같으므로 뺀다."""
    rows = await _rows(
        f"select (to_jsonb(t) - 'raw_text' - 'embedding')::text from observation_{domain} t "
        "where id=:i",
        i=uuid.UUID(oid),
    )
    return str(rows[0][0]) if rows else ""


async def suggestion_views(child_id: uuid.UUID, run_id: str) -> tuple[SuggestionView, ...]:
    rows = await _rows(
        "select id, agent::text, kind::text, content, reason, items from suggestion "
        "where child_id=:c and run_id=:r order by created_at, id",
        c=child_id,
        r=run_id,
    )
    views: list[SuggestionView] = []
    for sid, agent, kind, content, reason, items in rows:
        evidence = await _rows(
            "select source_kind, source_id::text, note from suggestion_evidence "
            "where suggestion_id=:s order by source_kind, source_id",
            s=sid,
        )
        views.append(
            SuggestionView(
                agent=agent,
                kind=kind,
                content=content,
                reason=reason or "",
                items=tuple(items or ()),
                citations=tuple(
                    CitationView(
                        kind=k,
                        id=i,
                        note=n,
                        child_record=Ref(kind=k, id=uuid.UUID(i)).is_child_record,
                    )
                    for k, i, n in evidence
                ),
            )
        )
    return tuple(views)


# ── 러너 ──────────────────────────────────────────────────────────────────────


@dataclass
class Login:
    parent_id: uuid.UUID
    token: str


@dataclass
class StepOutcome:
    trace: Trace
    extra_misses: list[str] = field(default_factory=list)  # 단계 종류별 확인 (기대 미달로 센다)
    status_code: int | None = None


@dataclass
class WeekDriver:
    case: WeekCase
    client: AsyncClient
    monday: date
    child_id: uuid.UUID
    logins: dict[str, Login]
    stale_obs: frozenset[str]
    logs: Callable[[], Sequence[str]]
    # 진행 상태
    saved_by_tag: dict[str, list[SavedRow]] = field(default_factory=dict)
    drafts_by_tag: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    last_run: dict[str, tuple[str, bool]] = field(default_factory=dict)  # 보호자 → (run_id, 되묻기)
    approved: list[str] = field(default_factory=list)  # 일정을 만든 승인 단계 tag
    corrections: list[tuple[str, str, int]] = field(
        default_factory=list
    )  # (판정, 기록 id, 단계 번호)
    cited: list[tuple[int, str]] = field(default_factory=list)  # (단계 번호, 인용한 아이 기록 id)
    # 관심 id → (merge_key, 처음 confirmed 로 본 단계 번호) · (단계 번호, 인용한 관심 id)
    confirmed_at: dict[str, tuple[str, int]] = field(default_factory=dict)
    affinity_cites: list[tuple[int, str]] = field(default_factory=list)
    inputs: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (케이스, 보호자, 날짜) 한도용
    index: int = 0

    def _headers(self, by: str, *, key: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.logins[by].token}"}
        if key:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        return headers

    async def run(self, step: Step) -> StepOutcome:
        """단계 하나. 시계를 그 시각으로 옮기고 종류대로 돈다. 예외는 trace.error 로 남긴다."""
        self.index += 1
        moment = step_moment(self.monday, step.at)
        say = render_say(step.say, self.monday) if step.say else None
        trace = Trace(
            case_id=f"{self.case.id}:{step.tag}",
            attempt=0,
            about=step.about,
            event_level="sse",
            canaries=tuple(c for c in (say, self.case.world.nickname) if c),
            stale_ids=self.stale_obs,
            searched=None,
        )
        outcome = StepOutcome(trace)
        with time_machine.travel(moment, tick=True):
            await quota.reset()  # W 는 한도를 끄고 돈다 — 한도 분석은 inputs 로 따로 낸다
            try:
                trace.safety_before = await safety_snapshot(self.child_id)
                if step.kind in ("say", "reply"):
                    await self._say(step, say or "", trace)
                elif step.kind == "approve":
                    await self._approve(step, outcome)
                elif step.kind == "correct":
                    await self._correct(step, outcome)
                elif step.kind == "check":
                    await self._check(step, outcome)
                trace.safety_after = await safety_snapshot(self.child_id)
                trace.confirmed_days = await confirmed_days(self.child_id)
                for aid, key, state in await profile_rows(self.child_id):
                    if state == "confirmed" and aid not in self.confirmed_at:
                        self.confirmed_at[aid] = (key, self.index)
            except Exception as exc:  # 단계 하나의 예외로 주 전체를 멈추지 않는다
                trace.error = error_text(exc)
                trace.error_detail = error_trace(exc)
        trace.logs = tuple(self.logs())
        return outcome

    async def _say(self, step: Step, say: str, trace: Trace) -> None:
        reply_to: str | None = None
        if step.kind == "reply":
            previous = self.last_run.get(step.by)
            if previous is None or not previous[1]:
                trace.error = "앞 단계가 되묻지 않아 답 단계를 돌릴 수 없다"
                return
            reply_to = previous[0]
        before = await observation_snapshot(self.child_id)
        kept_before = await kept_observation_ids(self.child_id)
        events_before = await event_count(self.child_id)
        body: dict[str, Any] = {"text": say, "source": "home_input"}
        if reply_to:
            body["reply_to"] = reply_to
        if step.location:
            body["location"] = BUSAN
        started = time.monotonic()
        response = await self.client.post(
            f"/api/v1/children/{self.child_id}/inputs",
            json=body,
            headers=self._headers(step.by, key=True),
        )
        self.inputs.append((self.case.id, step.by, step_day(self.monday, step.at).isoformat()))
        if response.status_code != 202:
            trace.error = f"입력 접수 {response_code(response)}"
            return
        run_id = response.json()["run_id"]
        events = await self.client.get(
            f"/api/v1/runs/{run_id}/events", headers=self._headers(step.by), timeout=SSE_TIMEOUT
        )
        trace.latency_ms = int((time.monotonic() - started) * 1000)
        if events.status_code != 200:
            trace.error = f"SSE {events.status_code}"
            return
        info = apply_frames(trace, parse_sse(events.text))
        await _wait_curator()  # 다음 단계 전에 승격이 끝나 있어야 한다
        trace.run_id = run_id
        after = await observation_snapshot(self.child_id)
        trace.saved = tuple(row for oid, row in after.items() if oid not in before)
        trace.removed = removed_rows(
            {oid: row for oid, row in before.items() if oid in kept_before},
            await kept_observation_ids(self.child_id),
        )
        trace.stored_events = await event_count(self.child_id) - events_before
        trace.suggestions = await suggestion_views(self.child_id, run_id)
        trace.usage = tuple(_usage(run_id))
        trace.stale_ids = self.stale_obs | await stale_profile_ids(self.child_id, self.stale_obs)
        self.saved_by_tag[step.tag] = list(trace.saved)
        self.drafts_by_tag[step.tag] = info.drafts
        self.last_run[step.by] = (run_id, info.awaits_reply)
        self.cited += [(self.index, c.id) for s in trace.suggestions for c in s.child_citations]
        self.affinity_cites += [
            (self.index, c.id)
            for s in trace.suggestions
            for c in s.citations
            if c.kind == "profile_affinity"
        ]

    async def _approve(self, step: Step, outcome: StepOutcome) -> None:
        drafts = self.drafts_by_tag.get(step.approve_of or "", [])
        creates = [d for d in drafts if d.get("op") == "create"]
        if not creates:
            outcome.extra_misses.append(f"{step.approve_of} 단계에 승인할 일정 초안이 없다")
            return
        draft = creates[0]
        response = await self.client.post(
            f"/api/v1/children/{self.child_id}/events",
            json={"event": draft["event"], "items": draft.get("items", [])},
            headers=self._headers(step.by, key=True),
        )
        outcome.status_code = response.status_code
        if response.status_code != 201:
            outcome.extra_misses.append(f"일정 등록 {response_code(response)}")
            return
        self.approved.append(step.approve_of or step.tag)

    async def _correct(self, step: Step, outcome: StepOutcome) -> None:
        saved = [
            r for r in self.saved_by_tag.get(step.correct_of or "", []) if r.domain != "health"
        ]
        targets: list[SavedRow] = []
        for row in saved:
            if step.match is None or re.search(
                step.match, await observation_text(row.domain, row.id)
            ):
                targets.append(row)
        if not targets:
            outcome.extra_misses.append(f"{step.correct_of} 단계에서 고칠 기록을 찾지 못했다")
            return
        for row in targets:
            response = await self.client.post(
                "/api/v1/corrections",
                json={
                    "target_ref": {"kind": f"observation_{row.domain}", "id": row.id},
                    "verdict": step.verdict,
                    "child_id": str(self.child_id),
                },
                headers=self._headers(step.by),
            )
            outcome.status_code = response.status_code
            if response.status_code != 201:
                outcome.extra_misses.append(f"정정 {response_code(response)}")
                continue
            self.corrections.append((step.verdict or "", row.id, self.index))
        await _wait_curator()

    async def _check(self, step: Step, outcome: StepOutcome) -> None:
        check = step.check
        assert check is not None
        misses = outcome.extra_misses
        states = await profile_states(self.child_id)
        misses += interest_misses(
            states, confirmed=check.confirmed, not_confirmed=check.not_confirmed
        )
        misses += affinity_cite_misses(
            check.cited_affinity, confirmed_at=self.confirmed_at, cites=self.affinity_cites
        )
        if check.no_event_except:
            wanted = [t for t in check.no_event_except if t in self.approved]
            rows = await event_count(self.child_id)
            if rows != len(wanted):
                misses.append(f"일정 행 {rows}개 ≠ 승인한 {len(wanted)}개 {wanted}")
        misses += corrected_misses(
            check.corrected_not_cited, corrections=self.corrections, cited=self.cited
        )


def _usage(run_id: str) -> list[dict[str, Any]]:
    return [
        {
            "run_id": r.run_id,
            "role": r.role,
            "model": r.model,
            "prompt_tokens": r.prompt_tokens,
            "cached_prompt_tokens": r.cached_prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "reasoning_tokens": r.reasoning_tokens,
            "latency_ms": r.latency_ms,
        }
        for r in llm_usage.drain(limit=10_000)
        if r.run_id == run_id
    ]


async def _wait_curator() -> None:
    if trigger._background_tasks:
        await asyncio.gather(*list(trigger._background_tasks))


# ── 세계 심기 ─────────────────────────────────────────────────────────────────


async def seed_world(
    case: WeekCase, monday: date
) -> tuple[uuid.UUID, dict[str, Login], dict[str, str], frozenset[str]]:
    """보호자 · 아이 · 동의 · 알레르기 · 시작 전 기록을 commit 한다. 실행마다 새 Parent · Child.

    (아이 id, 보호자 로그인, 시작 기록 tag → id, 6개월 지난 시작 기록 id)
    """
    world = case.world
    run_tag = uuid.uuid4().hex[:8]
    year, month = divmod(monday.year * 12 + monday.month - 1 - world.months, 12)
    birth = date(year, month + 1, min(monday.day, 28))
    logins: dict[str, Login] = {}
    seed_ids: dict[str, str] = {}
    stale: set[str] = set()
    with time_machine.travel(step_moment(monday, "mon 06:00"), tick=True):
        async with async_session_factory() as session:
            owner: uuid.UUID | None = None
            child: Child | None = None
            for name in world.parents:
                parent = Parent(nickname=parent_nickname(name))
                session.add(parent)
                await session.flush()
                if child is None:
                    owner = parent.id
                    child = Child(owner_parent_id=owner, nickname=world.nickname, birth_date=birth)
                    session.add(child)
                    await session.flush()
                session.add(
                    ParentChild(
                        parent_id=parent.id, child_id=child.id, relation=ParentChildRelation.OTHER
                    )
                )
                token = f"w-{run_tag}-{name}-{uuid.uuid4().hex}"
                await seed_login(
                    session,
                    parent_id=parent.id,
                    token=token,
                    access_expires_at=datetime(monday.year, monday.month, monday.day, tzinfo=UTC)
                    + timedelta(days=8),
                )
                logins[name] = Login(parent.id, token)
            assert child is not None and owner is not None
            consents: list[tuple[ConsentScope, dict[str, uuid.UUID]]] = [
                (ConsentScope.CHILD_HEALTH, {"child_id": child.id}),
                *(
                    (ConsentScope.LOCATION, {"subject_parent_id": lg.parent_id})
                    for lg in logins.values()
                ),
            ]
            for scope, target in consents:
                version = await find_version(session, scope=scope, version=POLICY_VERSION)
                if version is None:
                    raise RuntimeError(f"약관 {scope.value}/{POLICY_VERSION} 행이 DB 에 없다")
                await record_consent(
                    session,
                    actor_parent_id=owner,
                    scope=scope,
                    action=ConsentAction.GRANTED,
                    policy_version_id=version.id,
                    **target,
                )
            for label in world.allergies:
                session.add(
                    HealthSafety(
                        child_id=child.id,
                        kind=SafetyKind.ALLERGY,
                        label=label,
                        severity=SafetySeverity.CLASS_3,
                        status=SafetyStatus.ACTIVE,
                        created_by=owner,
                        updated_by=owner,
                    )
                )
            for seed in world.seeds:
                day = monday - timedelta(days=seed.days_before_D)
                values: dict[str, Any] = {"raw_text": "심어 둔 기록", "polarity": 1} | dict(
                    seed.fields
                )
                values.setdefault("confidence_source", obs.ConfidenceSource.PARENT_DIRECT)
                row = OBS_MODEL[seed.domain](
                    child_id=child.id,
                    source_writer=owner,
                    observed_range=Range(day, day + timedelta(days=1)),
                    **values,
                )
                session.add(row)
                await session.flush()
                seed_ids[seed.tag] = str(row.id)
                if seed.days_before_D >= STALE_DAYS:
                    stale.add(str(row.id))
            await session.commit()
            child_id = child.id
    return child_id, logins, seed_ids, frozenset(stale)


@asynccontextmanager
async def week_lock() -> AsyncIterator[None]:
    """W 실행 전체를 세션 advisory lock 으로 감싼다. 프로세스가 죽으면 연결이 끊겨 저절로 풀린다."""
    async with async_session_factory() as session:
        await session.execute(
            text("select pg_advisory_lock(:a, :b)"), {"a": W_LOCK[0], "b": W_LOCK[1]}
        )
        try:
            yield
        finally:
            await session.execute(
                text("select pg_advisory_unlock(:a, :b)"), {"a": W_LOCK[0], "b": W_LOCK[1]}
            )


def recipe_keys() -> list[tuple[str, str]]:
    """W 가 심는 레시피 (source, source_id) — Food eval 세계의 것. source_id 가 메뉴 이름이라 실제
    적재분(번호)과 겹치지 않는다."""
    from tests.eval.agents.food.world import build_world

    return [
        (row.source, str(row.source_id))
        for row in build_world("C48", now=datetime.now(KST)).recipes
    ]


async def drop_leftovers() -> tuple[int, int]:
    """강제로 죽은 앞 실행이 남긴 W 행을 지운다 — W 표식 보호자(와 그 아이) · eval 레시피.
    `week_lock` 안에서만 부른다(돌고 있는 다른 실행의 행을 지우지 않게). (아이 수, 보호자 수)"""
    parents = [
        pid
        for (pid,) in await _rows("select id from parent where nickname like :m", m=f"{W_MARK}%")
    ]
    children = (
        [
            cid
            for (cid,) in await _rows(
                "select id from child where owner_parent_id = any(:p)", p=parents
            )
        ]
        if parents
        else []
    )
    if parents:
        await _drop(children, parents)
    await drop_recipes(recipe_keys())
    return len(children), len(parents)


async def seed_recipes() -> list[tuple[str, str]]:
    """Food 후보 풀 — 레시피 카탈로그가 비어 있으면 Food 가 추천을 못 낸다. Food eval 세계의
    레시피를 심고, 새로 넣은 행의 (source, source_id) 를 돌려준다(`drop_recipes` 가 지운다)."""
    from app.domains.food import repository as food_repo
    from app.domains.food.agent_reader import recipe_values
    from tests.eval.agents.food.world import build_world

    rows = build_world("C48", now=datetime.now(KST)).recipes
    async with async_session_factory() as session:
        existing = {(r.source, r.source_id) for r in await food_repo.list_resolved_recipes(session)}
        fresh = [row for row in rows if (row.source, row.source_id) not in existing]
        if fresh:
            await food_repo.upsert_recipes(session, rows=[recipe_values(row) for row in fresh])
            await session.commit()
    return [(row.source, str(row.source_id)) for row in fresh]


async def drop_recipes(keys: Sequence[tuple[str, str]]) -> None:
    """`seed_recipes` 가 넣은 행만 지운다 — 다른 통합 테스트가 빈 카탈로그를 가정할 수 있다."""
    async with async_session_factory() as session:
        for source, source_id in keys:
            await session.execute(
                text("delete from recipe_catalog where source=:s and source_id=:i"),
                {"s": source, "i": source_id},
            )
        await session.commit()


async def drop_world(child_id: uuid.UUID, logins: dict[str, Login]) -> None:
    """`seed_world` 가 만든 보호자 · 아이와 그 아래 행을 전부 지운다 — 실행마다 쌓이면 전역으로
    세는 통합 테스트가 깨진다. 제품의 삭제 경로(`purge_child` · `purge_parent`)를 타고, 그 경로가
    일부러 남기는 동의 증빙(`consent_retention`)은 시험 DB 라서 같이 지운다. 아이 → 보호자 순 —
    아이의 소유자 FK 때문이다. 나머지는 FK CASCADE 가 따라온다."""
    await _drop([child_id], [lg.parent_id for lg in logins.values()])


async def _drop(child_ids: Sequence[uuid.UUID], parent_ids: Sequence[uuid.UUID]) -> None:
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        for child_id in child_ids:
            await purge_child(session, child_id=child_id, now=now)
        for parent_id in parent_ids:
            await purge_parent(session, parent_id=parent_id, now=now)
        await session.execute(
            text(
                "delete from consent_retention "
                "where child_ref = any(:c) or subject_parent_ref = any(:p)"
            ),
            {"c": list(child_ids), "p": list(parent_ids)},
        )
        await session.commit()


def reset_process_state() -> None:
    from app.api.runs import registry

    registry.clear()
    idempotency.clear()
    # 하루 한도는 표(rate_event, #877)라 여기서 비우지 않는다 — 단계마다 quota.reset() 이 비운다
