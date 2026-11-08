"""케이스가 적은 기대값을 판정한다. 불변식(§2)과 달리 케이스마다 다르다.

무거운 판정은 불변식이 맡고, 여기는 가볍게 — 라우팅 포함/제외 · 저장 도메인 · 추천 종류 ·
안내 코드 · Memory 가 부른 tool.
"""

from datetime import date, timedelta

from app.agents.memory.bundles import MUTATING_PREFIXES
from tests.eval.suite.schema import Expect, ToolsExpect
from tests.eval.suite.trace import Trace

MUTATING = "쓰기"  # must_not 에 적으면 쓰기 tool 전부 (test_memory.py 의 MUTATING 과 같은 뜻)


def check_expect(trace: Trace, expect: Expect, *, today: date) -> list[str]:
    misses: list[str] = []
    if expect.route is not None:
        route = expect.route
        if route.intent is not None and trace.intent != route.intent:
            misses.append(f"의도 {trace.intent} ≠ {route.intent}")
        misses += [f"route 에 {a} 없음" for a in route.include if a not in trace.route]
        misses += [f"route 에 {a} 있음" for a in route.exclude if a in trace.route]
    if expect.saved is not None:
        saved = expect.saved
        count = len(trace.saved)
        if saved.child_rows is not None and count != saved.child_rows:
            misses.append(f"저장 {count}행 ≠ {saved.child_rows}")
        if saved.min_rows is not None and count < saved.min_rows:
            misses.append(f"저장 {count}행 < {saved.min_rows}")
        for domain in saved.domains:
            if not any(row.domain == domain for row in trace.saved):
                misses.append(f"{domain} 저장 없음")
        if saved.days_ago is not None:
            want = today - timedelta(days=saved.days_ago)
            wrong = [row.observed_on.isoformat() for row in trace.saved if row.observed_on != want]
            if wrong:
                misses.append(f"관찰 날짜 {wrong} ≠ {want.isoformat()}")
    for agent, want_kind in expect.suggestions.items():
        kinds = {s.kind for s in trace.suggestions if s.agent == agent}
        if want_kind == "none":
            if kinds:
                misses.append(f"{agent} 추천이 나왔다")
        elif not kinds:
            misses.append(f"{agent} 추천 없음")
        elif want_kind == "personalized":
            # 묶음은 개인화가 하나라도 있으면 개인화다(제품 bundle_summary) — 섞여도 통과
            if "personalized" not in kinds:
                misses.append(f"{agent} 추천 종류 {sorted(kinds)} ≠ personalized")
        elif want_kind != "any" and kinds != {want_kind}:
            misses.append(f"{agent} 추천 종류 {sorted(kinds)} ≠ {want_kind}")
    misses += [f"안내 {code} 없음" for code in expect.guidance if code not in trace.guidance]
    if expect.event_drafts_min is not None and trace.event_drafts < expect.event_drafts_min:
        misses.append(f"일정 초안 {trace.event_drafts}건 < {expect.event_drafts_min}")
    if expect.questions_max is not None and len(trace.questions) > expect.questions_max:
        misses.append(f"되묻기 {len(trace.questions)}개 > {expect.questions_max}")
    texts = trace.output_texts()
    misses += [f"'{w}' 가 출력에 있다" for w in expect.never_say if any(w in t for t in texts)]
    if expect.must_say_any and not any(w in t for w in expect.must_say_any for t in texts):
        misses.append(f"출력에 {list(expect.must_say_any)} 중 하나도 없다")
    if expect.tools is not None:
        misses += _check_tools(trace, expect.tools)
    return misses


def _check_tools(trace: Trace, want: ToolsExpect) -> list[str]:
    if trace.memory_tools is None:
        return ["Memory tool 기록이 없다 — Memory 가 돌지 않았다"]
    misses: list[str] = []
    for tool, minimum in want.must.items():
        done = sum(1 for call in trace.memory_tools if call.name == tool and call.success)
        if done < minimum:
            misses.append(f"{tool} 성공 {done}회 < {minimum}")
    for tool in want.must_not:
        if tool == MUTATING:
            used = [c.name for c in trace.memory_tools if c.name.startswith(MUTATING_PREFIXES)]
        else:
            used = [c.name for c in trace.memory_tools if c.name == tool]
        if used:
            misses.append(f"부르면 안 되는 tool: {sorted(set(used))}")
    return misses
