"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useRef, useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ScreenTransition } from "@/components/route-transition";
import { CalendarDayPanel } from "@/components/calendar-day";
import { ChildNav } from "@/components/child-nav";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { MemoryDetailSheet, type MemoryTarget } from "@/components/memory-detail-sheet";
import { MonthGrid } from "@/components/month-grid";
import { Button } from "@/components/ui/button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { phaseOf, Swap } from "@/components/ui/swap";
import { useChildId } from "@/hooks/use-child-id";
import {
  api,
  isApiError,
  qk,
  type CalendarDay,
  type CalendarDayResponse,
  type CalendarMonthResponse,
} from "@/lib/api";
import { formatDay, parseISODate, toISODate, toMonthKey } from "@/lib/format";

/**
 * 09 캘린더.
 *
 * 🚨 **여기에 승인 게이트가 없다.** 캘린더 쓰기(게이트 ㉠)는 05·06 이 지고, 이 화면에 서는
 *    일정은 전부 보호자가 이미 제출한 것이다 (`event` 에는 제출한 행만 있다 · #118). 그래서
 *    `btn-approve` 도 `caution` 도 쓰지 않는다 — 그 둘은 되돌릴 수 없는 2곳 전용이다.
 *
 * 🚨 **일기는 관찰이 아니다** (계약서 §09). 같은 날짜 아래 있어도 구역이 다르고, 화면이
 *    "기억으로 저장되지 않아요" 를 말한다.
 *
 * 🚨 **날짜 계산을 하지 않는다.** 달 넘기기는 `react-day-picker` 가, 표시 문구는 `lib/format`
 *    이 맡는다 — 상대 시간·나이·기간은 어느 쪽도 만들지 않는다 (apps/web/CLAUDE.md §4).
 */
export default function CalendarPage() {
  return (
    // 🚨 `useSearchParams()` 는 Suspense 경계 안에 있어야 한다 (05·07 과 같은 이유).
    <Suspense fallback={null}>
      <AuthGate>
        <ScreenTransition>
          <CalendarScreen />
        </ScreenTransition>
      </AuthGate>
    </Suspense>
  );
}

function CalendarScreen() {
  const childId = useChildId();
  const router = useRouter();
  const searchParams = useSearchParams();

  /**
   * 고른 날의 정본은 URL 이다 (아이 스코프와 같은 규칙 · apps/web/CLAUDE.md §3).
   * 뒤로가기가 날짜를 되돌려야 하고, 링크로 그날을 바로 열 수 있어야 한다.
   * 잘못된 값이면 오늘로 떨어진다 — `parseISODate` 가 `null` 을 주고 판단은 여기서 한다.
   */
  const dateParam = searchParams.get("date");
  const date = toISODate((dateParam ? parseISODate(dateParam) : null) ?? new Date());
  // 문자열에서 되만들어 렌더마다 같은 값이 되게 한다 — `new Date()` 를 그대로 넘기면
  // 달력이 매 렌더 새 객체를 받는다.
  const selected = useMemo(() => parseISODate(date) ?? new Date(), [date]);
  const monthKey = toMonthKey(selected);

  const [target, setTarget] = useState<MemoryTarget | null>(null);

  /**
   * 일기 편집기를 연 날. 🚨 불리언이 아니라 **날짜**로 든다 — 다른 날로 옮기면(뒤로가기로 날짜만
   * 바뀌어도) 닫힌다. 열린 채로 넘어가면 그 사이 저장이 **다른 날의 일기**가 된다.
   * 🚨 **옮기는 순간 비운다.** 날짜 비교만 하면 옮긴 동안만 닫히고, 그날로 돌아오면 누르지도 않은
   *    빈 편집기가 다시 열려 키보드까지 올라온다. 렌더 중에 비우는 것은 이전 값에 맞춰 상태를
   *    고치는 React 의 방식이다 (effect 로 비우면 한 번은 열린 채로 그려진다).
   */
  const [writingDate, setWritingDate] = useState<string | null>(null);
  if (writingDate !== null && writingDate !== date) setWritingDate(null);
  const writing = writingDate === date;
  /**
   * 방금 일기를 지웠다. 🚨 지우면 누른 버튼이 구역째 사라져서 포커스가 `<body>` 로 떨어진다 —
   * 키보드 · 스크린리더 사용자는 어디에 서 있는지 잃는다. 다시 서는 머리의 "일기 쓰기" 가
   * 포커스를 받는다 (아래 버튼의 `ref`). 🚨 `preventScroll` — 보던 자리에서 화면 맨 위로 튀지 않는다.
   * 렌더와 상관없는 표시라 상태가 아니라 ref 다.
   */
  const focusWriteOnMount = useRef(false);

  const monthQuery = useQuery({
    queryKey: qk.calendar(childId, monthKey),
    queryFn: () =>
      api.get<CalendarMonthResponse>(`/children/${childId}/calendar`, {
        query: { month: monthKey },
      }),
  });

  const dayQuery = useQuery({
    queryKey: qk.calendarDay(childId, date),
    queryFn: () => api.get<CalendarDayResponse>(`/children/${childId}/calendar/${date}`),
  });

  const days = monthQuery.data?.days ?? [];
  const summary = days.find((day) => day.date === date);

  /**
   * 머리의 "일기 쓰기" 는 **그날 일기가 없다는 것을 안 뒤에만** 선다. 있는 날은 일기 카드의
   * "일기 고치기" 가 그 일을 하고, 하루 보기를 받기 전에는 어느 쪽인지 모른다.
   */
  const canWrite = dayQuery.data?.my_diary === null && !writing;

  function selectDate(next: Date) {
    router.replace(`/child/${childId}/calendar?date=${toISODate(next)}`, { scroll: false });
  }

  return (
    <Screen className="gap-6" nav={<ChildNav active="calendar" />}>
      <header>
        {/* 🚨 **버튼은 제목 줄에만 서고, 그 줄은 버튼이 없어도 버튼 높이다** (`min-h-touch`).
            버튼은 일기 없는 날에만 서서, 부제 옆까지 차지하면 좁은 폭(360)에서 부제 줄 수가
            날마다 바뀌고 **달력이 손가락 밑에서 위아래로 움직인다.** */}
        <div className="min-h-touch flex items-center justify-between gap-3">
          <PageTitle>캘린더</PageTitle>
          {/* 고른 날의 일기를 쓴다 (#503). 홈의 설정 톱니와 같은 자리 — 화면 머리 오른쪽 끝이다.
              🚨 하루 패널의 빈 상태("채우려고 만들지 않아요")와 떨어진 자리라 둘이 붙지 않는다. */}
          {canWrite ? (
            <Button
              ref={(node) => {
                if (!node || !focusWriteOnMount.current) return;
                focusWriteOnMount.current = false;
                node.focus({ preventScroll: true });
              }}
              variant="tertiary"
              size="compact"
              className="shrink-0"
              // 🚨 버튼이 고른 날과 멀리 있어서, 어느 날의 일기인지는 이름이 말한다 (보이는 글자를 품는다).
              aria-label={`${formatDay(date)} 일기 쓰기`}
              onClick={() => setWritingDate(date)}
            >
              일기 쓰기
            </Button>
          ) : null}
        </div>
        <p className="text-body-sm text-ink-muted mt-1">
          승인한 일정과 그날 남긴 기록이 날짜별로 모여요.
        </p>
      </header>

      <section className="flex flex-col gap-3">
        {/* 🚨 보고 있는 달을 따로 들지 않는다. `month` 는 고른 날이 속한 달이고, 달을 넘기면
            그 달의 첫날이 골라진다 — 상태를 둘로 두면 뒤로가기로 날짜만 되돌아왔을 때
            **다른 달을 보면서 이 달의 하루를 여는** 어긋난 화면이 생긴다. 정본은 URL 하나다. */}
        <MonthGrid
          month={selected}
          onMonthChange={selectDate}
          selected={selected}
          onSelect={selectDate}
          days={days}
        />
        {monthQuery.isError ? (
          <LoadFailed what="이 달을" childId={childId} error={monthQuery.error} />
        ) : null}
      </section>

      <section className="flex flex-col gap-3">
        <div>
          {/* 🚨 날짜 문구를 조합해 만들지 않는다 — 절대 날짜 하나를 한국어 표기로 바꾼 것뿐이다. */}
          <h2 className="text-title text-ink">{formatDay(date)}</h2>
          {/* 🚨 아무것도 없는 날에는 이 줄을 그리지 않는다. 하루 패널의 빈 상태가 같은 말을
              더 크게 하고 있어서, 남겨 두면 한 화면이 같은 사실을 두 번 말한다. */}
          {daySummary(summary) ? (
            <p className="text-body-sm text-ink-muted mt-1">{daySummary(summary)}</p>
          ) : null}
        </div>

        {/* 다른 날을 고르면 이 자리의 하루가 갈아 끼워진다 (디자인 시스템 §8 "교체"). 날짜가 같으면
            다시 받아도 그대로다. */}
        <Swap id={`${date}:${phaseOf(dayQuery)}`}>
          {dayQuery.isPending ? <SkeletonBlock label="이날을 불러오는 중" /> : null}

          {dayQuery.isError ? (
            <LoadFailed what="이날을" childId={childId} error={dayQuery.error} />
          ) : null}

          {dayQuery.data ? (
            // 🚨 `key` 로 날짜마다 새로 세운다. 안 그러면 일기 초안이 앞 날짜에서
            //    넘어오고, 그 사이에 저장을 누르면 **다른 날의 일기가 이 날짜로 저장된다.**
            <CalendarDayPanel
              key={date}
              childId={childId}
              date={date}
              data={dayQuery.data}
              writing={writing}
              onWritingDone={() => setWritingDate(null)}
              onDiaryDeleted={() => {
                focusWriteOnMount.current = true;
              }}
              onOpenObservation={(observation) => setTarget({ type: "observation", observation })}
            />
          ) : null}
        </Swap>
      </section>

      <MemoryDetailSheet childId={childId} target={target} onClose={() => setTarget(null)} />
    </Screen>
  );
}

/**
 * 달 조회가 이미 들고 있는 숫자로 한 줄 요약한다 — 하루 조회를 기다리는 동안에도 이 줄은
 * 바로 서서, 부모가 고른 날에 뭐가 있는지 먼저 안다.
 *
 * 🚨 **없는 날에는 아무 말도 하지 않는다.** 빈 날의 문구는 하루 패널의 빈 상태 하나가 맡는다 —
 *    요약 줄까지 같은 말을 하면 한 화면이 같은 사실을 두 번 말한다.
 */
function daySummary(summary: CalendarDay | undefined): string | null {
  if (!summary) return null;

  const parts: string[] = [];
  if (summary.has_event) parts.push("일정 있음");
  if (summary.observation_count > 0) parts.push(`기록 ${summary.observation_count}건`);
  if (summary.has_my_diary) parts.push("일기 있음");
  // 월 그리드에는 사진 표식이 없다 — 사진만 있는 날을 골랐을 때 이 줄이 그 사실을 먼저 말한다.
  if (summary.has_shared_photo) parts.push("사진 있음");

  // 🚨 빈 날은 **여기서 말하지 않는다.** 아래 하루 패널의 빈 상태가 그 말을 맡는다 —
  //    같은 사실을 두 곳에서 하면 화면이 사과하는 것처럼 읽힌다 (문서 §7).
  return parts.length === 0 ? null : parts.join(", ");
}

/**
 * 🚨 **실패를 빨강으로 칠하지 않는다** (문서 §3). 🚨 **기본값으로 대체하지 않는다** —
 *    못 불러왔으면 못 불러왔다고 말한다 (apps/web/CLAUDE.md §3).
 */
function LoadFailed({ what, childId, error }: { what: string; childId: string; error: unknown }) {
  if (isApiError(error) && error.code === "consent_required") {
    return <ConsentRequiredCard childId={childId} error={error} what={`${what} 볼 수 없어요.`} />;
  }

  return (
    <div className="bg-surface-muted rounded-card text-body-sm text-ink-muted p-4" role="status">
      {what} 지금 불러오지 못했어요. 잠시 뒤에 다시 열어주세요.
    </div>
  );
}
