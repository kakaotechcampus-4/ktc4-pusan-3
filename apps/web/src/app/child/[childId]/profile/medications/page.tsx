"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, Pill } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef } from "react";

import { AuthGate } from "@/components/auth-gate";
import { DoseCheck } from "@/components/medication-dose-check";
import { ChildNav } from "@/components/child-nav";
import { ScreenTransition } from "@/components/route-transition";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { CountChip } from "@/components/ui/chip";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { phaseOf, Swap } from "@/components/ui/swap";
import { useChildId } from "@/hooks/use-child-id";
import {
  api,
  qk,
  type MedicationCourse,
  type MedicationDose,
  type MedicationsResponse,
} from "@/lib/api";
import { formatDay } from "@/lib/format";
import { timesLabel } from "@/lib/medication";
import { MEDICATION_ENABLED } from "@/lib/store-policy";
import { BACK } from "@/lib/view-transition";

/**
 * 11-3 복약 — 진행 중인 복약과 회차 체크 (#537 · `docs/web/medication-v1.md` §3).
 *
 * 🚨 **복용 기록은 여기서만 생긴다.** 대화에서 "먹였어" 라고 적어도 아무것도 쓰지 않고 이 화면으로 안내한다
 *    (Health 의 `dose.check_on_screen`). 회차 1번이 한 행이고 그 체크 칸이 곧 기록이다 (#546).
 * 🚨 **승인 게이트가 아니다** — 체크는 되돌릴 수 있다. `btn-approve` · `caution` 을 쓰지 않는다 (최상위 §2 —
 *    게이트를 늘리지 않는다). 게이트는 복약을 **등록하는** 카드(04 "찾은 복약" 시트)에 있다.
 * 🚨 **날짜를 화면이 계산하지 않는다.** 무엇이 진행 중인지, 오늘이 언제인지, 어느 회차를 체크할 수 있는지는 서버가
 *    정한다 (`today` · `checkable`). 화면은 그 값을 그대로 쓰고, 오늘 줄은 서버의 `today` 와 같은 글자인지만 본다.
 * 🚨 **"먹여도 돼요" 로 읽힐 표시를 만들지 않는다.** 체크 칸은 먹였다는 기록일 뿐이다 — 다음 회차까지 남은 시간이나
 *    "지금 먹이세요" 같은 판단을 세우지 않는다 (Health 는 진단 · 허가하지 않는다 · 최상위 §2).
 *
 * 🚨 **11-1 처럼 11 아래 라우트다.** 대화 · 초안 카드에서도 들어오지만 돌아가는 길은 프로필로 고정한다 —
 *    `router.back()` 은 주소로 바로 들어오면 앱 밖으로 나간다 (`profile/growth` 와 같은 이유).
 * 🚨 **`?course=` 로 열면 그 코스 카드를 맨 위로 올린다** (04 체크 안내 · #713). 목록을 거르거나 순서를 바꾸지 않는다 —
 *    다른 약의 오늘 회차도 같은 화면에서 체크해야 한다. 목록에 없는 코스(끝났거나 멈춘 코스)면 그냥 처음부터 보인다.
 */
export default function MedicationsPage() {
  // 🚨 스토어 선언 때문에 꺼 둔 동안은 주소로 바로 와도 화면을 세우지 않는다 (`lib/store-policy.ts`).
  if (!MEDICATION_ENABLED) return <BackToProfile />;
  return (
    // 🚨 `useSearchParams()` 는 Suspense 경계 안에 있어야 한다 (04 대화와 같은 이유).
    <Suspense fallback={null}>
      <AuthGate>
        <ScreenTransition>
          <MedicationsScreen />
        </ScreenTransition>
      </AuthGate>
    </Suspense>
  );
}

/** 꺼 둔 동안 이 주소로 온 보호자를 프로필로 보낸다. 기록에 남지 않게 `replace` 다 — 뒤로 가기로 다시 오지 않는다. */
function BackToProfile() {
  const childId = useChildId();
  const router = useRouter();
  useEffect(() => {
    router.replace(`/child/${childId}/profile`);
  }, [childId, router]);
  return null;
}

function MedicationsScreen() {
  const childId = useChildId();
  const openCourse = useSearchParams().get("course");
  const medications = useQuery({
    queryKey: qk.medications(childId),
    queryFn: () => api.get<MedicationsResponse>(`/children/${childId}/medications`),
  });
  const courses = medications.data?.courses ?? [];

  return (
    <Screen className="gap-6" nav={<ChildNav active="profile" />}>
      <header className="flex flex-col gap-3">
        <Link
          href={`/child/${childId}/profile`}
          {...BACK}
          className="text-body-sm text-ink-muted ease-standard active:text-ink hover:text-ink min-h-touch -my-2 -ml-1 flex w-fit items-center gap-1 py-2 pr-2 pl-1 transition-colors duration-120"
        >
          <ChevronLeft aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
          아이 프로필
        </Link>
        <div>
          <div className="flex items-baseline gap-2">
            <PageTitle>복약</PageTitle>
            {courses.length > 0 ? <CountChip>{courses.length}건</CountChip> : null}
          </div>
          <p className="text-body-sm text-ink-muted mt-2">
            먹인 회차를 체크해 두면 그게 복용 기록이에요. 체크는 언제든 풀 수 있어요.
          </p>
        </div>
      </header>

      <Swap id={phaseOf(medications)}>
        {medications.isPending ? (
          <SkeletonBlock label="복약을 불러오는 중" />
        ) : medications.isError ? (
          <CardFailed>
            <p className="text-body text-ink">복약을 불러오지 못했어요</p>
            <Button
              variant="secondary"
              size="compact"
              onClick={() => void medications.refetch()}
              className="mt-3"
            >
              다시 불러오기
            </Button>
          </CardFailed>
        ) : courses.length === 0 ? (
          <EmptyState
            icon={Pill}
            title="지금 먹이는 약이 없어요"
            description="대화에 약 이름과 언제, 며칠 먹이는지 적어 주시면 복약 일정을 만들어 드려요."
            count={0}
            countLabel="진행 중인 복약"
          />
        ) : (
          <div className="flex flex-col gap-4">
            {courses.map((course) => (
              <CourseCard
                key={course.id}
                childId={childId}
                course={course}
                today={medications.data.today}
                opened={course.id === openCourse}
                alone={courses.length === 1}
              />
            ))}
          </div>
        )}
      </Swap>
    </Screen>
  );
}

/** 회차를 날짜별로 묶는다. 🚨 **최근 날이 위다** — 오늘 먹일 것이 제일 먼저 보여야 한다. 서버 순서를 뒤집기만 한다. */
function byDay(doses: readonly MedicationDose[]): Array<[string, MedicationDose[]]> {
  const days = new Map<string, MedicationDose[]>();
  for (const dose of doses) {
    const list = days.get(dose.dose_date) ?? [];
    list.push(dose);
    days.set(dose.dose_date, list);
  }
  return [...days].reverse();
}

function CourseCard({
  childId,
  course,
  today,
  opened,
  alone,
}: {
  childId: string;
  course: MedicationCourse;
  today: string;
  /** 주소(`?course=`)가 가리킨 코스. 처음 그려질 때 한 번 맨 위로 올리고 포커스를 옮긴다. */
  opened: boolean;
  /** 코스가 이것 하나뿐이다. 맨 위 카드라 스크롤하지 않는다 — 회차가 길면 머리말만 화면 밖으로 밀린다. */
  alone: boolean;
}) {
  const days = byDay(course.doses);
  const todays = course.doses.filter((dose) => dose.dose_date === today);
  const checkedToday = todays.filter((dose) => dose.checked_at !== null).length;

  // 🚨 **처음 그려질 때 한 번만** 옮긴다. 체크하다 다시 그려질 때마다 끌어올리면 보던 자리를 잃는다.
  //    포커스도 같이 옮겨서 화면 읽기 프로그램이 어느 약으로 왔는지 읽는다.
  const anchor = useRef<HTMLDivElement>(null);
  const openedOnMount = useRef({ opened, alone });
  useEffect(() => {
    if (!openedOnMount.current.opened) return;
    if (!openedOnMount.current.alone) anchor.current?.scrollIntoView({ block: "start" });
    anchor.current?.focus({ preventScroll: true });
  }, []);
  const titleId = `course-${course.id}`;

  return (
    <div
      ref={anchor}
      tabIndex={opened ? -1 : undefined}
      // 화면 읽기 프로그램은 카드 전체가 아니라 약 이름을 읽는다. 키보드로 온 포커스는 체크 칸과 같은 링으로 보인다.
      // 🚨 이름은 role 이 있어야 붙는다 — role 없는 div(generic)에 단 이름은 ARIA 가 금지하고 읽기 프로그램이 무시한다.
      role={opened ? "group" : undefined}
      aria-labelledby={opened ? titleId : undefined}
      className="rounded-card focus-visible:outline-brand scroll-mt-4 outline-none focus-visible:outline-2 focus-visible:outline-offset-2"
    >
      <Card>
        <p id={titleId} className="text-body text-ink">
          {course.title}
          {course.dosage ? <span className="text-ink-muted"> {course.dosage}</span> : null}
        </p>
        <p className="text-body-sm text-ink-muted mt-1">매일 {timesLabel(course.notice_times)}</p>
        <p className="text-caption text-ink-subtle mt-1">
          {formatDay(course.starts_on)}부터 {formatDay(course.ends_on)}까지
        </p>

        {/* 🚨 **사실만 센다** — 오늘 회차 중 체크한 수다. 다 했다고 축하하거나 색을 바꾸지 않는다. */}
        {todays.length > 0 ? (
          <p className="text-body-sm text-ink-muted mt-3">
            오늘 체크 {checkedToday}/{todays.length}회
          </p>
        ) : null}

        <div className="mt-2 flex flex-col">
          {days.map(([day, doses]) => (
            <section key={day} className="border-line border-t pt-2 first:border-t-0 first:pt-0">
              <h3 className="text-label text-ink-muted mt-2">
                {day === today ? `오늘 · ${formatDay(day)}` : formatDay(day)}
              </h3>
              <ul>
                {doses.map((dose) => (
                  <li key={dose.id}>
                    <DoseCheck childId={childId} dose={dose} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </Card>
    </div>
  );
}
