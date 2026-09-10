"use client";

import { ChevronLeft, ChevronRight, Calendar as CalendarIcon } from "lucide-react";
import { useId, useState } from "react";
import { DayPicker } from "react-day-picker";
import { ko } from "react-day-picker/locale";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { cn } from "@/lib/cn";

/**
 * 날짜 입력. 값은 `YYYY-MM-DD` 문자열이고, 달력은 바텀시트로 연다.
 *
 * **왜 `<input type="date">` 를 안 쓰나** — 브라우저·OS 마다 생김새가 완전히 달라서
 * 토큰으로 맞출 수가 없다. 디자인 시스템 §7 이 정한 입력 사양(높이 52 · `line-strong` 1px ·
 * body 16px)을 지킬 방법이 없는 유일한 입력이었다.
 *
 * **왜 팝오버가 아니라 바텀시트인가** — 이 화면은 대부분 웹뷰다(§9). 좁은 폭에서 달력을
 * 띄우면 화면 밖으로 넘치거나 입력을 가린다. 시트는 이미 있는 패턴이고 포커스 트랩·ESC 를
 * 브라우저가 준다.
 *
 * 🚨 **여기서 나이를 계산하지 않는다.** 미래 날짜를 막는 것은 "표시" 가 아니라 입력 제약이라
 *    허용된 예외다 (apps/web/CLAUDE.md §4). 나이 문구는 서버가 만든다.
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
          "rounded-field bg-surface ease-standard flex h-13 w-full items-center justify-between border px-3.5 transition-colors duration-120",
          error ? "border-danger" : "border-line-strong hover:border-ink-subtle",
        )}
      >
        <span className={cn("text-body", selected ? "text-ink" : "text-ink-subtle")}>
          {selected ? formatKorean(selected) : "생일을 골라주세요"}
        </span>
        <CalendarIcon
          aria-hidden
          size={ICON_SIZE.md}
          strokeWidth={ICON_STROKE}
          className="text-ink-subtle"
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
        description="연도와 달을 먼저 고르면 빠르게 찾을 수 있어요."
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
        <DayPicker
          mode="single"
          locale={ko}
          selected={draft}
          onSelect={setDraft}
          defaultMonth={selected ?? toDate}
          startMonth={fromDate}
          endMonth={toDate}
          disabled={{ before: fromDate, after: toDate }}
          // 🚨 생일은 몇 년 전이다. 화살표만 두면 4년 전까지 48번 눌러야 한다.
          captionLayout="dropdown"
          reverseYears
          components={{
            Chevron: ({ orientation }) =>
              orientation === "left" ? (
                <ChevronLeft size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
              ) : (
                <ChevronRight size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
              ),
          }}
          classNames={CALENDAR_CLASSES}
        />
      </BottomSheet>
    </div>
  );
}

/**
 * 라이브러리 기본 CSS 를 불러오지 않고 토큰 클래스로만 그린다 —
 * 디자인 시스템 §5 "컴포넌트에서 #hex 를 직접 쓰지 않는다" 를 지키는 유일한 방법이다.
 */
const CALENDAR_CLASSES = {
  // 화살표(nav)는 기본적으로 캡션 위 별도 줄에 그려진다. 같은 줄로 올려서
  // "◀ 2021년 9월 ▶" 한 줄이 되게 한다 — 줄이 둘로 나뉘면 시트가 그만큼 길어진다.
  root: "relative w-full",
  months: "flex flex-col gap-4",
  month: "flex flex-col gap-3",
  nav: "absolute inset-x-0 top-0 flex h-11 items-center justify-between",
  month_caption: "flex h-11 items-center justify-center",
  dropdowns: "flex items-center justify-center gap-2",
  dropdown_root: "relative",
  dropdown:
    "text-body text-ink bg-surface border-line-strong rounded-field h-11 cursor-pointer border px-3 pr-8",
  years_dropdown: "",
  months_dropdown: "",
  caption_label: "sr-only",
  button_previous:
    "text-ink-muted hover:bg-surface-muted active:bg-surface-muted flex size-11 items-center justify-center rounded-full disabled:opacity-40",
  button_next:
    "text-ink-muted hover:bg-surface-muted active:bg-surface-muted flex size-11 items-center justify-center rounded-full disabled:opacity-40",
  month_grid: "w-full border-collapse",
  weekdays: "flex",
  weekday: "text-caption text-ink-subtle flex h-8 flex-1 items-center justify-center font-normal",
  weeks: "flex flex-col gap-1",
  week: "flex",
  day: "flex flex-1 items-center justify-center p-0",
  day_button:
    "text-body-sm text-ink ease-standard flex size-11 items-center justify-center rounded-full transition-colors duration-120 hover:bg-surface-muted",
  selected: "[&_button]:bg-brand [&_button]:text-white [&_button]:hover:bg-brand-hover",
  today: "[&_button]:text-brand [&_button]:font-semibold",
  outside: "[&_button]:text-ink-subtle",
  disabled: "[&_button]:text-ink-subtle [&_button]:opacity-40 [&_button]:hover:bg-transparent",
  hidden: "invisible",
} as const;

/** `YYYY-MM-DD` → Date. 로컬 자정으로 만든다 (UTC 파싱은 하루가 밀린다). */
function parseDate(value: string): Date | undefined {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return undefined;
  const date = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(date.getTime()) ? undefined : date;
}

/** Date → `YYYY-MM-DD`. toISOString 은 UTC 라 시간대에 따라 하루가 밀린다. */
export function toISODate(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function formatKorean(date: Date): string {
  return `${date.getFullYear()}년 ${date.getMonth() + 1}월 ${date.getDate()}일`;
}
