"""케이스가 적은 기대값을 판정한다. 불변식(§2)과 달리 케이스마다 다르다.

무거운 판정은 불변식이 맡고, 여기는 가볍게 — 라우팅 포함/제외 · 저장 도메인 · 추천 종류 · 안내 코드.
"""

from datetime import date, timedelta

from tests.eval.suite.schema import Expect
from tests.eval.suite.trace import Trace

# 되묻기를 하면 저절로 빠지는 미달 — 기대한 결과(추천 · 일정 초안 · 라우팅 · 삭제 제안)가
# "안 나온" 것뿐이다. 되묻기로 끝난 run 에서 미달이 전부 이 종류면 "시험 못 함" (verdict.judge)
ASKED_INSTEAD = frozenset({"no_suggestion", "no_draft", "no_route", "no_proposal"})


class Miss(str):
    """기대 미달 한 줄. 글자는 그대로 결과 jsonl · md 에 가고, `kind` 는 판정만 본다.

    str 을 상속해서 글자 비교 · json 직렬화는 그대로다.
    """

    kind: str

    def __new__(cls, text: str, kind: str = "other") -> "Miss":
        miss = super().__new__(cls, text)
        miss.kind = kind
        return miss


def check_expect(trace: Trace, expect: Expect, *, today: date) -> list[Miss]:
    misses: list[Miss] = []
    if expect.route is not None:
        route = expect.route
        if route.intent is not None and trace.intent != route.intent:
            misses.append(Miss(f"의도 {trace.intent} ≠ {route.intent}", "no_route"))
        misses += [
            Miss(f"route 에 {a} 없음", "no_route") for a in route.include if a not in trace.route
        ]
        misses += [Miss(f"route 에 {a} 있음") for a in route.exclude if a in trace.route]
    if expect.saved is not None:
        saved = expect.saved
        count = len(trace.saved)
        if saved.child_rows is not None and count != saved.child_rows:
            misses.append(Miss(f"저장 {count}행 ≠ {saved.child_rows}"))
        if saved.min_rows is not None and count < saved.min_rows:
            misses.append(Miss(f"저장 {count}행 < {saved.min_rows}"))
        for domain in saved.domains:
            if not any(row.domain == domain for row in trace.saved):
                misses.append(Miss(f"{domain} 저장 없음"))
        if saved.days_ago is not None:
            want = today - timedelta(days=saved.days_ago)
            wrong = [row.observed_on.isoformat() for row in trace.saved if row.observed_on != want]
            if wrong:
                misses.append(Miss(f"관찰 날짜 {wrong} ≠ {want.isoformat()}"))
    for agent, want_kind in expect.suggestions.items():
        kinds = {s.kind for s in trace.suggestions if s.agent == agent}
        if want_kind == "none":
            if kinds:
                misses.append(Miss(f"{agent} 추천이 나왔다"))
        elif not kinds:
            misses.append(Miss(f"{agent} 추천 없음", "no_suggestion"))
        elif want_kind == "personalized":
            # 묶음은 개인화가 하나라도 있으면 개인화다(제품 bundle_summary) — 섞여도 통과
            if "personalized" not in kinds:
                misses.append(Miss(f"{agent} 추천 종류 {sorted(kinds)} ≠ personalized"))
        elif want_kind != "any" and kinds != {want_kind}:
            misses.append(Miss(f"{agent} 추천 종류 {sorted(kinds)} ≠ {want_kind}"))
    misses += [Miss(f"안내 {code} 없음") for code in expect.guidance if code not in trace.guidance]
    if expect.event_drafts_min is not None and trace.event_drafts < expect.event_drafts_min:
        misses.append(
            Miss(f"일정 초안 {trace.event_drafts}건 < {expect.event_drafts_min}", "no_draft")
        )
    if expect.questions_max is not None and len(trace.questions) > expect.questions_max:
        misses.append(Miss(f"되묻기 {len(trace.questions)}개 > {expect.questions_max}"))
    texts = trace.output_texts()
    misses += [
        Miss(f"'{w}' 가 출력에 있다") for w in expect.never_say if any(w in t for t in texts)
    ]
    if expect.must_say_any and not any(w in t for w in expect.must_say_any for t in texts):
        misses.append(Miss(f"출력에 {list(expect.must_say_any)} 중 하나도 없다"))
    return misses
