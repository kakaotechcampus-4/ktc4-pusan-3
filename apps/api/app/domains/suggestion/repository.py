"""제안 표시·피드백과 관찰 상세의 근거 역조회."""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.child.models import ChildGrowthLog
from app.domains.food.repository import resolved_recipe_names
from app.domains.memory.observation.models import (
    ObservationActivity,
    ObservationEducation,
    ObservationFood,
    ObservationHealth,
    ObservationRoutine,
    ObservationStatus,
)
from app.domains.memory.profile.models import ProfileAffinity
from app.domains.suggestion.models import (
    Suggestion,
    SuggestionAgent,
    SuggestionEvent,
    SuggestionEvidence,
    SuggestionFeedback,
    SuggestionKind,
    SuggestionStatus,
)
from app.rules.menu_evidence import MENU_EVIDENCE_KIND


async def create_suggestion(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    agent: SuggestionAgent,
    kind: SuggestionKind,
    content: str,
    reason: str | None,
    citations: list[dict],
    expires_at: datetime,
    allergens: Sequence[str] = (),
    items: Sequence[str] = (),
    run_id: str | None = None,
    place_notice: str | None = None,
    warnings: Sequence[str] = (),
) -> Suggestion:
    """Agent 결과 저장. 일반/개인화 판정과 근거 필수 여부는 호출 계층에서 검증한다.

    `citations` · `allergens` · `items` 는 `SuggestionDraft.to_payload()` 의 같은 이름 칸을
    그대로 받는다. `citations` 는 `source_kind` · `source_id` · `note` 세 칸이다.
    `run_id` 는 그 추천을 만든 입력 run 이다. run 밖에서 만들면 None.
    `place_notice` 는 장소가 붙은 추천의 확인 문구다(코드 문구). 장소가 없으면 None.
    `warnings` 는 그 추천의 위험 경고 문구다(코드 문구). 경고가 없으면 비어 있다.
    """
    row = Suggestion(
        child_id=child_id,
        agent=SuggestionAgent(agent),
        kind=SuggestionKind(kind),
        content=content,
        reason=reason,
        allergens=list(allergens),
        items=list(items),
        expires_at=expires_at,
        status=SuggestionStatus.DRAFT,
        run_id=run_id,
        place_notice=place_notice,
        warnings=list(warnings),
    )
    session.add(row)
    await session.flush()
    for item in citations:
        session.add(
            SuggestionEvidence(
                suggestion_id=row.id,
                source_kind=item["source_kind"],
                source_id=uuid.UUID(str(item["source_id"])),
                note=item["note"],
            )
        )
    await session.flush()
    return row


async def find_suggestion(
    session: AsyncSession, *, child_id: uuid.UUID, suggestion_id: uuid.UUID
) -> Suggestion | None:
    return await session.scalar(
        select(Suggestion).where(Suggestion.child_id == child_id, Suggestion.id == suggestion_id)
    )


async def list_suggestions(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    status: SuggestionStatus | None = None,
    rated: bool | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Suggestion], int]:
    """최신순으로 `offset` 부터 `limit` 건과, 필터를 건 뒤의 전체 건수(total)를 돌려준다.

    `rated` — None 이면 거르지 않고, True 면 피드백을 남긴 것(`feedback IS NOT NULL`)만,
    False 면 아직 안 남긴 것(`feedback IS NULL`)만. 피드백은 approved · rejected 에만 저장되니
    draft 는 늘 미평가다 — 기본 목록의 draft 제외가 `rated=False` 에서도 그대로 걸린다.
    """
    conditions = [Suggestion.child_id == child_id]
    if status is not None:
        conditions.append(Suggestion.status == SuggestionStatus(status))
    else:
        # 기본 목록에서 draft 제외 — 07 화면은 approved/rejected 만 쓴다
        conditions.append(Suggestion.status != SuggestionStatus.DRAFT)
    # draft 조회 시 만료된 것은 제외 (lazy expiration)
    if status == SuggestionStatus.DRAFT:
        conditions.append(Suggestion.expires_at > func.now())
    if rated is not None:
        conditions.append(
            Suggestion.feedback.is_not(None) if rated else Suggestion.feedback.is_(None)
        )
    total = await session.scalar(select(func.count()).select_from(Suggestion).where(*conditions))
    stmt = (
        select(Suggestion)
        .where(*conditions)
        # created_at 이 같은 행(한 트랜잭션에서 만든 것)도 쪽을 넘나들며 안 흔들리게 id 로 끊는다
        .order_by(Suggestion.created_at.desc(), Suggestion.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all()), total or 0


async def set_feedback(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    feedback: SuggestionFeedback,
) -> Suggestion | None:
    """피드백은 채택/거절된 제안만 바꾼다. draft/expired 에는 적용하지 않는다."""
    return await session.scalar(
        update(Suggestion)
        .where(
            Suggestion.child_id == child_id,
            Suggestion.id == suggestion_id,
            Suggestion.status.in_([SuggestionStatus.APPROVED, SuggestionStatus.REJECTED]),
        )
        .values(feedback=SuggestionFeedback(feedback))
        .returning(Suggestion)
    )


async def approve_suggestions(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    suggestion_ids: list[uuid.UUID],
) -> list[Suggestion]:
    """draft → approved. 호출자가 소유·상태·만료를 사전 검증한 뒤 부른다.

    DB 에서 한 번에 UPDATE — draft + 만료 전인 것만 바꾼다.
    사전 검증과 UPDATE 사이에 상태가 바뀌었으면 개수 불일치로 빈 목록을 돌려준다.
    """
    result = await session.execute(
        update(Suggestion)
        .where(
            Suggestion.child_id == child_id,
            Suggestion.id.in_(suggestion_ids),
            Suggestion.status == SuggestionStatus.DRAFT,
            Suggestion.expires_at > func.now(),
        )
        .values(status=SuggestionStatus.APPROVED)
        .returning(Suggestion)
    )
    approved = list(result.scalars().all())
    if len(approved) != len(suggestion_ids):
        return []
    return approved


async def link_suggestions_to_event(
    session: AsyncSession,
    *,
    suggestion_ids: list[uuid.UUID],
    event_id: uuid.UUID,
) -> None:
    """추천 ↔ 일정 연결 INSERT."""
    for sid in suggestion_ids:
        session.add(SuggestionEvent(suggestion_id=sid, event_id=event_id))
    await session.flush()


async def is_any_linked(session: AsyncSession, *, suggestion_ids: list[uuid.UUID]) -> bool:
    """suggestion_ids 중 이미 일정에 연결된 것이 있는지."""
    stmt = select(exists().where(SuggestionEvent.suggestion_id.in_(suggestion_ids)))
    return bool(await session.scalar(stmt))


# 아이 기록 9종. 문서 행(food_doc 등)은 응답 evidence에서 제외한다.
# agents/common/refs.py ChildRecordKind와 같은 목록.
_CHILD_RECORD_KINDS = frozenset(
    (
        "observation_food",
        "observation_health",
        "observation_education",
        "observation_activity",
        "observation_routine",
        "profile_affinity",
        "child_growth_log",
        "notice",
        "daycare_meal",
    )
)

# agents/common/refs.py DocKind 와 같은 목록. 문서 행은 아이 데이터가 아니고 DB 테이블도
# 아직 없어서(YAML 시드 · uuid5 id) 종류만 본다
_DOC_KINDS = frozenset(("food_doc", "growth_doc", "activity_doc"))

# agents/common/refs.py MenuKind 와 같은 목록(#442). Food 일반 추천만 고른 메뉴(레시피 카탈로그
# 행)를 근거로 단다 — 문서 행이 없는 월령에도 일반 추천에 근거가 붙게. 실제 후보 풀의 레시피인지는
# 저장할 때 본다
_MENU_KINDS = frozenset((MENU_EVIDENCE_KIND,))

# 근거의 아이 기록 중 대상 테이블이 있는 것 — 같은 아이의 행이 있는지 본다(correction 과 같은
# 방식). 여기 없는 아이 기록(notice · daycare_meal)은 테이블이 생길 때까지 거절한다
_EVIDENCE_TARGETS = {
    "observation_food": ObservationFood,
    "observation_health": ObservationHealth,
    "observation_education": ObservationEducation,
    "observation_activity": ObservationActivity,
    "observation_routine": ObservationRoutine,
    "profile_affinity": ProfileAffinity,
    "child_growth_log": ChildGrowthLog,
}

SUGGESTION_TTL = timedelta(hours=24)
"""추천 draft 가 사는 시간. 승인 없는 추천은 이 시간 뒤 만료된다 (루트 §2)."""


class SuggestionEvidenceError(ValueError):
    """추천 묶음의 근거가 규칙에 어긋난다. 메시지에 추천 문장 · 근거 문장 · id 를 싣지 않는다."""


async def save_agent_suggestions(
    session: AsyncSession,
    *,
    child_id: uuid.UUID,
    run_id: str | None,
    payloads: Sequence[Mapping[str, Any]],
    now: datetime,
) -> list[uuid.UUID]:
    """도메인 Agent 결과 하나의 추천을 저장한다(flush 까지). commit 은 호출자(러너)가 한다.

    - `payloads` 의 한 항목은 `SuggestionDraft.to_payload()` 모양이다. 돌려주는 id 는 그 순서다.
    - 근거를 먼저 다 본 뒤에 쓴다. 하나라도 어긋나면 `SuggestionEvidenceError` 이고 한 행도
      쓰지 않는다 — 개인화인데 아이 기록 근거 0행 · 일반인데 아이 기록 근거 · 일반인데 문서 행
      (Food 는 메뉴 행도) 근거 0행 · Food 일반 추천이 아닌데 메뉴 근거 · 후보 풀에 없는 메뉴 ·
      정해지지 않은 근거 종류 · 같은 아이의 행이 없는 아이 기록 · 대상 테이블이 아직
      없는 아이 기록.
    - 한 추천이 같은 (source_kind, source_id) 를 두 번 인용하면 첫 인용만 남긴다(PK).
    - `status=draft` · `expires_at=now+SUGGESTION_TTL` 은 여기서 정한다.
    """
    checked = [
        await _checked_citations(session, child_id=child_id, payload=item) for item in payloads
    ]
    ids: list[uuid.UUID] = []
    for item, citations in zip(payloads, checked, strict=True):
        row = await create_suggestion(
            session,
            child_id=child_id,
            agent=SuggestionAgent(item["agent"]),
            kind=SuggestionKind(item["kind"]),
            content=item["content"],
            reason=item["reason"],
            citations=citations,
            expires_at=now + SUGGESTION_TTL,
            allergens=item["allergens"],
            items=item["items"],
            run_id=run_id,
            # 장소가 없는 추천 · 이 칸 전에 만든 payload 에는 키가 없다
            place_notice=item.get("place_notice"),
            # 경고가 없는 추천은 빈 목록이다. 이 칸 전에 만든 payload 에는 키가 없다
            warnings=item.get("warnings", ()),
        )
        ids.append(row.id)
    return ids


async def _checked_citations(
    session: AsyncSession, *, child_id: uuid.UUID, payload: Mapping[str, Any]
) -> list[dict]:
    """추천 한 건의 인용을 검사해 돌려준다. 같은 근거를 두 번 인용했으면 첫 인용만 남긴다."""
    SuggestionAgent(payload["agent"])  # 정해지지 않은 Agent 면 쓰기 전에 ValueError
    kind = SuggestionKind(payload["kind"])
    citations: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in payload["citations"]:
        key = (item["source_kind"], str(uuid.UUID(str(item["source_id"]))))
        if key not in seen:
            seen.add(key)
            citations.append(dict(item))
    known = _CHILD_RECORD_KINDS | _DOC_KINDS | _MENU_KINDS
    if any(item["source_kind"] not in known for item in citations):
        raise SuggestionEvidenceError("정해지지 않은 근거 종류다")
    child = [item for item in citations if item["source_kind"] in _CHILD_RECORD_KINDS]
    menus = [item for item in citations if item["source_kind"] in _MENU_KINDS]
    if kind is SuggestionKind.PERSONALIZED and not child:
        raise SuggestionEvidenceError("개인화 추천인데 아이 기록 근거가 없다")
    if kind is SuggestionKind.GENERAL and child:
        raise SuggestionEvidenceError("일반 추천에 아이 기록 근거가 있다")
    if menus and (payload["agent"] != "food" or kind is not SuggestionKind.GENERAL):
        # 메뉴 근거는 Food 일반 추천만 단다(#442). 개인화는 아이 기록이 근거다
        raise SuggestionEvidenceError("메뉴 근거는 Food 일반 추천에만 단다")
    # 보호자가 놀이를 정해 물은 Activity 일반 추천(`requested`)은 그 요청이 기준이라 문서 행이
    # 없어도 된다 — "물놀이 뭐 하지?" 에 시드에 없는 놀이를 낼 수 있게 (루트 §2). Food 는 후보가
    # 레시피북에서만 나와야 해서 면제하지 않는다 (#597 리뷰)
    requested = payload.get("requested", False) and payload["agent"] == "activity"
    if (
        kind is SuggestionKind.GENERAL
        and not requested
        and not any(item["source_kind"] in _DOC_KINDS | _MENU_KINDS for item in citations)
    ):
        raise SuggestionEvidenceError("일반 추천인데 문서 행 근거가 없다")
    if menus:
        # 실제 후보 풀(검수된 레시피)의 메뉴여야 근거다 — 아니면 근거 0행과 같다
        names = await resolved_recipe_names(session)
        if any(uuid.UUID(str(item["source_id"])) not in names for item in menus):
            raise SuggestionEvidenceError("메뉴 근거가 레시피 카탈로그에 없다")
    for item in child:
        model = _EVIDENCE_TARGETS.get(item["source_kind"])
        if model is None:
            raise SuggestionEvidenceError("아직 확인할 수 없는 아이 기록 종류다")
        source_id = uuid.UUID(str(item["source_id"]))
        found = await session.scalar(
            select(model.id).where(model.id == source_id, model.child_id == child_id)
        )
        if found is None:
            raise SuggestionEvidenceError("근거 기록이 없거나 다른 아이의 기록이다")
    return citations


async def list_evidence(
    session: AsyncSession, *, suggestion_id: uuid.UUID
) -> list[SuggestionEvidence]:
    """추천 한 건의 근거 행 (아이 기록만, 문서 행 제외)."""
    stmt = (
        select(SuggestionEvidence)
        .where(
            SuggestionEvidence.suggestion_id == suggestion_id,
            SuggestionEvidence.source_kind.in_(_CHILD_RECORD_KINDS),
        )
        .order_by(SuggestionEvidence.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def list_run_suggestions(
    session: AsyncSession, *, child_id: uuid.UUID, run_id: str, agent: str, now: datetime
) -> list[Suggestion]:
    """그 run 에서 그 Agent 가 낸 추천 — 채택한 것 전부와 만료 전 draft (#249 판단 순서 1).

    채택한 것도 돌려준다. 일부만 채택하고 새로고침했을 때 남은 draft 만 오면 화면이 "1가지 중에서"
    를 그린다. 만료된 draft · 거절 · 만료 상태는 뺀다.
    """
    stmt = (
        select(Suggestion)
        .where(
            Suggestion.child_id == child_id,
            Suggestion.run_id == run_id,
            Suggestion.agent == SuggestionAgent(agent),
            (Suggestion.status == SuggestionStatus.APPROVED)
            | ((Suggestion.status == SuggestionStatus.DRAFT) & (Suggestion.expires_at > now)),
        )
        .order_by(Suggestion.created_at, Suggestion.id)
    )
    return list((await session.scalars(stmt)).all())


async def list_suggestions_by_ids(
    session: AsyncSession, *, child_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> list[Suggestion]:
    """방금 저장한 추천을 저장 순서대로. 다른 아이의 id 는 섞이지 않는다."""
    if not ids:
        return []
    rows = (
        await session.scalars(
            select(Suggestion).where(Suggestion.child_id == child_id, Suggestion.id.in_(ids))
        )
    ).all()
    by_id = {row.id: row for row in rows}
    return [by_id[i] for i in ids if i in by_id]


async def count_child_records(
    session: AsyncSession, *, child_id: uuid.UUID, kinds: Sequence[str]
) -> int:
    """그 주제로 쌓인 관찰 건수 — 화면이 "기록 N건" 으로 그대로 보여 준다 (루트 §2).

    남아 있는 관찰(active · stand_alone)만 센다. 잘못된 기록(inactive) · 지운 기록은 빼고,
    관찰이 아닌 종류는 세지 않는다.
    """
    kept = (ObservationStatus.ACTIVE, ObservationStatus.STAND_ALONE)
    total = 0
    for kind in kinds:
        model = _EVIDENCE_TARGETS.get(kind)
        if model is None or not kind.startswith("observation_"):
            continue
        total += (
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.child_id == child_id, model.status.in_(kept))
            )
            or 0
        )
    return total


async def list_general_evidence_batch(
    session: AsyncSession, *, suggestion_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SuggestionEvidence]]:
    """일반 추천의 근거 — 문서 행과 메뉴 행(Food, #442). 아이 기록 근거와 섞지 않는다 — 화면
    타입이 다르다."""
    return await _evidence_batch(session, suggestion_ids, _DOC_KINDS | _MENU_KINDS)


async def list_evidence_batch(
    session: AsyncSession, *, suggestion_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SuggestionEvidence]]:
    """여러 추천의 근거를 한 번에 조회한다 (아이 기록만, 문서 행 제외)."""
    return await _evidence_batch(session, suggestion_ids, _CHILD_RECORD_KINDS)


async def _evidence_batch(
    session: AsyncSession, suggestion_ids: list[uuid.UUID], kinds: frozenset[str]
) -> dict[uuid.UUID, list[SuggestionEvidence]]:
    """추천마다 그 종류의 근거 행을 만든 순서대로. 근거가 없는 추천도 빈 목록으로 둔다."""
    if not suggestion_ids:
        return {}
    stmt = (
        select(SuggestionEvidence)
        .where(
            SuggestionEvidence.suggestion_id.in_(suggestion_ids),
            SuggestionEvidence.source_kind.in_(kinds),
        )
        .order_by(SuggestionEvidence.suggestion_id, SuggestionEvidence.created_at)
    )
    result: dict[uuid.UUID, list[SuggestionEvidence]] = {sid: [] for sid in suggestion_ids}
    for row in (await session.scalars(stmt)).all():
        result[row.suggestion_id].append(row)
    return result


async def list_suggestions_using_observation(
    session: AsyncSession, *, child_id: uuid.UUID, kind: str, observation_id: uuid.UUID
) -> list[Suggestion]:
    """그 관찰을 근거로 쓴 추천. 근거는 `suggestion_evidence` 행이다."""
    stmt = (
        select(Suggestion)
        .join(SuggestionEvidence, SuggestionEvidence.suggestion_id == Suggestion.id)
        .where(
            Suggestion.child_id == child_id,
            SuggestionEvidence.source_kind == kind,
            SuggestionEvidence.source_id == observation_id,
        )
        .order_by(Suggestion.created_at.desc(), Suggestion.id.desc())
    )
    return list((await session.scalars(stmt)).all())
