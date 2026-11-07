"use client";

import { DoseCheck } from "@/components/medication-dose-check";
import { Button, ButtonLink } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import type { MedicationCourse, MedicationDose, MedicationsResponse } from "@/lib/api";
import { FORWARD } from "@/lib/view-transition";

/**
 * 03 홈 "오늘 먹일 약" — 진행 중인 복약의 **오늘 회차만** 세우고 거기서 바로 체크한다 (#563 리뷰).
 *
 * 왜 홈인가 — 약을 먹인 보호자가 앱을 열어 바로 체크하는 자리라서다. 11-3 은 프로필 안쪽이라 한 번 더 들어가야 하고,
 * 놓친 체크만큼 병원에 가져갈 기록이 빈다. 그래서 체크는 여기서도 하고, 지난 날짜와 코스 전체는 11-3 링크가 진다.
 *
 * - 🚨 **쿼리는 홈 화면이 든다** (`home/page.tsx`). 이 구역 안에서 요청을 시작하면 홈 응답을 받은 **뒤에야** 나가서,
 *   뜬 화면 한가운데에 구역이 뒤늦게 끼어들며 아래 카드를 밀었다 (#563 재리뷰 · 디자인 시스템 §8 — 화면이 뜰 때는
 *   움직이지 않는다). 홈이 두 쿼리를 같이 시작하고, 둘 다 끝난 뒤 한 번에 그린다. 머리(`Section`)도 홈이 단다.
 * - 🚨 **오늘 회차가 있을 때만 선다** (`todayCourses` 가 비면 홈이 구역을 안 세운다). "오늘" 은 서버의 `today` 와 같은
 *   글자인지로만 가른다 — 날짜 계산이 아니다 (`docs/web/medication-v1.md` §3).
 * - 🚨 **게이트가 아니다.** 11-3 과 같은 `DoseCheck` 다 — `btn-approve` · `caution` 이 없고, 같은 쿼리를 고쳐서
 *   한쪽에서 체크하면 다른 쪽도 같다.
 * - 🚨 **먹여도 된다는 뜻으로 읽힐 표시를 만들지 않는다.** 남은 시간이나 "지금 먹이세요" 를 세우지 않고, "오늘 체크 n/m회"
 *   도 사실만 센다. 도메인 색도 쓰지 않는다 (홈의 도메인 색은 제안 알약이 진다).
 */

export interface TodayCourse {
  course: MedicationCourse;
  doses: MedicationDose[];
}

/** 오늘 회차가 있는 코스만. 서버 `today` 와 같은 글자인 회차를 고른다. */
export function todayCourses(data: MedicationsResponse): TodayCourse[] {
  return data.courses
    .map((course) => ({
      course,
      doses: course.doses.filter((dose) => dose.dose_date === data.today),
    }))
    .filter(({ doses }) => doses.length > 0);
}

export function TodayMedications({
  childId,
  courses,
}: {
  childId: string;
  courses: readonly TodayCourse[];
}) {
  return (
    <>
      <Card className="divide-line flex flex-col divide-y">
        {courses.map(({ course, doses }) => (
          <div key={course.id} className="py-3 first:pt-0 last:pb-0">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <p className="text-body text-ink">
                {course.title}
                {course.dosage ? <span className="text-ink-muted"> {course.dosage}</span> : null}
              </p>
              <p className="text-caption text-ink-subtle">
                오늘 체크 {doses.filter((dose) => dose.checked_at !== null).length}/{doses.length}회
              </p>
            </div>
            <ul className="mt-1">
              {doses.map((dose) => (
                <li key={dose.id}>
                  <DoseCheck childId={childId} dose={dose} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </Card>
      <ButtonLink
        href={`/child/${childId}/profile/medications`}
        variant="tertiary"
        size="compact"
        className="mt-2"
        transitionTypes={FORWARD.transitionTypes}
      >
        지난 날짜와 전체 일정 보기
      </ButtonLink>
    </>
  );
}

/**
 * 불러오지 못했다 — 그 사실만 이 구역에서 말한다. 🚨 홈 전체를 막지 않는다 (NF-06). 복약이 있는지 모르는 채로 구역을
 * 지우면 체크할 회차가 있어도 안 보인다.
 */
export function TodayMedicationsFailed({ onRetry }: { onRetry: () => void }) {
  return (
    <CardFailed>
      <p>오늘 먹일 약을 불러오지 못했어요.</p>
      <Button variant="tertiary" size="compact" className="mt-3" onClick={onRetry}>
        다시 시도
      </Button>
    </CardFailed>
  );
}
