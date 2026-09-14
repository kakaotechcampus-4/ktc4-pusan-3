"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { DayPicker } from "react-day-picker";
import { ko } from "react-day-picker/locale";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import type { CalendarDay } from "@/lib/api/types";
import { cn } from "@/lib/cn";
import { toISODate } from "@/lib/format";

/**
 * 09 월 그리드 — 날짜 칸이 **무엇이 있었는지까지** 말한다.
 *
 * 점 하나만 찍는 달력을 거부한다. 점 하나는 "뭔가 있다" 까지만 말하고, 그러면 부모가 한 달치를
 * 전부 눌러 봐야 한다 — 밤에 한 손으로 여는 화면에서 그게 곧 인지 노동이다 (CLAUDE.md §1).
 *
 * 🚨 **표식을 색으로 가르지 않는다.** 이 시스템의 색은 뜻이 1:1 로 정해져 있고(문서 §3),
 *    "일정" · "관찰" · "일기" · "프로필 변화" 는 그 목록에 없다. 새 색을 넷 만들면 도메인 4색과
 *    섞여서 색이 무엇을 뜻하는지가 통째로 흐려진다. 그래서 **모양**으로 가른다.
 *
 * 🚨 **모양도 단독 신호가 될 수 없다** (문서 §3 · §10). 모양을 뜻으로 잇는 것은 그리드 아래
 *    **범례**이고, 칸마다 `aria-label` 이 같은 사실을 말로 다시 낸다. 세 경로가 같은 말을 한다.
 *
 * 🚨 **일기에 관찰과 같은 표식을 주지 않는다.** 일기는 관찰로 자동 추출되지 않는다(계약서 §09) —
 *    같은 점을 찍으면 화면이 그 규칙의 반대말을 한다. 가로 막대(일정)와 세로 막대(일기)는
 *    축이 달라서 6px 에서도 갈린다.
 *
 * 표식은 전부 `currentColor` 다. 고른 날(`brand` 채움 · 흰 글자) · 오늘 · 비활성의 글자색을
 * 그대로 따라가서, 어느 상태에서도 표식이 바탕에 묻히거나 혼자 튀지 않는다.
 *
 * 🚨 **기본 CSS 를 불러오지 않는다.** `classNames` 로 토큰만 입힌다 (apps/web/CLAUDE.md §3).
 *    라이브러리를 얹은 이유는 그리드 ARIA · 방향키 · 월 경계 · 로케일 넷뿐이다.
 */
export function MonthGrid({
  month,
  onMonthChange,
  selected,
  onSelect,
  days,
}: {
  /** 보고 있는 달. 🚨 달 넘기기는 라이브러리가 한다 — 프론트가 날짜를 더하고 빼지 않는다. */
  month: Date;
  onMonthChange: (month: Date) => void;
  selected: Date;
  onSelect: (date: Date) => void;
  /** 서버가 내려준 그 달의 요약. 목록에 없는 날은 표식이 없다. */
  days: CalendarDay[];
}) {
  const byDate = new Map(days.map((day) => [day.date, day]));
  /** 🚨 계약서에 아직 없는 optional 필드다. 안 오면 고리 표식도 범례의 그 줄도 나오지 않는다. */
  const hasProfileMarks = days.some((day) => (day.profile_changed_count ?? 0) > 0);

  return (
    <div className="flex flex-col gap-3">
      <DayPicker
        mode="single"
        required
        locale={ko}
        month={month}
        onMonthChange={onMonthChange}
        selected={selected}
        onSelect={onSelect}
        // 🚨 지난 달 날짜를 그리지 않는다. 그려 두면 누를 수 있는데, 누르면 이 달에 없는 날이
        //    골라져서 아직 받아오지 않은 달의 하루가 열린다 — 빈 화면으로 보이는 사고다.
        showOutsideDays={false}
        components={{
          Chevron: ({ orientation }) =>
            orientation === "left" ? (
              <ChevronLeft size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
            ) : (
              <ChevronRight size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
            ),
          DayButton: ({ day, modifiers, children, ...buttonProps }) => {
            const marks = markList(byDate.get(toISODate(day.date)));

            return (
              <button
                {...buttonProps}
                // 라이브러리가 만든 라벨("2026년 9월 12일")에 오늘인지와 그날 무엇이 있었는지를 잇는다.
                // 🚨 "오늘" 을 말로도 낸다 — 화면에서는 테두리와 글자색이 말하는데, 둘 다 눈에만
                //    보이는 신호다 (문서 §10).
                aria-label={[
                  buttonProps["aria-label"],
                  modifiers.today ? "오늘" : null,
                  ...marks.map((mark) => mark.spoken),
                ]
                  .filter(Boolean)
                  .join(", ")}
              >
                <span>{children}</span>
                {/* 표식이 없는 날에도 자리를 지킨다 — 있고 없고에 따라 숫자가 위아래로
                    흔들리면 달을 훑을 때 줄이 맞지 않는다. */}
                <span aria-hidden className="flex h-1.5 items-center justify-center gap-1">
                  {marks.map((mark) => (
                    <Mark key={mark.kind} kind={mark.kind} />
                  ))}
                </span>
              </button>
            );
          },
        }}
        classNames={CALENDAR_CLASSES}
      />

      <DayMarkLegend hasProfileMarks={hasProfileMarks} />
    </div>
  );
}

/* ── 표식 ─────────────────────────────────────────────────────────────── */

type MarkKind = "event" | "observation" | "diary" | "profile";

/** 순서는 이 배열이 정한다 — 칸마다 순서가 달라지면 모양을 외울 수 없다. */
const MARK_ORDER: MarkKind[] = ["event", "observation", "diary", "profile"];

const MARK_SHAPE: Record<MarkKind, string> = {
  // 가로 막대 — 시간을 차지하는 것(일정)은 눕는다.
  event: "h-[3px] w-2.5 rounded-full bg-current",
  // 찬 점 — 남긴 말 하나.
  observation: "size-1.5 rounded-full bg-current",
  // 세로 막대 — 손으로 그은 획(일기). 일정과 축이 달라서 6px 에서도 갈린다.
  diary: "h-1.5 w-[3px] rounded-full bg-current",
  // 빈 고리 — 관찰이 모여 **둘레만 생긴** 것. 채워지지 않은 것이 아직 확정이 아니라는 뜻과 맞는다.
  profile: "size-1.5 rounded-full border-[1.5px] border-current",
};

const MARK_LABEL: Record<MarkKind, string> = {
  event: "일정",
  observation: "관찰",
  diary: "일기",
  profile: "프로필 변화",
};

function Mark({ kind }: { kind: MarkKind }) {
  return <span aria-hidden className={cn("shrink-0", MARK_SHAPE[kind])} />;
}

/**
 * 🚨 표식은 **건수를 세지 않는다.** 점 세 개와 다섯 개를 눈으로 구별하는 사람은 없고,
 *    작은 점을 늘리면 칸이 지저분해지기만 한다. 종류만 모양이 말하고, 건수는 `aria-label` 과
 *    아래 하루 패널이 진다.
 */
function markList(summary: CalendarDay | undefined): Array<{ kind: MarkKind; spoken: string }> {
  if (!summary) return [];

  const spoken: Partial<Record<MarkKind, string>> = {};
  if (summary.has_event) spoken.event = "일정 있음";
  if (summary.observation_count > 0) spoken.observation = `관찰 ${summary.observation_count}건`;
  if (summary.has_diary) spoken.diary = "일기 있음";
  if ((summary.profile_changed_count ?? 0) > 0) spoken.profile = "프로필 달라짐";

  return MARK_ORDER.filter((kind) => spoken[kind] !== undefined).map((kind) => ({
    kind,
    spoken: spoken[kind] as string,
  }));
}

/**
 * 모양을 뜻으로 잇는 유일한 자리다. 🚨 지우면 표식이 단독 신호가 된다 (문서 §3 · §10).
 * `/design-system` 도 이 컴포넌트를 그대로 그린다 — 네 모양의 원본이 한 곳이어야 한다.
 */
export function DayMarkLegend({ hasProfileMarks }: { hasProfileMarks: boolean }) {
  const kinds = MARK_ORDER.filter((kind) => kind !== "profile" || hasProfileMarks);

  return (
    <ul className="text-caption text-ink-muted flex flex-wrap items-center gap-x-4 gap-y-1">
      {kinds.map((kind) => (
        <li key={kind} className="flex items-center gap-1.5">
          <Mark kind={kind} />
          {MARK_LABEL[kind]}
        </li>
      ))}
    </ul>
  );
}

/* ── 토큰 ─────────────────────────────────────────────────────────────── */

/**
 * `DateField` 의 표와 같은 언어다. 다른 점은 칸이 원이 아니라 `field` 상자라는 것 —
 * 숫자 아래 표식 줄이 들어가야 해서 정사각 원에는 두 단이 안 들어간다.
 */
const CALENDAR_CLASSES = {
  root: "relative w-full",
  months: "flex flex-col gap-4",
  month: "flex flex-col gap-2",
  nav: "absolute inset-x-0 top-0 flex h-touch items-center justify-between",
  month_caption: "flex min-h-touch items-center justify-center",
  caption_label: "text-section text-ink",
  button_previous:
    "text-ink-muted hover:bg-surface-muted active:bg-surface-muted flex h-touch aspect-square items-center justify-center rounded-full disabled:opacity-40",
  button_next:
    "text-ink-muted hover:bg-surface-muted active:bg-surface-muted flex h-touch aspect-square items-center justify-center rounded-full disabled:opacity-40",
  month_grid: "w-full border-collapse",
  weekdays: "flex",
  weekday: "text-caption text-ink-subtle flex h-8 flex-1 items-center justify-center font-normal",
  weeks: "flex flex-col gap-1",
  week: "flex gap-1",
  day: "flex flex-1 items-stretch p-0",
  day_button:
    "text-body-sm text-ink ease-standard rounded-field focus-visible:-outline-offset-2 hover:bg-surface-muted active:bg-surface-muted flex min-h-touch w-full flex-col items-center justify-center gap-1 py-1.5 transition-colors duration-120",
  selected: "[&_button]:bg-brand [&_button]:text-white [&_button]:hover:bg-brand-hover",
  // 🚨 오늘을 **색 하나로** 표시하지 않는다. `brand` 와 `ink` 는 그레이스케일에서 가깝고
  //    본문 서체가 단일 웨이트라 굵기로도 못 만든다 (문서 §3 · §4) — 테두리를 함께 준다.
  //    고른 날이 오늘이면 `selected` 의 채움이 이긴다(둘 다 걸려도 읽는 데 문제가 없다).
  today: "[&_button]:text-brand [&_button]:border [&_button]:border-brand",
  hidden: "invisible",
} as const;
