"""Food run() 라이브 eval — 영양소 분석이 구간을 올바른 어조로 말하는가 (#321 파트 2).

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/food/test_nutrient_run.py -m live

report_nutrient_analysis 의 출력 검증(Task 9 · #357 리뷰의 문장 규칙)은 수치 · 진단 · 처방 ·
아이 몸 상태 표현과 basis_keys 밖 항목을 코드로 막지만, "구간을 어떤 어조로 전달하는가" 는 검사
대상이 아니다 — 사람이 직접 읽고 본다. 모델이 출력 tool 을 못 끝내면 run() 이 코드 문장 틀로
대신 내므로(authored_by="code"), 그 경우도 같은 규칙을 통과하는지 같이 본다.

run() 전체(공통 tool 루프, Task 13)를 "기록 충분" · "기록 부족" 두 상황으로 세 번씩 돌려
결과를 남긴다.

데이터는 저단백(protein low) · 적정 칼슘(calcium ok) · 과다 나트륨(sodium high) 세 메뉴를
매일 먹인 것으로 꾸며, 모델이 compare_diet_balance 를 불렀을 때 세 구간이 실제로 나오게 한다.

사람이 보는 것 (test_nutrient_run_result.txt 의 [답변]):
- low 를 "적은 편 · 낮은 편" 으로 말하는가 (단정하지 않는가 — "부족" · "모자라" 는 규칙이 거절한다)
- ok 를 "잘 챙기고 있다" 로 말하는가
- high 를 칭찬으로 쓰지 않는가
- 수치(%·g·mg·kcal)가 0건인가 — report_nutrient_analysis 가 이미 코드로 막지만 end-to-end 로
  다시 본다

자동으로 보는 것 하나 — 기록 충분 3번 중 적어도 한 번은 **모델이 쓴 문장**(`authored_by="model"`)이
나가야 한다. 규칙이 엄격해져 모델 문장이 거의 다 거절되면 run() 이 조용히 코드 문장으로 바꿔서
위 검사가 계속 초록이기 때문이다 — 모델 경로가 죽은 것을 이 단언이 잡는다. [답변]만 보면 두
경로를 구분할 수 없다(코드 문장도 같은 모양이다).

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").

`test_기록_부족이면_insufficient_안내가_빈_화면_대신_뜬다` 는 final-review Critical #1 의 재발
방지용이다 — `_readouts_from` 이 findings 가 비어도 `verdict.insufficient` 를 안 보고 그냥
`readouts=()` 를 내던 버그가 있었다(agent.py). 코드 상수 문구라 단위 테스트
(test_food_agent.py)로도 잡히지만, 모델이 실제로 그 경로(findings 비우기)까지 이어가는지는
라이브로 다시 본다. 모델이 못 끝낸 run(no_tool_call · max_steps)은 failed 가 아니라 같은 문구를
코드가 내므로 status 는 늘 ok 다(#357 리뷰).
"""

import re
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.agents.common.schemas.task import DomainTask
from app.agents.food.agent import run
from app.agents.food.context import FoodContext
from app.agents.food.nutrient_wording import wording_problem
from app.agents.food.readouts import READOUTS
from app.agents.food.store import (
    DaycareMealRow,
    InMemoryDaycareMeals,
    InMemoryMenuCatalog,
    InMemoryProfile,
    MenuCatalogRow,
    in_memory_ports,
)
from tests.eval.agents.food.generate import require_food_model

pytestmark = pytest.mark.live

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=KST)
BIRTH_DATE = date(2022, 10, 2)  # NOW 기준 48개월 — 3-5y 섭취기준, 영아·질식 규칙과 안 겹친다
TODAY = NOW.date()
RUNS_PER_CASE = 3

# 숫자 + 단위(% · g · mg · kcal) 또는 그 한글 표현. report_nutrient_analysis(Task 9)가 이미
# 코드로 막지만, run() 전체가 실제로 그렇게 끝나는지 end-to-end 로 다시 본다
_NUMERIC_UNIT = re.compile(r"\d+(\.\d+)?\s*(%|g|mg|kcal)|퍼센트|칼로리")

_LOW_PROTEIN = "저단백식품"  # protein %kcal = (2*4)/100*100 = 8% — AMDR(10~20%) 미만 → low
_OK_CALCIUM = "칼슘식품"  # 1일 약 563mg — EAR(450mg, 3-5y) 이상 → ok
_HIGH_SODIUM = "나트륨식품"  # 1일 약 2,816mg — CDRR(1,500mg, 3-5y) 초과 → high


def _row(menu_key: str, **nutrients: float) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=menu_key,
        display_name=menu_key,
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=2000.0,
        nutrients=dict(nutrients),
        food_groups=frozenset(),
    )


def _daily_rows(days: int) -> list[DaycareMealRow]:
    """최근 `days`일(오늘 이전), 하루 세 메뉴. 14일이면 EAR 항목(칼슘)의 히스테리시스 창도
    채운다."""
    rows: list[DaycareMealRow] = []
    for offset in range(days):
        day = (TODAY - timedelta(days=1)) - timedelta(days=offset)
        for menu_key in (_LOW_PROTEIN, _OK_CALCIUM, _HIGH_SODIUM):
            rows.append(
                DaycareMealRow(
                    id=uuid4(),
                    child_id=CHILD,
                    serve_date=day,
                    meal_slot="lunch",
                    menu_keys=(menu_key,),
                    amount_factor=1.0,
                    amount_known=True,
                )
            )
    return rows


def _context(days: int) -> FoodContext:
    catalog = InMemoryMenuCatalog(
        [
            _row(_LOW_PROTEIN, protein=2.0, energy=100.0),
            _row(_OK_CALCIUM, calcium=100.0),
            _row(_HIGH_SODIUM, sodium=500.0),
        ]
    )
    ports = in_memory_ports(
        catalog=catalog,
        daycare=InMemoryDaycareMeals(_daily_rows(days)),
        profile=InMemoryProfile({CHILD: BIRTH_DATE}),
    )
    return FoodContext(child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=ports)


def _task() -> DomainTask:
    return DomainTask(
        run_id="run-1",
        agent="food",
        task_type="nutrient_analysis",
        request_texts=("요즘 아이 영양 상태가 어떤지 봐줘",),
    )


@pytest.mark.parametrize(
    ("case", "days"),
    [("기록_충분", 14), ("기록_부족", 2)],
    ids=["기록_충분", "기록_부족"],
)
async def test_영양소_분석_어조(case: str, days: int, expect) -> None:
    criteria = [
        "low 는 단정하지 않고 '적은 편' · '낮은 편' 식으로 말한다 — '부족' 은 문장 규칙이 거절한다",
        "ok 는 '잘 챙기고 있다' 처럼 안심시키는 말로 전한다",
        "high 를 칭찬으로 쓰지 않는다",
        "수치(%·g·mg·kcal)가 0건이다 — report_nutrient_analysis 의 검증을 end-to-end 로 다시 본다",
    ]
    if case == "기록_충분":
        criteria.append(
            "3번 중 적어도 한 번은 모델이 쓴 문장이 나간다(authored_by=model) — "
            "전부 코드 문장이면 모델 경로가 죽은 것이다"
        )
    expect(*criteria)
    require_food_model()

    authored: list[str] = []
    for attempt in range(RUNS_PER_CASE):
        result = await run(_task(), _context(days))
        bodies = " / ".join(readout.body for readout in result.readouts)
        assert not _NUMERIC_UNIT.search(bodies), (
            f"[{case} #{attempt}] 수치 표현이 남았다: {bodies!r}"
        )
        # 모델 문장이든 코드 문장 틀이든 같은 문장 규칙을 통과해야 한다 (#357 리뷰 — 한 화면에
        # 기준이 둘이면 안 된다). nutrient.insufficient 같은 안내 문구(kind="nutrient")는 건수
        # 안내라 영양 문장이 아니므로 영양 문장(nutrient_analysis)만 본다
        for readout in result.readouts:
            if readout.kind != "nutrient_analysis":
                continue
            authored.append(readout.authored_by)
            for line in readout.body.splitlines():
                assert wording_problem(line) is None, (
                    f"[{case} #{attempt} {readout.authored_by}] 문장 규칙에 걸린다: {line!r}"
                )

    if case == "기록_충분":
        counts = f"model={authored.count('model')} code={authored.count('code')}"
        assert "model" in authored, (
            f"[{case}] 모델이 쓴 문장이 한 번도 안 나왔다({counts}) — 문장 규칙이 모델 문장을 거의 "
            "다 거절하거나 모델이 출력 tool 을 못 끝내고 있다. [답변]만으로는 구분되지 않는다"
        )


_INSUFFICIENT_DAYS = 2  # _daily_rows(2) = 3 메뉴 × 2일 = 6행 — MIN_RECORDS(10) 미만
_INSUFFICIENT_RECORDS = 3 * _INSUFFICIENT_DAYS


async def test_기록_부족이면_insufficient_안내가_빈_화면_대신_뜬다(expect) -> None:
    expect(
        "기록 부족이면 status 는 늘 ok 이고 readout 이 비지 않는다 — "
        "nutrient.insufficient 코드 문구가 그대로 뜬다(final-review Critical #1 재발 방지)",
        "모델이 출력 tool 을 못 끝내도(no_tool_call · max_steps) failed 가 아니라 같은 문구를 "
        "코드가 낸다 — 라이브 eval 에서 기록 부족이 6번 중 2번 failed 였던 것이 사라지는지 본다",
    )
    require_food_model()

    expected = READOUTS.get("nutrient.insufficient").template.format(recorded=_INSUFFICIENT_RECORDS)
    for attempt in range(RUNS_PER_CASE):
        result = await run(_task(), _context(_INSUFFICIENT_DAYS))
        assert result.status == "ok", f"[#{attempt}] status={result.status} — failed 가 남았다"
        assert result.readouts, (
            f"[#{attempt}] status=ok 인데 readout 이 비었다 — 화면에 아무것도 안 뜬다"
        )
        assert result.readouts[0].body == expected, (
            f"[#{attempt}] insufficient 문구가 기대와 다르다: {result.readouts[0].body!r}"
        )
