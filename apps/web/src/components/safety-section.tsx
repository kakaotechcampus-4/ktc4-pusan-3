"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Shield } from "lucide-react";
import { useRef, useState } from "react";

import { ConsentRequiredCard } from "@/components/consent-required-card";
import { HealthSafetyList, HealthSafetySheet } from "@/components/health-safety-list";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { EmptyState } from "@/components/ui/empty-state";
import { Section, SectionAction, SectionError } from "@/components/ui/section";
import { SkeletonBlock } from "@/components/ui/skeleton";
import { useSafetyScanDraftStore } from "@/stores/safety-scan-draft";
import { api, isApiError, qk, type HealthSafety, type HealthSafetyListResponse } from "@/lib/api";

/**
 * 알레르기 · 건강 구역. **11 아이 프로필과 02 아이 정보가 같은 것을 쓴다.**
 *
 * 🚨 **여기가 알레르기의 유일한 쓰기 경로다** (승인 게이트 ㉡ · NF-03). 02 는 한동안
 *    "없음 / 있음 · 입력 / 잘 모르겠어요" 칩과 쉼표 입력으로 `POST /children/{cid}/onboarding`
 *    본문에 알레르기를 실어 보냈는데, 그러면 **게이트를 지나지 않는 두 번째 쓰기 경로**가
 *    생긴다. 같은 구역을 쓰면 등록·수정·회수가 한 곳에 모이고, 첫날 적은 것과 나중에 적은
 *    것이 같은 모양으로 남는다.
 *
 * ⚠️ **그래서 `safety_status`(없음 / 잘 모르겠어요)를 물을 자리가 사라졌다.** 목록이 0건인
 *    것만으로는 "확인했고 없다" 와 "아직 모른다" 를 가를 수 없는데, 계약서 §05 는 후자를
 *    `guards.safety_unknown` 으로 받아 Food Agent 실행 자체를 막는다 (최상위 §2).
 *    11 프로필에도 원래 없던 구멍이고, 02 가 같은 구역을 쓰면서 그대로 따라왔다 —
 *    **어디서 그 선언을 받을지는 팀 결정이다** (#96 에 적어 뒀다).
 */
export function SafetySection({ childId }: { childId: string }) {
  const router = useRouter();

  /**
   * 🚨 **목록을 이 컴포넌트가 직접 불러온다.** 부르는 화면이 둘(11 · 02)이 되면서, 바깥에서
   *    넘겨 주게 두면 두 화면이 같은 쿼리를 각자 세우고 한쪽만 키를 틀리는 길이 생긴다.
   *    같은 `qk.healthSafety(childId)` 라 이미 캐시가 하나다.
   */
  const query = useQuery({
    queryKey: qk.healthSafety(childId),
    queryFn: () => api.get<HealthSafetyListResponse>(`/children/${childId}/health-safety`),
  });

  /**
   * 더하는 길이 둘이다 — 직접 적기와 검사지 사진. 🚨 **시트를 겹쳐 열지 않는다**
   * (`<dialog>` 두 장이 포개지면 포커스 트랩이 둘이 된다). 한 번에 하나만 열리게
   * 상태 하나로 가른다.
   *
   * 🚨 **검사지는 시트가 아니라 화면(11-2)으로 나간다.** 한 장에서 열 줄 넘게 나오는데 시트
   *    높이 안에서는 배너·사진·목록·승인 버튼이 서로 자리를 뺏는다 (사진을 96px 까지 줄여야
   *    목록이 보였다). 그래서 여기 남는 `mode` 는 둘뿐이다.
   */
  const [mode, setMode] = useState<null | "choose" | "manual">(null);
  /** 고치는 중인 기록. 있으면 같은 승인 게이트 시트가 고치기로 열린다. */
  const [editing, setEditing] = useState<HealthSafety | null>(null);

  /**
   * 🚨 **파일 입력은 사용자 제스처에서 열어야 한다.** 시트를 닫고 나서 열려고 하면 제스처가
   *    이미 소비돼 브라우저가 막는다 — 고르는 줄에서 바로 `click()` 한다.
   *    `accept="image/*"` 하나면 OS 가 촬영·앨범을 알아서 같이 준다.
   */
  const photoInputRef = useRef<HTMLInputElement>(null);

  const items = query.data?.items ?? [];

  return (
    <Section
      title="알레르기 · 건강"
      description="보호자가 직접 확인한 것만 저장돼요. AI 는 이 목록을 만들지도 고치지도 못해요."
      count={items.length > 0 ? items.length : undefined}
      action={<SectionAction label="알레르기·건강 기록 추가" onClick={() => setMode("choose")} />}
    >
      {query.isPending ? (
        <SkeletonBlock label="안전 정보를 불러오는 중" />
      ) : isApiError(query.error, "consent_required") ? (
        // 🚨 일반 실패로 뭉뚱그리지 않는다 — 다시 시도해도 같은 403 이다.
        <ConsentRequiredCard
          childId={childId}
          error={query.error}
          what="안전 정보를 불러올 수 없고"
        />
      ) : query.isError ? (
        <SectionError what="안전 정보를 불러오지 못했어요" onRetry={() => void query.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Shield}
          title="등록된 것이 아직 없어요"
          description="알레르기나 지병이 확인되면 여기에 적어 두세요. 식사 제안이 이 목록을 보고 걸러요."
          count={0}
          countLabel="등록된 항목"
        />
      ) : (
        <HealthSafetyList childId={childId} items={items} onEdit={setEditing} />
      )}

      {/* 🚨 화면에 보이지 않지만 자리는 여기다 — 고르는 시트가 닫힌 뒤에도 같은 입력을 쓴다. */}
      <input
        ref={photoInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0] ?? null;
          // 같은 파일을 다시 고를 수 있게 값을 비운다 (안 비우면 onChange 가 안 온다).
          e.target.value = "";
          if (!file) return;
          // 🚨 **파일은 URL 로 못 넘긴다.** 화면 밖 스토어에 한 칸 두고 넘긴다 —
          //    objectURL 을 만드는 것도 해제하는 것도 거기 한 곳이 맡는다.
          useSafetyScanDraftStore.getState().putPhoto(childId, file);
          setMode(null);
          router.push(`/child/${childId}/profile/safety-scan`);
        }}
      />

      <AddSafetySheet
        open={mode === "choose"}
        onClose={() => setMode(null)}
        onManual={() => setMode("manual")}
        onPhoto={() => photoInputRef.current?.click()}
      />

      <HealthSafetySheet open={mode === "manual"} onClose={() => setMode(null)} childId={childId} />

      {/* 🚨 고치는 기록이 바뀌면 시트를 새로 만든다 (`key`) — 폼이 `useState` 로 값을 들고 있어서
          같은 인스턴스를 재사용하면 앞 기록의 입력이 남는다. */}
      {editing ? (
        <HealthSafetySheet
          key={editing.id}
          open
          onClose={() => setEditing(null)}
          childId={childId}
          item={editing}
        />
      ) : null}
    </Section>
  );
}

/**
 * 더하는 길 고르기. 🚨 **승인 게이트가 아니다** — 아직 아무것도 저장하지 않으므로
 * `caution` 도 `btn-approve` 도 쓰지 않고, 스크림 탭으로 닫힌다.
 *
 * 🚨 **직접 적기가 위다.** 사진이 더 편해 보여도, 검사지가 없는 보호자가 대부분이고
 *    이 제품의 기본 경로는 보호자가 직접 확인한 것을 적는 것이다 (최상위 §2).
 */
function AddSafetySheet({
  open,
  onClose,
  onManual,
  onPhoto,
}: {
  open: boolean;
  onClose: () => void;
  onManual: () => void;
  onPhoto: () => void;
}) {
  return (
    <BottomSheet open={open} onClose={onClose} title="알레르기 · 건강 기록 추가">
      <div className="flex flex-col gap-2">
        <AddSafetyOption
          title="직접 적기"
          description="확인한 것을 한 건씩 적어요."
          onClick={onManual}
        />
        <AddSafetyOption
          title="검사지 사진에서 가져오기"
          description="알레르기 검사지를 찍으면 적힌 것을 옮겨 와요. 확인하고 한 번에 등록해요."
          onClick={onPhoto}
        />
      </div>
    </BottomSheet>
  );
}

/**
 * 고르는 줄. 🚨 **`Button` 이 아니다** — 설명이 두 줄까지 늘어나고 왼쪽 정렬이라 버튼 사양
 * (가운데 정렬 · 한 줄)과 맞지 않는다. 시트 안의 목록 줄이라 `line` 1px 로 갈린다.
 */
function AddSafetyOption({
  title,
  description,
  onClick,
}: {
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-card border-line bg-surface ease-standard active:bg-surface-muted hover:bg-surface-muted min-h-touch flex flex-col items-start gap-1 border px-4 py-3 text-left transition-colors duration-120"
    >
      <span className="text-body text-ink">{title}</span>
      <span className="text-body-sm text-ink-muted">{description}</span>
    </button>
  );
}
