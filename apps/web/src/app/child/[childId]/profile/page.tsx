"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Pencil, Pill, Ruler, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ScreenTransition } from "@/components/route-transition";
import {
  ChildIdentityCard,
  ChildIdentityCardSkeleton,
  ChildIdentitySheet,
} from "@/components/child-identity";
import { ChildNav } from "@/components/child-nav";
import { GrowthLatestCard, GrowthLatestCardSkeleton } from "@/components/growth-log-list";
import { GrowthSheet } from "@/components/growth-sheet";
import { SafetySection } from "@/components/safety-section";
import { ScreenTour, TourHelpButton } from "@/components/screen-tour";
import { SettingsGroup, SettingsLinkRow } from "@/components/settings-row";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Section, SectionAction, SectionError } from "@/components/ui/section";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { phaseOf, Swap } from "@/components/ui/swap";
import { useChildId } from "@/hooks/use-child-id";
import { useSheetTarget } from "@/hooks/use-sheet-target";
import { EARLIEST_BIRTH_DATE } from "@/lib/date-bounds";
import {
  api,
  qk,
  type ChildProfile,
  type GrowthLog,
  type GrowthLogsResponse,
  type MedicationsResponse,
} from "@/lib/api";
import { timesLabel } from "@/lib/medication";
import { MEDICATION_ENABLED } from "@/lib/store-policy";
import { FORWARD } from "@/lib/view-transition";
import { profileTour } from "@/lib/tour/screens/profile";

export default function ChildProfilePage() {
  return (
    <AuthGate>
      <ScreenTransition>
        <ChildProfileScreen />
      </ScreenTransition>
    </AuthGate>
  );
}

function ChildProfileScreen() {
  const childId = useChildId();

  const profile = useQuery({
    queryKey: qk.childProfile(childId),
    queryFn: () => api.get<ChildProfile>(`/children/${childId}`),
  });

  const growth = useQuery({
    queryKey: qk.growth(childId),
    queryFn: () => api.get<GrowthLogsResponse>(`/children/${childId}/growth`),
  });

  return (
    <Screen className="gap-6" nav={<ChildNav active="profile" />}>
      {/* 🚨 **머리에 아이 이름·나이를 다시 세우지 않는다.** 바로 아래 "기본 정보" 구역의
          별명 입력과 생일 입력이 같은 값을 들고 있어서, 머리에 또 쓰면 한 화면에서 같은 사실이
          두 번 선다. 제목은 화면을 가리키고, 아이가 누구인지는 그 구역이 진다. */}
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <PageTitle>아이 프로필</PageTitle>
          <p className="text-body-sm text-ink-muted mt-2">
            {MEDICATION_ENABLED
              ? "기본 정보와 키·몸무게, 복약, 알레르기를 여기서 관리해요."
              : "기본 정보와 키·몸무게, 알레르기를 여기서 관리해요."}
          </p>
        </div>
        <TourHelpButton tour={profileTour} />
      </header>

      <IdentitySection childId={childId} query={profile} />

      <GrowthSection childId={childId} query={growth} />

      {/* 🚨 스토어 선언 때문에 꺼 둔 동안은 칸도 조회도 없다 (`lib/store-policy.ts`). 투어의 복약 단계는 비출 칸이 없어 저절로 빠진다. */}
      {MEDICATION_ENABLED ? <MedicationSection childId={childId} /> : null}

      <SafetySection childId={childId} />

      {profile.data?.role === "owner" ? <DeleteChildSection childId={childId} /> : null}

      <ScreenTour tour={profileTour} ready={profile.isSuccess && growth.isSuccess} />
    </Screen>
  );
}

/* ── 아이 삭제 (#751) ─────────────────────────────────────────────────── */

/**
 * 아이 기본정보 · 건강정보 동의를 앱 안에서 거두는 길 (#751 · 개인정보 보호법 제38조 ④).
 *
 * 🚨 **여기서 지우지 않는다.** 줄을 누르면 무엇이 지워지는지 읽고 확인하는 화면으로 간다 — 설정의
 *    탈퇴 줄과 같다. 그래서 줄에 빨강(`danger`)을 쓰지 않는다. `danger` 는 파괴적 **확정**의 색이고 그
 *    자리는 다음 화면의 확인 시트다.
 * 🚨 **등록 보호자에게만 그린다.** 서버는 함께 보는 보호자를 403 `owner_only` 로 막는다 — 버튼을 그렸다가
 *    눌러야 실패를 알려주는 길을 만들지 않는다 (설정의 초대 버튼과 같은 원칙). role 을 모르는 동안에도
 *    그리지 않는다.
 * 🚨 **화면 맨 아래다.** 손대는 구역(기본 정보 · 키·몸무게 · 알레르기) 뒤에 나가는 길이 선다 — 설정이
 *    탈퇴를 맨 아래에 두는 것과 같은 순서다. 맨 아래라 프로필을 받은 뒤에 서도 위 구역을 밀지 않는다.
 * 🚨 **아이 프로필에 둔다.** 아이 자체에 대한 것은 네비의 "아이" 칸이 소유한다 — 설정에 두면 같은 아이를
 *    다루는 문이 둘이 된다 (설정 화면 머리말).
 */
function DeleteChildSection({ childId }: { childId: string }) {
  return (
    <section aria-label="아이 삭제">
      <SettingsGroup>
        <SettingsLinkRow
          href={`/child/${childId}/profile/delete`}
          icon={Trash2}
          title="이 아이 삭제하기"
          status="아이와 쌓인 기록이 모두 지워져요"
          note="무엇이 어떻게 되는지 다음 화면에서 알려드려요"
          {...FORWARD}
        />
      </SettingsGroup>
    </section>
  );
}

/* ── 구역 틀 ──────────────────────────────────────────────────────────── */

/* ── ㉠ 기본 정보 ─────────────────────────────────────────────────────── */

function IdentitySection({
  childId,
  query,
}: {
  childId: string;
  query: ReturnType<typeof useQuery<ChildProfile>>;
}) {
  /** 고칠 대상은 늘 이 아이라 따로 들 값이 없다 — 열 때마다 바뀌는 `session`(시트를 새로 만드는 `key`)만 쓴다. */
  const editing = useSheetTarget<true>();

  return (
    <Section
      data-tour="identity"
      title="기본 정보"
      // 🚨 **설명 줄을 두지 않는다.** "아이를 부르는 말과 생일이에요" 는 바로 아래 카드가
      //    별명 · 생일 · 성별을 글자로 다 보여주는 것을 한 번 더 말할 뿐이었다 —
      //    같은 화면에서 같은 사실이 두 번 서는 자리다 (머리의 아이 이름을 뺀 것과 같은 이유).
      // 🚨 고치는 것은 드문 일이라 **행동으로** 둔다 — 입력칸을 늘 펼쳐 두지 않는다
      //    (`ChildIdentityCard` 머리말). 다른 두 구역과 같은 머리줄 문법이다.
      // 🚨 받는 동안에도 자리는 지킨다 (누르지만 못 한다) — 받은 뒤에 서면 머리줄이 커져 아래가 밀린다 (CLS).
      action={
        query.data || query.isPending ? (
          <SectionAction
            label="기본 정보 고치기"
            icon={Pencil}
            onClick={() => editing.show(true)}
            disabled={!query.data}
          />
        ) : undefined
      }
    >
      {/* 스켈레톤 → 내용은 같은 자리의 교체다 (디자인 시스템 §8). */}
      <Swap id={phaseOf(query)}>
        {query.isPending ? (
          <ChildIdentityCardSkeleton />
        ) : query.isError ? (
          <SectionError what="아이 정보를 불러오지 못했어요" onRetry={() => void query.refetch()} />
        ) : (
          <>
            <ChildIdentityCard profile={query.data} />
            {/* 🚨 열 때마다 새로 만든다 (`key`) — 폼이 `useState` 로 값을 들고 있어서 같은
              인스턴스를 재사용하면 지난번에 고치다 만 값이 남는다. 닫는 것은 `open` 이 해서
              내려가는 움직임이 남는다 (`useSheetTarget`). */}
            {editing.target ? (
              <ChildIdentitySheet
                key={editing.session}
                open={editing.open}
                onClose={editing.close}
                childId={childId}
                profile={query.data}
                earliestBirthDate={EARLIEST_BIRTH_DATE}
              />
            ) : null}
          </>
        )}
      </Swap>
    </Section>
  );
}

/* ── ㉡ 키 · 몸무게 ──────────────────────────────────────────────────── */

/**
 * 🚨 **여기는 가장 최근 한 줄만 세운다.** 쌓인 목록과 변화 그래프는 11-1 상세
 * (`profile/growth`)가 진다 — 이유는 `GrowthLatestCard` 머리말에 있다.
 *
 * 🚨 **화면의 말은 "키 · 몸무게" 고 코드의 이름은 `growth` 다.** 07 이 관찰/기록 ·
 *    프로필/기억으로 갈라 두는 것과 같은 처리다 (apps/web/CLAUDE.md §3) — 화면 문구만
 *    바꾸고 엔드포인트·쿼리 키·타입 이름을 따라 바꾸지 않는다. 그쪽은 계약서를 따른다.
 * 🚨 상세로 가는 길이 **줄 자체**다. 머리줄의 `Plus` 말고 다른 버튼을 늘리지 않는다.
 */
function GrowthSection({
  childId,
  query,
}: {
  childId: string;
  query: ReturnType<typeof useQuery<GrowthLogsResponse>>;
}) {
  const [adding, setAdding] = useState(false);

  const logs = query.data?.items ?? [];
  /**
   * 🚨 **서버가 준 순서를 믿지 않는다.** 목은 최신이 위지만 계약서가 정렬을 약속한 적이 없고,
   *    여기서 틀리면 화면이 **오래된 값을 "지금 얼마" 로** 말한다. 날짜 비교는 계산이 아니라
   *    같은 형식(`YYYY-MM-DD`)의 문자열 정렬이다 (apps/web/CLAUDE.md §4 는 **나이·기간**을 막는다).
   */
  const latest = logs.reduce<GrowthLog | null>(
    (best, log) => (best === null || log.measured_on > best.measured_on ? log : best),
    null,
  );

  return (
    <Section
      data-tour="growth"
      title="키·몸무게"
      description="가장 최근에 잰 것만 보여줘요. 눌러서 지난 기록과 변화를 볼 수 있어요."
      // 🚨 불러오기 전에는 숫자 대신 자리만(`"pending"`), 0 건이면 넘기지 않는다 — 앞은 "아직 모름" 이라
      //    0 을 그리면 거짓말이고, 뒤는 바로 아래 `EmptyState` 가 같은 숫자를 이미 말한다.
      count={query.isPending ? "pending" : logs.length > 0 ? logs.length : undefined}
      action={
        // 🚨 승인 게이트가 아니다 — 잘못 적으면 그 줄을 지우면 된다 (최상위 §2).
        <SectionAction label="키·몸무게 새로 적기" onClick={() => setAdding(true)} />
      }
    >
      <Swap id={phaseOf(query)}>
        {query.isPending ? (
          <GrowthLatestCardSkeleton />
        ) : query.isError ? (
          <SectionError what="측정 기록을 불러오지 못했어요" onRetry={() => void query.refetch()} />
        ) : latest === null ? (
          // 🚨 빈 상태를 사과문으로 쓰지 않는다. 쌓인 건수를 그대로 보여준다 (`EmptyState`).
          // 🚨 상세로 가는 길을 여기 붙이지 않는다 — 가 봐야 같은 0 건이다.
          <EmptyState
            icon={Ruler}
            title="아직 잰 기록이 없어요"
            description="어린이집 신체검사나 병원에서 잰 날, 여기에 적어 두면 돼요."
            count={0}
            countLabel="적어 둔 기록"
          />
        ) : (
          <GrowthLatestCard log={latest} href={`/child/${childId}/profile/growth`} />
        )}
      </Swap>

      <GrowthSheet open={adding} onClose={() => setAdding(false)} childId={childId} />
    </Section>
  );
}

/* ── 복약 (#537) ─────────────────────────────────────────────────────── */

/**
 * 진행 중인 복약의 입구. 🚨 **여기서 등록하지 않는다** — 복약은 대화가 만든 초안을 보호자가 확인해 넣는다
 * (승인 게이트 ㉡ · 04 "찾은 복약" 시트). 그래서 머리줄에 더하기 버튼이 없다.
 * 🚨 **회차 체크도 여기서 하지 않는다** — 11-3 상세가 진다. 이 구역은 무엇을 먹이는 중인지 한 줄씩만 세운다
 * (`GrowthSection` 이 가장 최근 한 줄만 세우는 것과 같은 이유 · 한 구역이 다른 구역을 밀지 않게).
 */
function MedicationSection({ childId }: { childId: string }) {
  const query = useQuery({
    queryKey: qk.medications(childId),
    queryFn: () => api.get<MedicationsResponse>(`/children/${childId}/medications`),
  });
  const courses = query.data?.courses ?? [];

  return (
    <Section
      data-tour="medications"
      title="복약"
      description="지금 먹이는 약이에요. 눌러서 먹인 회차를 체크할 수 있어요."
      count={courses.length > 0 ? courses.length : undefined}
    >
      <Swap id={phaseOf(query)}>
        {query.isPending ? (
          <SkeletonBlock label="복약을 불러오는 중" />
        ) : query.isError ? (
          <SectionError what="복약을 불러오지 못했어요" onRetry={() => void query.refetch()} />
        ) : courses.length === 0 ? (
          <EmptyState
            icon={Pill}
            title="지금 먹이는 약이 없어요"
            description="대화에 약 이름, 먹일 시각, 먹일 기간을 적으면 복약 일정을 준비해요. 확인하고 넣은 약만 여기에 보여요."
            count={0}
            countLabel="진행 중인 복약"
          />
        ) : (
          <Link
            href={`/child/${childId}/profile/medications`}
            {...FORWARD}
            className="rounded-card border-line bg-surface ease-standard active:bg-surface-muted hover:bg-surface-muted min-h-touch flex items-start gap-3 border px-4 py-3.5 transition-colors duration-120"
          >
            <IconTile icon={Pill} tone="neutral" />
            <span className="flex min-w-0 flex-1 flex-col gap-1.5">
              {courses.map((course) => (
                <span key={course.id} className="flex flex-col">
                  <span className="text-body text-ink">{course.title}</span>
                  <span className="text-caption text-ink-subtle">
                    매일 {timesLabel(course.notice_times)}
                  </span>
                </span>
              ))}
            </span>
            <span className="sr-only">회차 체크하러 가기</span>
            <ChevronRight
              aria-hidden
              size={ICON_SIZE.md}
              strokeWidth={ICON_STROKE}
              className="text-ink-subtle mt-1.5 shrink-0"
            />
          </Link>
        )}
      </Swap>
    </Section>
  );
}

/* ── ㉢ 알레르기 · 건강 (🚨 승인 게이트 ㉡) ──────────────────────────── */
