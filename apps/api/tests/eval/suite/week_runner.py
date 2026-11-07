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
import traceback
import uuid
from collections.abc import Callable, Sequence
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
from app.domains.identity.models import Parent
from app.domains.memory.curator import trigger
from app.domains.memory.observation import models as obs
from app.domains.policy.repository import find_version
from app.domains.safety.models import HealthSafety, SafetyKind, SafetySeverity, SafetyStatus
from app.infra.db.session import async_session_factory
from tests.eval.suite.schema import WEEKDAYS, Step, WeekCase
from tests.eval.suite.trace import CitationView, SavedRow, SuggestionView, Trace
from tests.eval.suite.week_frames import apply_frames, parse_sse
from tests.integration.api.conftest import seed_login

KST = ZoneInfo("Asia/Seoul")
STALE_DAYS = 183  # world.py 와 같다 — 루트 §2 "6개월 이상"
POLICY_VERSION = "2026-11-03"  # 지금 유효한 약관 판 (child_health · location)
BUSAN = {"lat": 35.18, "lon": 129.08}
SSE_TIMEOUT = 150.0
OBS_TABLES = ("food", "health", "education", "activity", "routine")
PROMOTABLE_TABLES = ("food", "education", "activity")
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
            quota.clear()  # W 는 한도를 끄고 돈다 — 한도 분석은 inputs 로 따로 낸다
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
            except Exception as exc:  # 단계 하나의 예외로 주 전체를 멈추지 않는다
                trace.error = f"{type(exc).__name__}: {exc}"
                trace.error_detail = traceback.format_exc()
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
            trace.error = f"입력 접수 {response.status_code}: {response.text[:200]}"
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
        trace.stored_events = await event_count(self.child_id) - events_before
        trace.suggestions = await suggestion_views(self.child_id, run_id)
        trace.usage = tuple(_usage(run_id))
        trace.stale_ids = self.stale_obs | await stale_profile_ids(self.child_id, self.stale_obs)
        self.saved_by_tag[step.tag] = list(trace.saved)
        self.drafts_by_tag[step.tag] = info.drafts
        self.last_run[step.by] = (run_id, info.awaits_reply)
        self.cited += [(self.index, c.id) for s in trace.suggestions for c in s.child_citations]

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
            outcome.extra_misses.append(f"일정 등록 {response.status_code}: {response.text[:160]}")
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
                outcome.extra_misses.append(f"정정 {response.status_code}: {response.text[:160]}")
                continue
            self.corrections.append((step.verdict or "", row.id, self.index))
        await _wait_curator()

    async def _check(self, step: Step, outcome: StepOutcome) -> None:
        check = step.check
        assert check is not None
        misses = outcome.extra_misses
        states = await profile_states(self.child_id)
        for word in check.confirmed:
            hit = {k: s for k, s in states.items() if word in k}
            if not any(s == "confirmed" for s in hit.values()):
                misses.append(f"'{word}' 관심이 confirmed 가 아니다 ({hit or '없음'})")
        for word in check.not_confirmed:
            hit = {k: s for k, s in states.items() if word in k}
            if any(s == "confirmed" for s in hit.values()):
                misses.append(f"'{word}' 관심이 confirmed 다 ({hit})")
        if check.no_event_except:
            wanted = [t for t in check.no_event_except if t in self.approved]
            rows = await event_count(self.child_id)
            if rows != len(wanted):
                misses.append(f"일정 행 {rows}개 ≠ 승인한 {len(wanted)}개 {wanted}")
        for verdict in check.corrected_not_cited:
            ids = {oid: n for v, oid, n in self.corrections if v == verdict}
            if not ids:
                misses.append(f"{verdict} 로 고친 기록이 없어 점검할 수 없다")
                continue
            leaked = sorted({oid for n, oid in self.cited if oid in ids and n > ids[oid]})
            if leaked:
                misses.append(f"{verdict} 로 고친 기록이 뒤 추천의 근거에 있다 ({len(leaked)}건)")


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
                parent = Parent(nickname=name)
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


def reset_process_state() -> None:
    from app.api.runs import registry

    registry.clear()
    idempotency.clear()
    quota.clear()
