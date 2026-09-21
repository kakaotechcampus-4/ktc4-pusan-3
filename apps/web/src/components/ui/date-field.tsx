"use client";

import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Calendar as CalendarIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { DayPicker } from "react-day-picker";
import { ko } from "react-day-picker/locale";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { cn } from "@/lib/cn";
import { formatDateWithYear, toISODate } from "@/lib/format";

/**
 * 날짜 입력. 값은 `YYYY-MM-DD` 문자열이고, 달력은 바텀시트로 연다.
 *
 * **왜 `<input type="date">` 를 안 쓰나** — 브라우저·OS 마다 생김새가 완전히 달라서
 * 토큰으로 맞출 수가 없다. 디자인 시스템 §7 이 정한 입력 사양(`field` 높이 · `line-strong` 1px ·
 * body 16px)을 지킬 방법이 없는 유일한 입력이었다.
 *
 * **왜 팝오버가 아니라 바텀시트인가** — 이 화면은 대부분 웹뷰다(§9). 좁은 폭에서 달력을
 * 띄우면 화면 밖으로 넘치거나 입력을 가린다. 시트는 이미 있는 패턴이고 포커스 트랩·ESC 를
 * 브라우저가 준다.
 *
 * 🚨 **여기서 나이를 계산하지 않는다.** 미래 날짜를 막는 것은 "표시" 가 아니라 입력 제약이라
 *    허용된 예외다 (apps/web/CLAUDE.md §4). 나이 문구는 서버가 만든다.
 *
 * ## 머리줄은 우리가 그린다 (`hideNavigation`)
 *
 * 🚨 **예전에는 `captionLayout="dropdown"` 이었고, 그건 네이티브 `<select>` 두 개였다.**
 *    이 저장소는 네이티브 `<select>` 를 쓰지 않기로 했고(`components/ui/select.tsx` 머리말 ·
 *    apps/web/CLAUDE.md §3), 그래서 고르기 상자도 직접 만들었다. 달력 안에만 예외를 둘 이유가
 *    없다 — 닫혀 있을 때 말고는 생김새를 우리가 못 정하고, OS 마다 다른 휠이 뜬다.
 *
 *    대신 라이브러리 머리줄을 끄고(`hideNavigation` + `caption_label: "sr-only"`)
 *    **화살표 · 연월 버튼을 우리가 그린다.** 연월 버튼을 누르면 시트 본문이 날짜 그리드에서
 *    **연도 그리드 → 달 그리드**로 바뀐다. 생일은 몇 년 전이라 화살표만 두면 48번을 눌러야 하고,
 *    드롭다운은 위 이유로 못 쓴다 — 같은 면에 펼치는 그리드가 남는 답이다.
 *
 * 🚨 **라이브러리가 맡는 것은 그대로 둔다** — 그리드 ARIA · 방향키 · 월 경계 · 로케일 넷
 *    (디자인 시스템 §7). 우리가 가져온 것은 머리줄 하나뿐이고, `month` 를 통제 상태로 올려
 *    그 넷과 어긋나지 않게 `onMonthChange` 로 되받는다.
 */
export function DateField({
  label,
  hint,
  value,
  onChange,
  error,
  /** 고를 수 있는 가장 이른 날. 생일이면 "너무 오래전" 을 막는다. */
  fromDate,
  /** 고를 수 있는 가장 늦은 날. 생일이면 오늘. */
  toDate,
}: {
  label: string;
  hint?: string;
  /** `YYYY-MM-DD` 또는 빈 문자열. */
  value: string;
  onChange: (value: string) => void;
  error?: string | null;
  fromDate: Date;
  toDate: Date;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Date | undefined>(undefined);

  const selected = parseDate(value);
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;

  return (
    <div className="flex flex-col gap-1.5">
      <span id={`${id}-label`} className="text-label text-ink-muted">
        {label}
      </span>
      {hint ? (
        <p id={hintId} className="text-caption text-ink-subtle">
          {hint}
        </p>
      ) : null}

      <button
        type="button"
        // 값이 화면 글자로만 있으면 스크린리더가 라벨과 값을 못 잇는다.
        aria-labelledby={`${id}-label`}
        // 🚨 `aria-invalid` 는 button role 에서 지원되지 않는다. 오류는 describedby 로
        //    이어진 문구가 읽어 준다 (`<input>` 이 아니라 버튼으로 여는 컨트롤이라 그렇다).
        aria-describedby={cn(hintId, errorId) || undefined}
        onClick={() => {
          setDraft(selected);
          setOpen(true);
        }}
        className={cn(
          "rounded-field bg-surface ease-standard min-h-field flex w-full items-center justify-between gap-2 border px-3.5 py-2 text-left transition-colors duration-120",
          error ? "border-danger" : "border-line-strong hover:border-ink-subtle",
        )}
      >
        <span className={cn("text-body", selected ? "text-ink" : "text-ink-subtle")}>
          {value ? formatDateWithYear(value) : "생일을 골라주세요"}
        </span>
        <CalendarIcon
          aria-hidden
          size={ICON_SIZE.md}
          strokeWidth={ICON_STROKE}
          // 글자를 키워 값이 두 줄이 돼도 아이콘이 찌그러지지 않게.
          className="text-ink-subtle shrink-0"
        />
      </button>

      {error ? (
        <p id={errorId} className="text-caption text-danger-ink">
          {error}
        </p>
      ) : null}

      <BottomSheet
        open={open}
        onClose={() => setOpen(false)}
        title={label}
        description="연월을 누르면 연도와 달을 바로 고를 수 있어요."
        footer={
          <div className="flex gap-2">
            <Button variant="secondary" className="flex-1" onClick={() => setOpen(false)}>
              취소
            </Button>
            <Button
              className="flex-1"
              disabled={!draft}
              onClick={() => {
                if (draft) onChange(toISODate(draft));
                setOpen(false);
              }}
            >
              고르기
            </Button>
          </div>
        }
      >
        {/* 🚨 시트가 닫히면 달력을 통째로 버린다 (`open &&`). 다음에 열 때 **고른 값의 달**에서
            시작해야 하는데, 살려 두면 지난번에 넘겨 둔 달과 펼쳐 둔 연도 그리드가 그대로 남는다. */}
        {open ? (
          <Calendar
            selected={draft}
            onSelect={setDraft}
            fromDate={fromDate}
            toDate={toDate}
            initialMonth={selected ?? toDate}
          />
        ) : null}
      </BottomSheet>
    </div>
  );
}

/* ── 달력 ─────────────────────────────────────────────────────────────── */

/** 시트 본문에 서는 면. 날짜 · 연도 · 달 셋이 같은 자리를 번갈아 쓴다. */
type CalendarView = "days" | "years" | "months";

function Calendar({
  selected,
  onSelect,
  fromDate,
  toDate,
  initialMonth,
}: {
  selected: Date | undefined;
  onSelect: (date: Date | undefined) => void;
  fromDate: Date;
  toDate: Date;
  initialMonth: Date;
}) {
  /**
   * 🚨 **`month` 를 통제 상태로 올린다.** 연도·달 그리드에서 고른 값이 날짜 그리드에 반영되려면
   *    화면이 달을 들고 있어야 한다. 라이브러리의 화살표·방향키·월 경계는 그대로 살아 있고,
   *    그쪽에서 달이 바뀌면 `onMonthChange` 로 되받아 한 곳에서만 관리한다.
   */
  const [month, setMonth] = useState(() => clampMonth(initialMonth, fromDate, toDate));
  const [view, setView] = useState<CalendarView>("days");

  const captionRef = useRef<HTMLButtonElement>(null);

  const year = month.getFullYear();
  const monthIndex = month.getMonth();

  const canGoBack = startOfMonth(month) > startOfMonth(fromDate);
  const canGoForward = startOfMonth(month) < startOfMonth(toDate);

  function goToMonth(next: Date) {
    setMonth(clampMonth(next, fromDate, toDate));
  }

  return (
    <div className="flex flex-col gap-3">
      {/* ── 머리줄 ──────────────────────────────────────────────────────
          🚨 화살표는 **날짜 그리드에서만** 쓸 수 있다. 연도 그리드가 펼쳐진 동안 뒤에서
             달이 넘어가면 무엇을 고르는 중인지가 흐려진다 — 그래서 비활성이 아니라 **감춘다**
             (비활성으로 두면 눌러도 안 되는 버튼이 둘 늘어난다). 자리는 `min-h-touch` 가
             지키므로 감춰도 머리줄 높이가 흔들리지 않는다. */}
      <div className="min-h-touch flex items-center justify-between gap-1">
        <NavButton
          label="이전 달"
          hidden={view !== "days"}
          disabled={!canGoBack}
          onClick={() => goToMonth(addMonths(month, -1))}
        >
          <ChevronLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </NavButton>

        {/* 🚨 **연월이 버튼이라는 것이 보여야 한다.** 쉐브론 하나로 말하고, 펼침 상태는
            `aria-expanded` 가 함께 진다. 🚨 쉐브론을 **회전시키지 않는다** — 상호작용
            전환은 색만 바꾸고 크기·위치는 건드리지 않는다는 규칙(디자인 시스템 §8)이라,
            방향은 아이콘을 갈아 끼워 말한다 (`Select` 와 같은 처리). */}
        <button
          ref={captionRef}
          type="button"
          aria-expanded={view !== "days"}
          onClick={() => setView((v) => (v === "days" ? "years" : "days"))}
          className="text-section text-ink min-h-touch ease-standard hover:bg-surface-muted active:bg-surface-muted rounded-field flex items-center gap-1.5 px-3 transition-colors duration-120"
        >
          {/* 🚨 그리드가 펼쳐져도 **지금 어느 해에 있는지**는 계속 보여준다. "연도 고르기"
              같은 안내 문구로 바꾸면 고르는 중에 기준점이 사라진다. */}
          {view === "days" ? `${year}년 ${monthIndex + 1}월` : `${year}년`}
          {view === "days" ? (
            <ChevronDown aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
          ) : (
            <ChevronUp aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
          )}
        </button>

        <NavButton
          label="다음 달"
          hidden={view !== "days"}
          disabled={!canGoForward}
          onClick={() => goToMonth(addMonths(month, 1))}
        >
          <ChevronRight aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </NavButton>
      </div>

      {/* 🚨 **세 면의 높이를 맞춘다.** 셋이 같은 자리를 번갈아 쓰는데 그냥 두면 시트가
          448 ↔ 548px 로 오르내려서, 연도를 고를 때마다 시트 윗변이 100px 씩 뛴다.
          날짜 그리드(제일 자주 보는 면)에 바닥을 맞춰 두면 달 그리드가 쪼그라들지 않는다.
          🚨 `px` 가 아니라 `rem` 이다 — 글자를 키우면 그리드가 같이 커지는데 바닥만
          제자리에 남으면 소용이 없다 (09 표식 크기와 같은 이유 · 디자인 시스템 §7). */}
      <div className="flex min-h-[19rem] flex-col">
        {view === "years" ? (
          <YearGrid
            year={year}
            fromYear={fromDate.getFullYear()}
            toYear={toDate.getFullYear()}
            onPick={(nextYear) => {
              goToMonth(new Date(nextYear, monthIndex, 1));
              setView("months");
            }}
          />
        ) : view === "months" ? (
          <MonthGrid
            year={year}
            monthIndex={monthIndex}
            fromDate={fromDate}
            toDate={toDate}
            onPick={(nextMonth) => {
              goToMonth(new Date(year, nextMonth, 1));
              setView("days");
              // 🚨 포커스를 연월 버튼으로 되돌린다. 안 되돌리면 그리드가 사라지는 순간
              //    포커스가 `<body>` 로 떨어져 키보드 사용자가 자리를 잃는다
              //    (`Select` 가 같은 사고를 낸 적이 있다 · 09 달력).
              captionRef.current?.focus();
            }}
          />
        ) : (
          <DayPicker
            mode="single"
            locale={ko}
            selected={selected}
            onSelect={onSelect}
            month={month}
            onMonthChange={goToMonth}
            startMonth={fromDate}
            endMonth={toDate}
            disabled={{ before: fromDate, after: toDate }}
            // 🚨 머리줄은 위에서 우리가 그린다 — 라이브러리 것은 끈다.
            hideNavigation
            captionLayout="label"
            classNames={CALENDAR_CLASSES}
          />
        )}
      </div>
    </div>
  );
}

/** 머리줄 화살표. 🚨 `h-touch aspect-square` 는 글자가 없는 정사각 버튼에 허용된 조합이다. */
function NavButton({
  label,
  hidden,
  disabled,
  onClick,
  children,
}: {
  label: string;
  hidden: boolean;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      // 🚨 감추는 것은 `invisible`(= `visibility: hidden`) 이다. `opacity-0` 로 감추면
      //    **보이지 않는데 탭으로 닿고 눌리는** 버튼이 되는데, visibility 는 접근성 트리와
      //    포커스에서 함께 빼 준다. `disabled` 는 그 위의 이중 잠금이다.
      disabled={disabled || hidden}
      onClick={onClick}
      className={cn(
        "text-ink-muted h-touch ease-standard hover:bg-surface-muted active:bg-surface-muted flex aspect-square shrink-0 items-center justify-center rounded-full transition-colors duration-120",
        "disabled:text-ink-subtle disabled:hover:bg-transparent",
        hidden && "invisible",
      )}
    >
      {children}
    </button>
  );
}

/**
 * 연도 그리드. 🚨 **최근 연도가 위**다 — 이 입력이 받는 것은 아이 생일과 잰 날이라
 * 찾는 값이 거의 항상 최근 몇 해 안에 있다. 오래된 쪽부터 쌓으면 매번 끝까지 스크롤해야 한다.
 */
function YearGrid({
  year,
  fromYear,
  toYear,
  onPick,
}: {
  year: number;
  fromYear: number;
  toYear: number;
  onPick: (year: number) => void;
}) {
  const years: number[] = [];
  for (let y = toYear; y >= fromYear; y -= 1) years.push(y);

  return (
    <PickerGrid columns={4} label="연도 고르기">
      {years.map((y) => (
        <PickerCell key={y} selected={y === year} onClick={() => onPick(y)}>
          {y}
        </PickerCell>
      ))}
    </PickerGrid>
  );
}

/** 달 그리드. 고를 수 있는 범위 밖의 달은 잠근다 — 눌러서 아무 일도 안 나는 칸을 만들지 않는다. */
function MonthGrid({
  year,
  monthIndex,
  fromDate,
  toDate,
  onPick,
}: {
  year: number;
  monthIndex: number;
  fromDate: Date;
  toDate: Date;
  onPick: (monthIndex: number) => void;
}) {
  const first = startOfMonth(fromDate);
  const last = startOfMonth(toDate);

  return (
    <PickerGrid columns={3} label={`${year}년, 달 고르기`}>
      {Array.from({ length: 12 }, (_, m) => {
        const candidate = new Date(year, m, 1);
        const outOfRange = candidate < first || candidate > last;

        return (
          <PickerCell
            key={m}
            selected={m === monthIndex}
            disabled={outOfRange}
            onClick={() => onPick(m)}
          >
            {m + 1}월
          </PickerCell>
        );
      })}
    </PickerGrid>
  );
}

/* ── 그리드 조각 ──────────────────────────────────────────────────────── */

/**
 * 🚨 **"2열 그리드를 만들지 않는다"(§9)의 대상이 아니다.** 그 규칙은 **화면 레이아웃**이
 *    1열이어야 한다는 것이고, 달력 자체가 이미 7열 그리드다. 여기 그리드는 그 달력 안에서
 *    같은 자리를 쓰는 다른 면이다.
 *
 * 🚨 열 수를 `grid-cols-${n}` 으로 만들지 않는다 — Tailwind 는 소스의 **문자열**을 훑어서
 *    클래스를 만들기 때문에 조립한 이름은 생성되지 않는다 (`ChildNav` 와 같은 주의).
 */
const COLUMNS = {
  3: "grid-cols-3",
  4: "grid-cols-4",
} as const;

function PickerGrid({
  columns,
  label,
  children,
}: {
  columns: keyof typeof COLUMNS;
  label: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  /**
   * 🚨 **열 때 고른 칸으로 포커스가 들어간다** (`Select` 와 같은 처리). 연도를 고르면 그
   *    그리드가 통째로 달 그리드로 바뀌는데, 포커스를 안 옮기면 방금 누른 버튼이 사라지면서
   *    포커스가 `<body>` 로 떨어진다 — 키보드 사용자가 자리를 잃는 그 사고다.
   *    두 그리드는 view 가 바뀔 때 새로 마운트되므로 이 effect 가 한 번씩 돈다.
   */
  useEffect(() => {
    ref.current
      ?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]:not(:disabled)')
      ?.focus();
  }, []);

  return (
    // 시트 본문이 이미 스크롤한다 — 여기서 높이를 잡으면 스크롤이 두 겹이 된다.
    <div ref={ref} role="group" aria-label={label} className={cn("grid gap-1.5", COLUMNS[columns])}>
      {children}
    </div>
  );
}

/**
 * 🚨 **고른 칸을 색 하나로 말하지 않는다.** `brand` 채움 + 흰 글자라 명도가 함께 뒤집히고,
 *    `aria-pressed` 가 같은 사실을 말로 낸다 (디자인 시스템 §3 · §10 — 색은 단독 신호가 못 된다).
 * 🚨 **높이를 고정하지 않는다.** 글자가 들어가는 칸이라 `min-h-touch` 다 — 글자를 키우면
 *    칸이 늘어나야 연도가 잘리지 않는다 (§10).
 */
function PickerCell({
  selected,
  disabled = false,
  onClick,
  children,
}: {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "text-body-sm min-h-touch rounded-field ease-standard flex items-center justify-center px-1 transition-colors duration-120",
        selected
          ? "bg-brand hover:bg-brand-hover active:bg-brand-hover text-white"
          : "text-ink hover:bg-surface-muted active:bg-surface-muted",
        // 🚨 비활성을 브랜드색 흐리게로 만들지 않는다 (디자인 시스템 §2-5).
        "disabled:text-ink-subtle disabled:hover:bg-transparent disabled:active:bg-transparent",
      )}
    >
      {children}
    </button>
  );
}

/**
 * 라이브러리 기본 CSS 를 불러오지 않고 토큰 클래스로만 그린다 —
 * 디자인 시스템 §5 "컴포넌트에서 #hex 를 직접 쓰지 않는다" 를 지키는 유일한 방법이다.
 *
 * 🚨 머리줄(`nav` · `dropdowns`)이 여기 없는 이유는 **우리가 그리기 때문**이다 (위 머리말).
 *    `caption_label` 은 `sr-only` 로 남긴다 — 라이브러리가 그리드에 `aria-labelledby` 로
 *    거는 이름이라, 지우면 그리드가 무슨 달인지 스크린리더에서 사라진다.
 */
const CALENDAR_CLASSES = {
  root: "w-full",
  months: "flex flex-col gap-4",
  month: "flex flex-col gap-3",
  month_caption: "sr-only",
  caption_label: "",
  month_grid: "w-full border-collapse",
  weekdays: "flex",
  weekday: "text-caption text-ink-subtle flex h-8 flex-1 items-center justify-center font-normal",
  weeks: "flex flex-col gap-1",
  week: "flex",
  day: "flex flex-1 items-center justify-center p-0",
  day_button:
    "text-body-sm text-ink ease-standard flex h-touch aspect-square items-center justify-center rounded-full transition-colors duration-120 hover:bg-surface-muted",
  selected: "[&_button]:bg-brand [&_button]:text-white [&_button]:hover:bg-brand-hover",
  today: "[&_button]:text-brand [&_button]:font-semibold",
  outside: "[&_button]:text-ink-subtle",
  disabled: "[&_button]:text-ink-subtle [&_button]:opacity-40 [&_button]:hover:bg-transparent",
  hidden: "invisible",
} as const;

/* ── 날짜 셈 ──────────────────────────────────────────────────────────── */

/**
 * ⚠️ 아래 셋은 **표시가 아니라 입력 제약**이다 — 고를 수 있는 범위 안으로 달을 붙잡아 두는
 *    일이라 apps/web/CLAUDE.md §4 가 허용한 예외 안에 있다. 나이·상대 날짜는 여전히
 *    서버가 만든다.
 */
function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function addMonths(date: Date, delta: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + delta, 1);
}

/** 연도만 고르면 달이 범위 밖으로 나갈 수 있다 (2021년 12월 → 올해 12월은 아직 안 왔다). */
function clampMonth(date: Date, fromDate: Date, toDate: Date): Date {
  const candidate = startOfMonth(date);
  const first = startOfMonth(fromDate);
  const last = startOfMonth(toDate);
  if (candidate < first) return first;
  if (candidate > last) return last;
  return candidate;
}

/** `YYYY-MM-DD` → Date. 로컬 자정으로 만든다 (UTC 파싱은 하루가 밀린다). */
function parseDate(value: string): Date | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return undefined;
  const date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(date.getTime()) ? undefined : date;
}
