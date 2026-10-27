"""러너와 05 추천 엔드포인트가 같이 쓰는 Agent 포트 — 짧은 DB 세션으로 연다 (#257 · #249).

도메인 Agent 의 기억 포트는 호출마다, 추천 저장은 Agent 결과 하나마다 세션 하나다. 요청 세션이나
run 세션을 같이 타면 동시에 도는 task 들이 AsyncSession 을 함께 써서 깨진다.
기록 건수도 호출마다 세션 하나다.
Growth 도서 어댑터(정보나루 · 이미 낸 책)도 같다 — 인증키가 있을 때만 만든다.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from app.agents import entrypoint
from app.api.deps.db import background_session

# 그 Agent 의 "쌓인 기록 N건" 을 셀 관찰 종류 (루트 §2). 04 블록(#227)과 05(#249)가 같은 수를
# 말하게 한 곳에 둔다. 표에 없는 Agent 는 0건이다
RECORD_KINDS: dict[str, tuple[str, ...]] = {
    "food": ("observation_food",),
    "activity": ("observation_activity",),
    # 놀이 기록은 Growth 가 근거로 읽기만 해서 세지 않는다 — Activity 의 기록이다
    "growth": ("observation_education", "observation_routine"),
}


def domain_store_opener(child_id: UUID) -> entrypoint.StoreOpener:
    from app.domains.memory.store.db_store import DbMemoryStore

    @asynccontextmanager
    async def domain_store() -> AsyncIterator[DbMemoryStore]:
        # 읽기만 한다. Memory 는 도메인 단계 전에 commit 되므로 방금 저장한 관찰도 보인다
        async with background_session() as read_session:
            yield DbMemoryStore(read_session, child_id=child_id)

    return domain_store


def growth_log_reader() -> entrypoint.GrowthLogReader:
    from app.domains.child.agent_reader import DbGrowthLogReader

    # 호출마다 짧은 세션으로 child_growth_log 를 읽는다 — run 세션을 같이 타지 않게 (#257)
    return DbGrowthLogReader(background_session)


def suggestion_saver(
    child_id: UUID, *, on_commit: Callable[[], None] | None = None
) -> entrypoint.SaveSuggestions:
    from app.domains.suggestion.repository import save_agent_suggestions

    async def save_suggestions(batch: entrypoint.SuggestionBatch) -> tuple[str, ...]:
        # Agent 결과 하나마다 짧은 세션 · 트랜잭션 하나다(트랜잭션 회의 표)
        async with background_session() as save_session:
            ids = await save_agent_suggestions(
                save_session,
                child_id=child_id,
                run_id=batch.run_id,
                payloads=[draft.to_payload() for draft in batch.drafts],
                now=datetime.now(UTC),
            )
            await save_session.commit()
            # commit 이 돌아온 뒤에만 알린다. 결과를 모르면 표시가 없어 failed 다(결정 4)
            if on_commit is not None:
                on_commit()
        return tuple(str(row_id) for row_id in ids)

    return save_suggestions


def record_counter(child_id: UUID) -> entrypoint.CountRecords:
    from app.domains.suggestion.repository import count_child_records

    async def count_records(agent: str) -> int:
        # 읽기만 한다. 결과 하나마다 짧은 세션 — run 세션을 도메인 task 들과 같이 타지 않는다
        async with background_session() as session:
            return await count_child_records(
                session, child_id=child_id, kinds=RECORD_KINDS.get(agent, ())
            )

    return count_records


def food_stores() -> dict[str, object]:
    """Food 가 읽는 카탈로그 · 추천 이력 어댑터. 호출마다 짧은 세션을 연다 — 러너와 05 가 같이 쓴다.

    입력 run 과 05 가 같은 어댑터를 넘겨야 두 경로의 후보 풀이 갈라지지 않는다. 만들 때는 세션을
    열지 않는다(`background_session` 을 함수로 넘길 뿐이다).
    """
    from app.domains.food.agent_reader import DbMenuCatalog, DbRecipeCatalog
    from app.domains.suggestion.agent_reader import DbSuggestionHistory

    return {
        "food_catalog": DbMenuCatalog(background_session),
        "food_recipes": DbRecipeCatalog(background_session),
        "food_suggestions": DbSuggestionHistory(background_session),
    }


def safety_readers() -> dict[str, object]:
    """Food · Activity · Growth 가 읽는 `health_safety` 어댑터 — 러너와 05 가 같이 쓴다 (#460).

    어댑터 하나를 셋에게 넘기고, Food 는 `food_safety`(active · kind 전부), Activity 와 Growth 는
    `activity_safety` · `growth_safety`(allergy · environmental · status 전부)로 읽는다. 안 넘기면
    Food · Activity 와 Growth 교육이 `blocked.safety` 로 닫힌다. 호출마다 짧은 세션을 연다 —
    만들 때는 열지 않는다.
    """
    from app.domains.safety.agent_reader import DbSafetyReader

    reader = DbSafetyReader(background_session)
    return {"food_safety": reader, "activity_safety": reader, "growth_safety": reader}


def growth_book_ports() -> dict[str, object]:
    """Growth 도서 어댑터 — 정보나루 검색(검색어 캐시 포함)과 이미 낸 책. 러너와 05 가 같이 쓴다.

    인증키(`DATA4LIBRARY_AUTH_KEY`)가 없으면 둘 다 넘기지 않는다 — 도서는 `closed.book_api` 로
    닫힌다. 키 없이 빈 목록으로 채우지 않는다. 검색어 캐시는 `REDIS_URL` 이 있을 때만 쓴다(없으면
    정보나루를 바로 부른다). 만들 때는 세션 · HTTP 연결을 열지 않는다 — 호출마다 연다.
    """
    from app.core.config import settings

    key = settings.DATA4LIBRARY_AUTH_KEY.strip()
    if not key:
        return {}
    from app.domains.suggestion.agent_reader import DbIssuedBooks
    from app.infra.redis import get_redis
    from app.integrations.data4library.client import Data4LibraryBooks

    return {
        "growth_books": Data4LibraryBooks(auth_key=key, cache=get_redis()),
        "growth_issued_books": DbIssuedBooks(background_session),
    }


def health_ports() -> dict[str, object]:
    """Health 가 읽는 안전 · 복약 · 아이 기본 정보 어댑터 — 러너가 넘긴다.

    복약 어댑터는 읽기와 확인받은 중단 쓰기를 한 객체가 하고, 호출마다 짧은 세션을 연다. 만들 때는
    세션을 열지 않는다. 05(`suggest`)는 Health 를 돌리지 않아 `safety_readers()` 에 섞지 않는다.
    """
    from app.domains.child.agent_reader import DbChildBasicReader
    from app.domains.medication.agent_reader import DbMedicationStore
    from app.domains.safety.agent_reader import DbSafetyReader

    medication = DbMedicationStore(background_session)
    return {
        "health_safety": DbSafetyReader(background_session),
        "health_medication": medication,
        "health_medication_writer": medication,
        "health_child": DbChildBasicReader(background_session),
    }
