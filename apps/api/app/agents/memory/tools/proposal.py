"""삭제 제안 tool (#535). 아무것도 지우지 않는다.

모델에게는 관찰 · 일정을 지우는 tool 이 없다. "지워줘" 에는 조회로 후보를 찾고 이 tool 로
요청에 맞는 id 를 고른다. 후보는 이 run 에서 종류마다 마지막으로 조회한 결과(`context.found`)이고
코드가 정한다 — 모델이 고른 id 는 그 안에서만 받는다. 제안은 Memory commit 때 저장되고,
보호자가 화면 카드에서 체크를 고쳐 버튼을 누르면 서버가 후보 안의 체크한 것만 지운다
(`docs/api/delete-proposal-v1.md`).
"""

from app.agents.memory.context import AgentContext, Proposal
from app.agents.memory.result import ErrorCode, ToolResult, fail, ok
from app.agents.memory.schemas.proposal import ProposeDeleteArgs
from app.agents.memory.store.ports import DELETE_KINDS, DeleteCandidates

RESOURCE = "delete_proposal"

_QUERY_FIRST = (
    "이 run 에서 조회한 기록 · 일정이 없다. 지울 기간과 종류로 조회 tool 을 먼저 부르고, "
    "그 결과의 id 로 다시 부른다."
)
_ONCE = "이미 삭제 제안을 남겼다. 다시 부르지 않는다. 고칠 것은 보호자가 카드에서 체크로 고친다."
_DONE = (
    "아직 아무것도 지우지 않았다. "
    "보호자가 화면 카드에서 체크를 확인하고 삭제 버튼을 눌러야 지워진다. "
    "지웠다고 말하지 말고, 카드에서 확인하고 삭제 버튼을 눌러 달라고 짧게 안내한다."
)


def candidate_tabs(
    context: AgentContext, picked: frozenset[str] = frozenset()
) -> list[DeleteCandidates]:
    """이 run 의 후보 탭. 행이 있는 종류만, 화면 순서(DELETE_KINDS)로."""
    return [
        DeleteCandidates(
            kind=kind,
            truncated=rows.truncated,
            ids=rows.ids,
            picked=tuple(oid for oid in rows.ids if oid in picked),
        )
        for kind in DELETE_KINDS
        if (rows := context.found.get(kind)) is not None and rows.ids
    ]


async def propose_delete(context: AgentContext, args: ProposeDeleteArgs) -> ToolResult:
    # 두 번째 호출이면 다른 검사보다 먼저 알린다 — 다시 조회하라는 안내를 받으면 또 부른다
    if context.proposals:
        return fail("create", RESOURCE, ErrorCode.INVALID_ARGS, _ONCE)
    tabs = candidate_tabs(context)
    if not tabs:
        return fail("create", RESOURCE, ErrorCode.TARGET_REQUIRED, _QUERY_FIRST)
    queried = {tab.kind for tab in tabs}
    unqueried = [kind for kind in args.all_kinds if kind not in queried]
    if unqueried:
        return fail(
            "create",
            RESOURCE,
            ErrorCode.TARGET_NOT_FOUND,
            f"{', '.join(unqueried)} 는 이 run 에서 조회한 결과가 없다. 조회한 종류만 all_kinds 에 "
            "넣는다. 그 종류도 지울 거면 먼저 조회한다.",
        )
    known = {oid for tab in tabs for oid in tab.ids}
    outside = [oid for oid in (*args.ids, *args.except_ids) if oid not in known]
    if outside:
        return fail(
            "create",
            RESOURCE,
            ErrorCode.TARGET_NOT_FOUND,
            f"{', '.join(outside)} 는 이 run 의 조회 결과에 없다. 조회 결과에 있는 id 만 넣는다. "
            "같은 종류를 다시 조회했으면 마지막 조회 결과만 후보다.",
        )
    whole = set(args.all_kinds)
    picked = (set(args.ids) | {oid for tab in tabs if tab.kind in whole for oid in tab.ids}) - set(
        args.except_ids
    )
    tabs = candidate_tabs(context, frozenset(picked))
    if any(tab.picked for tab in tabs):
        # 고른 것이 있는 종류만 탭으로 남긴다. 한 run 이 다른 요청(일정 수정 등)을 위해 조회한
        # 종류가 체크 없는 탭으로 붙으면, 잘못 누른 한 번에 상관없는 기록이 지워진다.
        # 하나도 못 골랐으면 조회한 종류를 모두 보여 보호자가 고르게 한다
        tabs = [tab for tab in tabs if tab.picked]
    candidates = sum(len(tab.ids) for tab in tabs)
    try:
        proposal_id = await context.store.propose_delete(
            parent_id=context.source_writer, run_id=context.run_id, tabs=tabs
        )
    except ValueError:
        # store 가 같은 run 의 제안을 이미 들고 있다 — 이 context 밖에서 남긴 것이다
        return fail("create", RESOURCE, ErrorCode.INVALID_ARGS, _ONCE)
    context.proposals.append(Proposal(proposal_id, tuple(tabs)))
    return ok(
        "create",
        RESOURCE,
        id=proposal_id,
        picked=sum(len(tab.picked) for tab in tabs),
        candidates=candidates,
        notice=_DONE,
    )


PROPOSAL_HANDLERS = {"propose_delete": propose_delete}
