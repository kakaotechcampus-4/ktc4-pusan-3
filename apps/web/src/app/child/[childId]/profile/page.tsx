"use client";

import { useQuery } from "@tanstack/react-query";
import { Pencil, Ruler } from "lucide-react";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ChildIdentityCard, ChildIdentitySheet } from "@/components/child-identity";
import { ChildNav } from "@/components/child-nav";
import { GrowthLatestCard } from "@/components/growth-log-list";
import { GrowthSheet } from "@/components/growth-sheet";
import { SafetySection } from "@/components/safety-section";
import { EmptyState } from "@/components/ui/empty-state";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Section, SectionAction, SectionError } from "@/components/ui/section";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { useChildId } from "@/hooks/use-child-id";
import { EARLIEST_BIRTH_DATE } from "@/lib/date-bounds";
import { api, qk, type ChildProfile, type GrowthLog, type GrowthLogsResponse } from "@/lib/api";

export default function ChildProfilePage() {
  return (
    <AuthGate>
      <ChildProfileScreen />
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
      <header>
        <PageTitle>아이 프로필</PageTitle>
        <p className="text-body-sm text-ink-muted mt-2">
          기본 정보와 키·몸무게, 알레르기를 여기서 관리해요.
        </p>
      </header>

      <IdentitySection childId={childId} query={profile} />

      <GrowthSection childId={childId} query={growth} />

      <SafetySection childId={childId} />
    </Screen>
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
  const [editing, setEditing] = useState(false);

  return (
    <Section
      title="기본 정보"
      // 🚨 **설명 줄을 두지 않는다.** "아이를 부르는 말과 생일이에요" 는 바로 아래 카드가
      //    별명 · 생일 · 성별을 글자로 다 보여주는 것을 한 번 더 말할 뿐이었다 —
      //    같은 화면에서 같은 사실이 두 번 서는 자리다 (머리의 아이 이름을 뺀 것과 같은 이유).
      // 🚨 고치는 것은 드문 일이라 **행동으로** 둔다 — 입력칸을 늘 펼쳐 두지 않는다
      //    (`ChildIdentityCard` 머리말). 다른 두 구역과 같은 머리줄 문법이다.
      action={
        query.data ? (
          <SectionAction label="기본 정보 고치기" icon={Pencil} onClick={() => setEditing(true)} />
        ) : undefined
      }
    >
      {query.isPending ? (
        <SkeletonBlock label="아이 정보를 불러오는 중" />
      ) : query.isError ? (
        <SectionError what="아이 정보를 불러오지 못했어요" onRetry={() => void query.refetch()} />
      ) : (
        <>
          <ChildIdentityCard profile={query.data} />
          {/* 🚨 열 때마다 새로 만든다 (`key`) — 폼이 `useState` 로 값을 들고 있어서 같은
              인스턴스를 재사용하면 지난번에 고치다 만 값이 남는다. */}
          {editing ? (
            <ChildIdentitySheet
              key={query.data.id}
              open
              onClose={() => setEditing(false)}
              childId={childId}
              profile={query.data}
              earliestBirthDate={EARLIEST_BIRTH_DATE}
            />
          ) : null}
        </>
      )}
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
      title="키 · 몸무게"
      description="가장 최근에 잰 것만 보여줘요. 눌러서 지난 기록과 변화를 볼 수 있어요."
      // 🚨 불러오기 전과 0 건일 때는 넘기지 않는다 — 앞은 "아직 모름" 이라 0 을 그리면
      //    거짓말이고, 뒤는 바로 아래 `EmptyState` 가 같은 숫자를 이미 말한다.
      count={logs.length > 0 ? logs.length : undefined}
      action={
        // 🚨 승인 게이트가 아니다 — 잘못 적으면 그 줄을 지우면 된다 (최상위 §2).
        <SectionAction label="키·몸무게 새로 적기" onClick={() => setAdding(true)} />
      }
    >
      {query.isPending ? (
        <SkeletonBlock label="측정 기록을 불러오는 중" />
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

      <GrowthSheet open={adding} onClose={() => setAdding(false)} childId={childId} />
    </Section>
  );
}

/* ── ㉢ 알레르기 · 건강 (🚨 승인 게이트 ㉡) ──────────────────────────── */
