"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Plus, Ruler } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ChildNav } from "@/components/child-nav";
import { GrowthChart } from "@/components/growth-chart";
import { GrowthLogList } from "@/components/growth-log-list";
import { GrowthSheet } from "@/components/growth-sheet";
import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { CountChip } from "@/components/ui/chip";
import { EmptyState } from "@/components/ui/empty-state";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { useChildId } from "@/hooks/use-child-id";
import { api, qk, type GrowthLog, type GrowthLogsResponse } from "@/lib/api";

/**
 * 11-1 키 · 몸무게 상세 — 이 아이 안에서 어떻게 움직였고, 언제 무엇을 쟀는지.
 *
 * 🚨 **화면의 말은 "키 · 몸무게" 고 코드·주소의 이름은 `growth` 다** (11 프로필과 같은 처리).
 *    화면 문구만 바꾸고 라우트·쿼리 키·타입을 따라 바꾸지 않는다 — 그쪽은 계약서를 따른다.
 *
 * 🚨 **11 프로필에서 갈라져 나온 화면이다.** 프로필은 가장 최근 한 줄만 세우고
 *    (`GrowthLatestCard`), 쌓인 것과 그래프는 여기가 진다. 이유는 그 컴포넌트 머리말에 있다.
 *
 * 🚨 **그래프가 이 화면의 주인공이 아니다.** 목록이 정본이고 그래프는 같은 값을 한 번 더
 *    보여주는 것이다 — 그래서 그래프가 보조기술에 안 보이고(`aria-hidden`), 없어도 화면이
 *    성립한다. 반대로 **목록을 지우면 그래프의 접근성 근거가 사라진다** (`GrowthChart` 머리말).
 *
 * 🚨 **하단 네비를 그대로 둔다.** 05 제안·04 저장 결과와 달리 이 화면은 흐름 중이 아니라
 *    **가는 곳**이다 (`ChildNav` 머리말) — 고르다 마는 것이 없어서 다른 데로 새는 길을
 *    만들어도 끝나지 않는 화면이 생기지 않는다. "아이" 칸이 켜진 채다.
 */
export default function GrowthDetailPage() {
  return (
    <AuthGate>
      <GrowthDetailScreen />
    </AuthGate>
  );
}

function GrowthDetailScreen() {
  const childId = useChildId();
  const queryClient = useQueryClient();

  const [adding, setAdding] = useState(false);
  /** 고치는 중인 줄. 있으면 **같은 시트**가 고치기로 열린다 (`GrowthSheet` 머리말). */
  const [editing, setEditing] = useState<GrowthLog | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  /** 🚨 프로필 화면과 **같은 쿼리 키**다 — 여기서 지운 줄이 뒤로 갔을 때 그대로 남으면 안 된다. */
  const growth = useQuery({
    queryKey: qk.growth(childId),
    queryFn: () => api.get<GrowthLogsResponse>(`/children/${childId}/growth`),
  });

  const remove = useMutation({
    mutationFn: (log: GrowthLog) => {
      setDeletingId(log.id);
      return api.delete<void>(`/children/${childId}/growth/${log.id}`);
    },
    onSettled: async () => {
      setDeletingId(null);
      await queryClient.invalidateQueries({ queryKey: qk.growth(childId) });
    },
  });

  const logs = growth.data?.items ?? [];

  return (
    <Screen className="gap-6" nav={<ChildNav active="profile" />}>
      <header className="flex flex-col gap-3">
        {/* 🚨 **뒤로 가는 길을 화살표 하나로 두지 않는다.** 웹뷰의 기기 뒤로가기가 있긴 하지만
            (`apps/mobile/App.tsx`) 브라우저로 직접 들어온 부모에게는 그 길이 없고, 화살표만
            있으면 어디로 돌아가는지도 안 보인다 — 돌아갈 화면의 이름을 글자로 함께 세운다.
            🚨 `router.back()` 이 아니라 `Link` 다: 알림이나 주소로 이 화면에 바로 들어오면
            히스토리에 프로필이 없어서 back 이 앱 밖으로 나간다. */}
        <Link
          href={`/child/${childId}/profile`}
          className="text-body-sm text-ink-muted ease-standard active:text-ink hover:text-ink min-h-touch -my-2 -ml-1 flex w-fit items-center gap-1 py-2 pr-2 pl-1 transition-colors duration-120"
        >
          <ChevronLeft aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
          아이 프로필
        </Link>

        <div>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-baseline gap-2">
              <PageTitle>키 · 몸무게</PageTitle>
              {/* 🚨 0 건이면 넘기지 않는다 — 아래 `EmptyState` 가 같은 숫자를 훨씬 크게 말한다. */}
              {logs.length > 0 ? <CountChip>{logs.length}건</CountChip> : null}
            </div>
            {/* 🚨 승인 게이트가 아니다 — 잘못 적으면 그 줄을 지우면 된다 (최상위 §2).
                프로필 구역 머리줄과 **같은 아이콘 버튼**이다. */}
            <IconButton
              label="키·몸무게 새로 적기"
              onClick={() => setAdding(true)}
              className="-my-1"
            >
              <Plus aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
            </IconButton>
          </div>
          {/* 🚨 **또래·백분위를 안 본다는 것을 화면이 직접 말한다.** 그래프가 있는 화면에서
              이 문장이 빠지면, 부모는 이 선이 어딘가의 기준과 견준 결과라고 읽는다. */}
          <p className="text-body-sm text-ink-muted mt-2">
            잰 날 그대로 쌓아 둬요. 또래와 견주거나 백분위를 매기지 않아요.
          </p>
        </div>
      </header>

      {growth.isPending ? (
        <SkeletonBlock label="측정 기록을 불러오는 중" />
      ) : growth.isError ? (
        /* 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). */
        <CardFailed>
          <p className="text-body text-ink">측정 기록을 불러오지 못했어요</p>
          <Button
            variant="secondary"
            size="compact"
            onClick={() => void growth.refetch()}
            className="mt-3"
          >
            다시 불러오기
          </Button>
        </CardFailed>
      ) : logs.length === 0 ? (
        // 🚨 빈 상태를 사과문으로 쓰지 않는다. 쌓인 건수를 그대로 보여준다 (`EmptyState`).
        <EmptyState
          icon={Ruler}
          title="아직 잰 기록이 없어요"
          description="어린이집 신체검사나 병원에서 잰 날, 여기에 적어 두면 돼요."
          count={0}
          countLabel="적어 둔 기록"
        />
      ) : (
        <>
          <section className="flex flex-col gap-3">
            <h2 className="text-label text-brand">변화</h2>
            <GrowthChart logs={logs} />
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="text-label text-brand">기록</h2>
            {/* 🚨 **목록이 화면을 밀지 않게 목록 안에서 스크롤한다.** 잰 기록은 해가 갈수록
                쌓이기만 하는데, 다 펼치면 그래프가 화면 위로 사라지고 "새로 적기" 는 스크롤
                끝까지 가야 닿는다. 🚨 그래도 **목록을 접거나 몇 건만 보여주지 않는다** — 쌓인
                것을 그대로 보여주는 것이 이 화면이 하는 일이다 (최상위 CLAUDE.md §2). */}
            <GrowthLogList
              logs={logs}
              onEdit={setEditing}
              onDelete={(log) => remove.mutate(log)}
              deletingId={deletingId}
              scrollable
            />
            {remove.isError ? (
              <p role="status" className="text-body-sm text-ink-muted">
                지우지 못했어요. 그 줄은 아직 목록에 있으니 다시 눌러 주세요.
              </p>
            ) : null}
          </section>
        </>
      )}

      <GrowthSheet open={adding} onClose={() => setAdding(false)} childId={childId} />

      {/* 🚨 고치는 줄이 바뀌면 시트를 새로 만든다 (`key`) — 폼이 `useState` 로 값을 들고 있어서
          같은 인스턴스를 재사용하면 앞 줄의 값이 남는다 (11 프로필의 다른 시트들과 같다). */}
      {editing ? (
        <GrowthSheet
          key={editing.id}
          open
          onClose={() => setEditing(null)}
          childId={childId}
          log={editing}
        />
      ) : null}
    </Screen>
  );
}
