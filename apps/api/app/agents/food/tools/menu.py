"""기관 급식 조회·갱신·삭제 tool. 아이에게 위험한 식품 표시는 filter_food_safety(코드)를 재사용"""

from dataclasses import replace
from datetime import date, timedelta

from app.agents.common.age import life_stage
from app.agents.common.datetime_rules import DateParseError
from app.agents.food.amount_rules import amount_of, is_amount_text
from app.agents.food.context import FoodContext
from app.agents.food.meal_dates import in_edit_range, resolve_meal_date
from app.agents.food.menu_resolver import MenuResolver
from app.agents.food.readouts import (
    DAYCARE_ABSENT,
    DAYCARE_ALLERGY_CONFLICT,
    DAYCARE_AMBIGUOUS,
    DAYCARE_ASK_SUBSTITUTE,
    DAYCARE_NOT_FOUND,
    DAYCARE_OUT_OF_RANGE,
    DAYCARE_REPLACED_UNKNOWN,
    DAYCARE_SLOT_AMBIGUOUS,
    DAYCARE_UPDATED,
    READOUTS,
)
from app.agents.food.result import ErrorCode, Operation, ToolResult, fail, ok
from app.agents.food.schemas.daycare import DeleteDaycareMealArgs, UpdateDaycareMealArgs
from app.agents.food.schemas.menu import DaycareMenuArgs, MenuDay
from app.agents.food.store.ports import (
    DAYCARE_SLOT_ORDER,
    DaycareMealRow,
    DaycareSlot,
    MenuCatalogRow,
    SafetyEntry,
)
from app.agents.food.tools.safety import SafetyVerdict, filter_food_safety
from app.rules.allergen import ALLERGEN_NAMES
from app.rules.menu_key import normalize_menu_key

_DAY_OFFSET: dict[MenuDay, int] = {MenuDay.YESTERDAY: -1, MenuDay.TODAY: 0, MenuDay.TOMORROW: 1}


async def lookup_daycare_menu(context: FoodContext, args: DaycareMenuArgs) -> ToolResult:
    """기관 급식 메뉴를 날짜로 찾아 메뉴마다 알레르기 판정(ok · blocked · unchecked)을 붙인다.

    - `args.day` 라벨(어제 · 오늘 · 내일)은 코드가 날짜로 푼다. 미래를 보는 유일한 읽기 tool 이다.
    - 급식은 `menu_catalog` 만 본다. 레시피 재료로 판정하지 않는다 — 어린이집이 실제로 넣은 재료를
      표준 레시피가 "없음" 으로 덮으면 안 된다(own_table §1-1). 급식표에 인쇄된 알레르기 번호도
      읽지 않는다 — 기관이 아이의 알레르기를 관리하고 보호자에게 미리 알린다.
    - `build_gate` 가 읽어 둔 `context.state.safety` 로 `filter_food_safety(purpose="daycare")` 를
      건다. 포트를 다시 읽지 않는다. 메뉴 이름 · 재료가 걸리면 `blocked`, 재료를 모르거나 해석하지
      못한 메뉴는 통과가 아니라 `unchecked`("확인 못 함")다. 카탈로그에 없는 메뉴도 이름으로 보고
      아니면 `unchecked` 다.
    - `safety` 가 `None`(조회 실패)이면 판정하지 않고 전부 `unchecked`, `allergy_checked=False` 다.
    - 결과에는 메뉴 · 판정 · 끼니 · 출처 · 보호자 확인 여부만 싣는다. 걸린 알레르기 항목 이름과 사유
      는 싣지 않는다 — 모델 입력에 아이의 알레르기를 넣지 않고(Food 프롬프트와 같은 원칙), 사유를
      알려 주면 어휘 회피를 가르치게 된다. 질식 주의 문구도 싣지 않는다.
    - 그날 급식이 없으면 `meals=[]` 이다. 실패가 아니다.
    """
    ports = context.ports
    day = context.today + timedelta(days=_DAY_OFFSET[MenuDay(args.day)])
    rows = sorted(
        await ports.daycare.rows(child_id=context.child_id, date_from=day, date_to=day),
        key=lambda row: DAYCARE_SLOT_ORDER[row.meal_slot],
    )
    keys = tuple(dict.fromkeys(key for row in rows for key in row.menu_keys))
    entries = context.state.safety

    allergy = dict.fromkeys(keys, "unchecked")
    if keys and entries is not None:
        found = await ports.catalog.get_many(keys)
        birth_date = await ports.profile.birth_date(child_id=context.child_id)
        life = life_stage(birth_date, context.today)
        verdict = filter_food_safety(
            [found.get(key) or _unknown_menu(key, context) for key in keys],
            entries,
            life.stage,
            months=life.months,
            purpose="daycare",
        )
        allergy.update({row.menu_key: "ok" for row in verdict.passed})
        allergy.update({row.menu_key: "blocked" for row in verdict.blocked})

    return ok(
        "query",
        "daycare_menu",
        date=day.isoformat(),
        allergy_checked=entries is not None,
        meals=[
            {
                "slot": row.meal_slot,
                "origin": row.origin,
                "caregiver_checked": row.caregiver_checked,
                "menus": [{"menu_key": key, "allergy": allergy[key]} for key in row.menu_keys],
            }
            for row in rows
        ],
    )


def _unknown_menu(menu_key: str, context: FoodContext) -> MenuCatalogRow:
    """카탈로그에 없는 급식 메뉴. 이름만으로 거르고, 재료를 모르니 걸리지 않으면 unchecked 다."""
    return MenuCatalogRow(
        menu_key=menu_key,
        display_name=menu_key,
        source="manual",
        resolved=False,
        synced_at=context.now,
    )


_RESOURCE = "daycare_meal"
_SLOT_LABEL: dict[DaycareSlot, str] = {
    "lunch": "점심",
    "snack_am": "오전 간식",
    "snack_pm": "오후 간식",
}
# 모델에게 돌려주는 거절 설명. 모델이 쓴 값은 싣지 않는다 — message 는 로그로 흘러간다
_SPAN_MESSAGE = (
    "date_span · removed_spans · added_spans · amount_span 은 발화 원문에 있는 구간만 쓴다. "
    "원문에 없는 말을 지어내거나 바꿔 쓰지 않는다."
)


async def update_daycare_meal(context: FoodContext, args: UpdateDaycareMealArgs) -> ToolResult:
    """이미 있는 급식 행을 고친다. 없는 급식을 새로 만들지 않는다 (INSERT 는 Food 의 권한 밖이다).

    - 날짜 · 메뉴 · 양은 발화 원문(`state.utterance`)의 부분 문자열이어야 한다. 아니면 모델에게
      `INVALID_ARGS` 로 돌려준다. 날짜는 코드가 푼다(`resolve_meal_date`) — 풀 수 없거나 말하지
      않았으면 오늘로 추정하지 않고 되묻는다(`daycare.ambiguous`). 과거 30일 밖이면 고치지 않는다
      (`daycare.out_of_range`). 그날 행이 없으면 새로 만들지 않는다(`daycare.not_found`).
    - 끼니(`slot`)를 말하지 않았을 때 그날 행이 하나면 그 행을, 여럿이면 되묻는다
      (`daycare.slot_ambiguous`) — 엉뚱한 끼니를 고치면 영양 계산이 틀어진다.
    - 메뉴는 **집합 연산**이다: `menu_keys - removed + added`. 같은 갱신을 두 번 해도 결과가 같다.
      넣을 메뉴만 있고 뺄 메뉴가 없으면 되묻는다(`daycare.replaced_unknown`) — 전체 교체로 처리하면
      그날 먹은 밥 · 국까지 사라진다. 뺄 메뉴만 있거나 아무것도 못 고치면 되묻는다
      (`daycare.ask_substitute`). 뺄 메뉴가 그날 급식에 없으면 어느 것을 바꾼 건지 되묻는다.
      넣을 메뉴를 `MenuResolver` 로 찾아 둔다 — 못 찾아도(`unresolved`) 키는 저장하고 영양
      계산에서만 빠진다.
    - 양은 양 사전(`amount_terms.yaml`)에 있는 말일 때만 바꾼다. 사전에 없는 말로 알던 양을 덮지
      않는다. 메뉴를 고쳤으면 보호자가 확인한 행이다(`caregiver_checked`) — 양만 말했으면 아니다.
    - 쓰기는 `ports.daycare.update` 로 호출 하나가 짧은 트랜잭션 하나다. 화면 문구
      (`state.readouts`)는 **쓰기가 끝난 뒤에만** 채운다 — 쓰기가 실패하면 "바꿔 뒀어요" 가
      나가지 않는다.
    - 넣은 메뉴는 `filter_food_safety(purpose="daycare")` 로 다시 본다. 아이의 알레르기와 겹쳐도
      저장은 막지 않는다(보호자가 실제로 있었던 일을 말한 것이다) — 경고 문구를 함께 낸다. 겹치는
      항목 이름은 화면 문구에만 있고 모델에게 돌려주는 결과에는 없다. 안전 정보를 못 읽었으면
      (`None`) 다시 보지 못하고 `allergy_checked=False` 로 알린다.
    - 승인 게이트가 없다. 확인 모달이 뜨지 않는다(되돌릴 수 없는 2곳이 아니다).
    """
    state = context.state
    spans = (args.date_span, *args.removed_spans, *args.added_spans, args.amount_span or "")
    if _outside_utterance(state.utterance, spans):
        return fail("update", _RESOURCE, ErrorCode.INVALID_ARGS, _SPAN_MESSAGE)

    day, stop = _target_day(context, args.date_span)
    if day is None:
        return _stop(context, "update", stop)
    ports = context.ports
    rows = await ports.daycare.rows(child_id=context.child_id, date_from=day, date_to=day)
    row, stop = _pick_row(rows, args.slot)
    if row is None:
        return _stop(context, "update", stop)

    removed = _menu_keys(args.removed_spans)
    added_names = _clean(args.added_spans)
    added = _menu_keys(added_names)
    amount_given = is_amount_text(args.amount_span)
    if added and not removed:
        return _stop(context, "update", DAYCARE_REPLACED_UNKNOWN)
    if removed and not added:
        return _stop(context, "update", DAYCARE_ASK_SUBSTITUTE)
    present = set(row.menu_keys)
    if removed and not set(removed) & present and not set(added) <= present:
        # 뺄 메뉴가 그날 급식에 없다. 넣을 메뉴가 이미 다 들어 있으면 같은 갱신을 다시 한 것이라
        # 그대로 둔다(두 번 해도 같은 결과). 아니면 어느 메뉴를 바꾼 건지 되묻는다
        return _stop(context, "update", DAYCARE_REPLACED_UNKNOWN)
    if not removed and not amount_given:
        return _stop(context, "update", DAYCARE_ASK_SUBSTITUTE)

    updated = row
    changed: list[str] = []
    if added:
        # 넣을 메뉴를 찾아 카탈로그에 캐시한다. 못 찾아도 키는 저장한다
        await MenuResolver(
            catalog=ports.catalog, source=ports.menu_source, now=context.now
        ).resolve_many(added_names)
        menus = tuple(
            dict.fromkeys([key for key in row.menu_keys if key not in removed] + list(added))
        )
        # 메뉴를 고치면 보호자가 그날 급식을 확인한 것이다
        updated = replace(updated, menu_keys=menus, caregiver_checked=True)
        changed.append("menus")
    if amount_given:
        rule = amount_of(args.amount_span)
        updated = replace(updated, amount_factor=rule.factor, amount_known=rule.known)
        changed.append("amount")

    if updated != row:
        await ports.daycare.update(updated)

    readouts = ()
    if added:
        readouts = (
            READOUTS.render(
                DAYCARE_UPDATED,
                date=_date_label(day),
                slot=_SLOT_LABEL[row.meal_slot],
                menu=" · ".join(added_names),
            ),
        )
    entries = state.safety
    if entries is not None and added:
        labels = await _allergy_conflicts(context, added, entries)
        if labels:
            readouts += (READOUTS.render(DAYCARE_ALLERGY_CONFLICT, label=", ".join(labels)),)
    state.readouts = readouts
    return ok(
        "update",
        _RESOURCE,
        status="updated",
        changed=changed,
        date=day.isoformat(),
        slot=row.meal_slot,
        allergy_checked=entries is not None,
    )


async def delete_daycare_meal(context: FoodContext, args: DeleteDaycareMealArgs) -> ToolResult:
    """그날 급식 행을 지운다(결식 처리). 되돌리려면 OCR 재적재가 필요하다.

    - `slot` 이 없으면 그날 행 전부를, 있으면 그 끼니만 지운다. "어린이집 빠져" 는 하루를 통째로
      안 가는 것이라 끼니를 물을 이유가 없다.
    - 날짜는 발화 원문에 있는 구간을 코드가 푼다(`resolve_meal_date`). 말하지 않았거나 풀 수 없으면
      **반드시 되묻고** 지우지 않는다(`daycare.ambiguous`). 과거 30일 밖이면 지우지 않는다
      (`daycare.out_of_range`). 그날 행이 없으면 `daycare.not_found` 다. 미래 날짜도 지운다.
    - 쓰기가 끝난 뒤에만 "기관 식사 없는 걸로 해 뒀어요"(`daycare.absent`)를 `state.readouts` 에
      채운다.
    - 승인 게이트가 없다.
    """
    state = context.state
    if _outside_utterance(state.utterance, (args.date_span,)):
        return fail("delete", _RESOURCE, ErrorCode.INVALID_ARGS, _SPAN_MESSAGE)

    day, stop = _target_day(context, args.date_span)
    if day is None:
        return _stop(context, "delete", stop)
    ports = context.ports
    rows = await ports.daycare.rows(child_id=context.child_id, date_from=day, date_to=day)
    targets = [row for row in rows if args.slot is None or row.meal_slot == args.slot]
    if not targets:
        return _stop(context, "delete", DAYCARE_NOT_FOUND)

    await ports.daycare.delete(child_id=context.child_id, row_ids=tuple(row.id for row in targets))
    state.readouts = (READOUTS.render(DAYCARE_ABSENT, date=_date_label(day)),)
    return ok("delete", _RESOURCE, status="deleted", date=day.isoformat(), deleted=len(targets))


def _outside_utterance(utterance: str, spans: tuple[str, ...]) -> bool:
    """원문에 없는 구간이 하나라도 있나. 빈 구간은 없는 것으로 본다(되묻기로 간다)."""
    return any(span.strip() and span.strip() not in utterance for span in spans)


def _clean(spans: list[str]) -> list[str]:
    return [span.strip() for span in spans if span.strip()]


def _menu_keys(spans: list[str]) -> list[str]:
    """구간 → 정규화한 menu_key. 정규화하면 비는 것은 뺀다. 순서를 지키고 겹치지 않는다."""
    return list(dict.fromkeys(key for span in spans if (key := normalize_menu_key(span))))


def _date_label(target: date) -> str:
    return f"{target.month}월 {target.day}일"


def _target_day(context: FoodContext, span: str) -> tuple[date | None, str]:
    """날짜 구간 → (날짜, "") 또는 (None, 멈출 문구 키). 추측하지 않는다."""
    try:
        target = resolve_meal_date(span, today=context.today)
    except DateParseError:
        return None, DAYCARE_AMBIGUOUS
    if not in_edit_range(target, today=context.today):
        return None, DAYCARE_OUT_OF_RANGE
    return target, ""


def _pick_row(
    rows: list[DaycareMealRow], slot: DaycareSlot | None
) -> tuple[DaycareMealRow | None, str]:
    """고칠 행 하나. 없으면 (None, 멈출 문구 키)."""
    if slot is not None:
        found = next((row for row in rows if row.meal_slot == slot), None)
        return (found, "") if found else (None, DAYCARE_NOT_FOUND)
    if not rows:
        return None, DAYCARE_NOT_FOUND
    if len(rows) > 1:
        return None, DAYCARE_SLOT_AMBIGUOUS
    return rows[0], ""


def _stop(context: FoodContext, operation: Operation, key: str) -> ToolResult:
    """되묻거나 못 고치는 경우. 아무것도 쓰지 않고 코드 문구만 낸다.

    모델이 다시 부를 일이 아니다 — 성공으로 끝내고 되묻기는 보호자에게 간다.
    """
    context.state.readouts = (READOUTS.render(key),)
    return ok(operation, _RESOURCE, status="stopped")


async def _allergy_conflicts(
    context: FoodContext, menu_keys: list[str], entries: tuple[SafetyEntry, ...]
) -> tuple[str, ...]:
    """넣은 메뉴 중 아이의 알레르기와 겹치는 항목 이름. 겹침만 알리고 기록은 막지 않는다."""
    ports = context.ports
    found = await ports.catalog.get_many(tuple(menu_keys))
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    life = life_stage(birth_date, context.today)
    verdict = filter_food_safety(
        [found.get(key) or _unknown_menu(key, context) for key in menu_keys],
        entries,
        life.stage,
        months=life.months,
        purpose="daycare",
    )
    return _allergy_labels(verdict, entries)


def _allergy_labels(verdict: SafetyVerdict, entries: tuple[SafetyEntry, ...]) -> tuple[str, ...]:
    """걸린 키 중 알레르기만 사람이 읽는 이름으로. 연령 규칙(꿀 · 팝콘)은 알레르기가 아니다."""
    registered = {entry.label for entry in entries}
    labels: dict[str, None] = {}
    for hits in verdict.hits.values():
        for key in hits:
            name = (
                ALLERGEN_NAMES.get(int(key))
                if key.isdigit()
                else key
                if key in registered
                else None
            )
            if name:
                labels.setdefault(name, None)
    return tuple(labels)
