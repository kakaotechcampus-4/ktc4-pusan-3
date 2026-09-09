"""observation 4테이블(food / health / education / activity)의 CRUD tool 16개.

각 tool 흐름:
  - 날짜·시각 표현을 datetime_rules 로 확정 -> context 값 주입 -> NOT NULL 기본값 채움
  -> store 호출 -> ToolResult 반환

LLM은 표현까지만 제공하고, 계산과 기본값은 이쪽에서 해결
"""

from typing import Any, Callable

from app.agents.common.datetime_rules import (
    DateParseError,
    build_observed_range,
    combine,
    resolve_date,
    resolve_query_bound,
    resolve_time,
)
from app.agents.memory.context import AgentContext
from app.agents.memory.result import ErrorCode, ToolResult, fail, ok
from app.agents.memory.schemas.observation import (
    ObservationActivityCreate,
    ObservationActivityUpdate,
    ObservationEducationCreate,
    ObservationEducationUpdate,
    ObservationFoodCreate,
    ObservationFoodUpdate,
    ObservationHealthCreate,
    ObservationHealthUpdate,
    ObservationQueryArgs,
    RecordRef,
)

# create 인자 중 store가 따로 받는 것들. fields에 중복으로 넣지 않는다
_CREATE_HANDLED = {"raw_text", "observed_on", "temporal_direction"}

# update도 날짜를 바꿀 수 있다. 이 셋은 fields로 내려보내지 않고 따로 푼다
_UPDATE_HANDLED = {"observation_id", "observed_on", "temporal_direction"}

# subject는 NOT NULL이고 임베딩 입력에 핵심. 도메인마다 다른 필드로 정의됨
_SUBJECT_FIELD = {"food": "subject", "education": "topic", "activity": "activity"}

def _resource(domain: str) -> str:
    return f"observation_{domain}"


def _subject_of(domain: str, fields: dict[str, Any]) -> str | None:
    key = _SUBJECT_FIELD.get(domain)
    return fields.get(key) if key else None


async def _create(
    context: AgentContext,
    args: Any,
    *,
    domain: str,
    resolve_observed_time: bool = False,
) -> ToolResult:
    resource = _resource(domain)
    fields = args.model_dump(exclude=_CREATE_HANDLED)

    try:
        day = resolve_date(
            args.observed_on, today=context.today, direction=args.temporal_direction
        )
    except DateParseError as exc:
        return fail("create", resource, ErrorCode.DATE_UNPARSEABLE, _date_remedy(exc))

    if resolve_observed_time:
        # 표현을 그대로 저장하지 않는다. 확정 못 하면 모델이 비우고 다시 부르게 한다
        try:
            moment = resolve_time(fields.pop("observed_time", None))
        except DateParseError as exc:
            return fail("create", resource, ErrorCode.DATE_UNPARSEABLE, _time_remedy(exc))
        fields["observed_time"] = (
            combine(day, moment, context.timezone).isoformat() if moment else None
        )

    subject = _subject_of(domain, fields)
    if subject is not None:
        fields["subject"] = subject

    row = await context.store.create_observation(
        domain=domain,
        child_id=context.child_id,
        source_writer=context.source_writer,
        raw_text=args.raw_text,
        observed_on=day,
        observed_range=build_observed_range(day),
        fields=fields,
    )
    return ok("create", resource, id=row.id, observed_on=day.isoformat())


async def _query(context: AgentContext, args: ObservationQueryArgs, *, domain: str) -> ToolResult:
    resource = _resource(domain)
    try:
        # direction은 호출하는 쪽이 정함
        bounds = {
            "date_from": resolve_query_bound(
                args.date_from,
                today=context.today,
                direction=args.temporal_direction,
                is_end=False,
            ),
            "date_to": resolve_query_bound(
                args.date_to,
                today=context.today,
                direction=args.temporal_direction,
                is_end=True,
            ),
        }
    except DateParseError as exc:
        return fail("query", resource, ErrorCode.DATE_UNPARSEABLE, _date_remedy(exc))

    rows = await context.store.query_observations(
        domain=domain,
        child_id=context.child_id,
        raw_text_query=args.raw_text_query,
        **bounds,
    )
    return ok(
        "query",
        resource,
        count=len(rows),
        records=[row.to_summary() for row in rows],
    )


async def _update(
    context: AgentContext,
    args: Any,
    *,
    domain: str,
    resolve_observed_time: bool = False,
) -> ToolResult:
    resource = _resource(domain)
    current = await context.store.get_observation(
        domain=domain, observation_id=args.observation_id
    )
    if current is None:
        return fail("update", resource, ErrorCode.TARGET_NOT_FOUND, _not_found(domain))

    fields = args.model_dump(exclude=_UPDATE_HANDLED)

    day = current.observed_on
    if args.observed_on is not None:
        try:
            day = resolve_date(
                args.observed_on, today=context.today, direction=args.temporal_direction
            )
        except DateParseError as exc:
            return fail("update", resource, ErrorCode.DATE_UNPARSEABLE, _date_remedy(exc))
        # 날짜가 바뀌면 observed_range 도 같이 바꾼다. 어긋나면 조회와 감쇠가 다른 날을 본다
        fields["observed_range"] = build_observed_range(day)

    if resolve_observed_time and fields.get("observed_time") is not None:
        try:
            moment = resolve_time(fields["observed_time"])
        except DateParseError as exc:
            return fail("update", resource, ErrorCode.DATE_UNPARSEABLE, _time_remedy(exc))
        # 날짜를 같이 바꿨으면 새 날짜에, 아니면 원래 관찰 일자에 시각을 얹는다
        fields["observed_time"] = (
            combine(day, moment, context.timezone).isoformat() if moment else None
        )

    subject = _subject_of(domain, fields)
    if subject is not None:
        fields["subject"] = subject      # topic/activity 를 바꾸면 subject 도 따라간다

    row = await context.store.update_observation(
        domain=domain,
        observation_id=args.observation_id,
        fields=fields,
        observed_on=day if args.observed_on is not None else None,
    )
    if row is None:
        return fail("update", resource, ErrorCode.TARGET_NOT_FOUND, _not_found(domain))
    return ok("update", resource, id=row.id, observed_on=row.observed_on.isoformat())


async def _delete(context: AgentContext, args: RecordRef, *, domain: str) -> ToolResult:
    resource = _resource(domain)
    deleted = await context.store.delete_observation(
        domain=domain, observation_id=args.observation_id
    )
    if not deleted:
        return fail("delete", resource, ErrorCode.TARGET_NOT_FOUND, _not_found(domain))
    return ok("delete", resource, id=args.observation_id)


def _not_found(domain: str) -> str:
    return f"그 id의 기록이 없다. query_{_resource(domain)} 로 대상을 먼저 찾는다."


# 실패 메시지에는 다음에 뭘 해야 하는지까지 저장 후 전달
def _date_remedy(exc: DateParseError) -> str:
    return f"{exc} 오늘·어제·모레·금요일 같은 원문의 시간 표현이나 YYYY-MM-DD 형태로 넣는다."


def _time_remedy(exc: DateParseError) -> str:
    return (
        f"{exc} 시각은 '오후 2시'·'저녁 7시'·'14:30' 처럼 몇 시인지 알 수 있을 때만 넣는다. "
        "'낮'/'아침'처럼 시각이 특정되지 않으면 비워두고 다시 호출한다."
    )


def _query_handler(domain: str) -> Callable[..., Any]:
    """조회 tool 4개는 인자도 동작도 같다. 이름만 다르게 만들어 registry에 올린다."""

    async def handler(context: AgentContext, args: ObservationQueryArgs) -> ToolResult:
        return await _query(context, args, domain=domain)
    return handler


# food
async def create_observation_food(
    context: AgentContext, args: ObservationFoodCreate
) -> ToolResult:
    return await _create(context, args, domain="food")


async def update_observation_food(
    context: AgentContext, args: ObservationFoodUpdate
) -> ToolResult:
    return await _update(context, args, domain="food")


async def delete_observation_food(context: AgentContext, args: RecordRef) -> ToolResult:
    return await _delete(context, args, domain="food")


# health
async def create_observation_health(
    context: AgentContext, args: ObservationHealthCreate
) -> ToolResult:
    return await _create(context, args, domain="health", resolve_observed_time=True)


async def update_observation_health(
    context: AgentContext, args: ObservationHealthUpdate
) -> ToolResult:
    return await _update(context, args, domain="health", resolve_observed_time=True)


async def delete_observation_health(context: AgentContext, args: RecordRef) -> ToolResult:
    return await _delete(context, args, domain="health")


# education
async def create_observation_education(
    context: AgentContext, args: ObservationEducationCreate
) -> ToolResult:
    return await _create(context, args, domain="education")


async def update_observation_education(
    context: AgentContext, args: ObservationEducationUpdate
) -> ToolResult:
    return await _update(context, args, domain="education")


async def delete_observation_education(context: AgentContext, args: RecordRef) -> ToolResult:
    return await _delete(context, args, domain="education")


# activity
async def create_observation_activity(
    context: AgentContext, args: ObservationActivityCreate
) -> ToolResult:
    return await _create(context, args, domain="activity")


async def update_observation_activity(
    context: AgentContext, args: ObservationActivityUpdate
) -> ToolResult:
    return await _update(context, args, domain="activity")


async def delete_observation_activity(context: AgentContext, args: RecordRef) -> ToolResult:
    return await _delete(context, args, domain="activity")


query_observation_food = _query_handler("food")
query_observation_health = _query_handler("health")
query_observation_education = _query_handler("education")
query_observation_activity = _query_handler("activity")

OBSERVATION_HANDLERS = {
    "create_observation_food": create_observation_food,
    "query_observation_food": query_observation_food,
    "update_observation_food": update_observation_food,
    "delete_observation_food": delete_observation_food,
    "create_observation_health": create_observation_health,
    "query_observation_health": query_observation_health,
    "update_observation_health": update_observation_health,
    "delete_observation_health": delete_observation_health,
    "create_observation_education": create_observation_education,
    "query_observation_education": query_observation_education,
    "update_observation_education": update_observation_education,
    "delete_observation_education": delete_observation_education,
    "create_observation_activity": create_observation_activity,
    "query_observation_activity": query_observation_activity,
    "update_observation_activity": update_observation_activity,
    "delete_observation_activity": delete_observation_activity,
}
