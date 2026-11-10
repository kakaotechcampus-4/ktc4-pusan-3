"""Growth 의 일반 정보 질문 (#574) — `common/doc_answer` 에 넘기는 행 읽기 · 문장 검사.

"보통 몇 살부터 혼자 양치해?" 같은 아이 일반 질문에 growth_doc 행을 근거로 답한다. 안전 규칙은
교육 후보와 같은 포트를 쓴다.

    행 거르기   질문 구간과 겹치는 `INFO_ROW_TYPES` 행 → 교육 후보 검사
                (`check_learning_candidate`)를 구간 하한 월령으로 — 음식 용어 · 등록 알레르기 이름 ·
                비식품 알레르기 대응표 · 위험 용어
    문장 검사   같은 교육 후보 검사 + 문장 가드(진단 · 강압 · 구매) + 의료 처치 · 증상 닫힘 + 수준
                판정 말(`find_level_judgement`). 공용 평가 표현은 뼈대가 이미 본다

호출 전에 `context.read_learning_safety` 가 안전 사전과 등록 알레르기를 run state 에 담는다.
"""

from collections.abc import Sequence

from app.agents.common.age import life_stage
from app.agents.common.doc_answer import DocAnswerSpec, DocLine
from app.agents.growth.context import GrowthContext
from app.agents.growth.guard import check_growth_text
from app.agents.growth.readouts import BLOCKED_SAFETY, READOUTS
from app.agents.growth.store.ports import INFO_ROW_TYPES, GrowthDocRow
from app.agents.growth.tools.closures import closure_key
from app.agents.growth.tools.safety import check_learning_candidate
from app.rules.evaluative import find_level_judgement

# 포트에서 받는 후보 수. 안전 검사에서 빠진 자리를 채울 수 있게 넉넉히 받고 뼈대가 5행으로 자른다
INFO_POOL = 200
TOPIC = "생활과 배움"


def info_spec(context: GrowthContext) -> DocAnswerSpec:
    state = context.state

    def _safe_months(asked_lo: int) -> int:
        """안전 검사에 넘기는 월령 — 질문 나이 하한과 이 아이 월령 중 어린 쪽.

        질문 나이만 보면 14개월 아이의 보호자가 "보통 4살은 …" 을 물을 때 36개월 미만에 막히는
        위험이 풀린다. 어린 쪽으로 보면 이 아이에게 막히는 것은 일반 자료에서도 거른다(§2).
        """
        if state.birth_date is None:
            return asked_lo
        return min(asked_lo, life_stage(state.birth_date, context.today).months)

    def _allowed(texts: Sequence[str], months: int) -> bool:
        if state.rules is None:
            return False  # 사전을 못 읽었으면 통과시키지 않는다
        verdict = check_learning_candidate(
            texts, months=months, entries=state.safety_entries, rules=state.rules
        )
        # 경고(warn)가 붙은 행도 뺀다 — 답변에는 경고 문구를 붙일 자리가 없다
        return verdict.allowed and not verdict.cautions

    async def rows(lo: int, hi: int, question: str) -> list[DocLine]:
        found = await context.ports.docs.search_range(
            min_month=lo, max_month=hi, row_types=INFO_ROW_TYPES, query="", limit=INFO_POOL
        )
        months = _safe_months(lo)
        return [_line(row) for row in found if _allowed(_texts(row), months)]

    def check(sentence: str, lo: int) -> str | None:
        if not _allowed((sentence,), _safe_months(lo)):
            return "safety"
        if not check_growth_text((sentence,)).allowed:
            return "guard"
        if closure_key((sentence,)) is not None:
            return "closure"
        if find_level_judgement(sentence):
            return "level"
        return None

    return DocAnswerSpec(
        agent="growth",
        doc_kind="growth_doc",
        topic=TOPIC,
        rows=rows,
        check=check,
        blocked=READOUTS.render(BLOCKED_SAFETY),
    )


def _texts(row: GrowthDocRow) -> tuple[str, ...]:
    return (row.title, row.body, *row.materials)


def _line(row: GrowthDocRow) -> DocLine:
    return DocLine(id=row.id, title=row.title, body=row.body, materials=tuple(row.materials))
