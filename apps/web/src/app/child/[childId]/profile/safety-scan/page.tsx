"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { SafetyScanReview } from "@/components/safety-scan-review";
import { toRow, type ScanRow } from "@/components/safety-scan-fields";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { PageTitle } from "@/components/ui/page-title";
import { Screen } from "@/components/ui/screen";
import { Spinner } from "@/components/ui/spinner";
import { useChildId } from "@/hooks/use-child-id";
import { useSafetyScanDraftStore, type PendingScanPhoto } from "@/stores/safety-scan-draft";
import {
  addHealthSafety,
  api,
  isApiError,
  newIdempotencyKey,
  qk,
  type ApiError,
  type HealthSafetyListResponse,
  type IdempotencyKey,
  type SafetyScanResponse,
} from "@/lib/api";

/**
 * 11-2 알레르기 검사지에서 가져오기 (`/child/[childId]/profile/safety-scan`).
 *
 * 🚨 **11 프로필의 바텀시트에서 나온 화면이다.** 검사지 한 장에서 열 줄 넘게 나오는데 시트
 *    높이 안에서는 배너·사진·목록·승인 버튼이 서로 자리를 뺏는다 (사진을 96px 까지 줄여야
 *    목록이 보였다). 08 사진으로 적기와 같은 모양으로 화면을 내줬다 — 무엇을 왜 가르는지는
 *    `SafetyScanReview` 머리말에 있다.
 *
 * 🚨 **여기는 승인 게이트 ㉡ 다.** 08 사진과 달리 등록은 되돌릴 수 없어서 `banner-caution` 과
 *    `btn-approve` 를 쓴다. 게이트를 늘린 것이 아니라 **있던 게이트가 시트에서 화면으로
 *    옮겨온 것**이다 (최상위 CLAUDE.md §2 — 늘리지도 줄이지도 않는다).
 *
 * 🚨 **하단 네비(`ChildNav`)를 붙이지 않는다.** 흐름 중인 화면이라 승인하다 마는 길을 만들지
 *    않는다 (apps/web/CLAUDE.md §3 — 04·05·08 과 같은 이유). 11-1 키·몸무게는 **가는 곳**이라
 *    네비를 달았는데, 여기는 그 반대다.
 *
 * 🚨 **승인 전에는 아무것도 저장되지 않는다.** 읽기(`/scan`)는 저장하지 않는다 — 그래서 화면을
 *    그냥 나가도 남는 것이 없고, "나가면 사라져요" 확인 창을 만들지 않는다.
 *
 * 🚨 **검사지 원본은 이 화면 밖으로 나가지 않는다.** 미리보기는 `URL.createObjectURL` 이고
 *    만들고 해제하는 자리는 `stores/safety-scan-draft.ts` 한 곳이다.
 */
export default function SafetyScanPage() {
  return (
    <AuthGate>
      <SafetyScanScreen />
    </AuthGate>
  );
}

function SafetyScanScreen() {
  const childId = useChildId();
  const router = useRouter();
  const queryClient = useQueryClient();

  /**
   * 보호자가 프로필에서 고른 검사지.
   *
   * 🚨 **effect 가 아니라 초기값으로 받는다.** effect 안에서 `setState` 를 하면 이 화면의 첫
   *    프레임이 **사진 없는 화면**이 되어 "고르는 칸" 이 한 번 깜빡인다.
   * 🚨 `peek` 은 **순수한 읽기**라 초기값으로 안전하다. objectURL 은 스토어가 이벤트 안에서
   *    이미 만들어 뒀다 (렌더 중에 만들면 StrictMode 에서 샌다).
   * 🚨 **아이가 맞을 때만 온다** — 아이를 바꾼 뒤 들어오면 다른 아이의 검사지가 이 아이
   *    알레르기로 등록된다.
   */
  const [photo] = useState<PendingScanPhoto | null>(() =>
    useSafetyScanDraftStore.getState().peek(childId),
  );

  const [step, setStep] = useState<"reading" | "review">("reading");
  const [scan, setScan] = useState<SafetyScanResponse | null>(null);
  const [rows, setRows] = useState<ScanRow[]>([]);
  const [readError, setReadError] = useState<ApiError | "failed" | null>(null);
  const [saving, setSaving] = useState(false);

  /** 🚨 줄마다 키 하나. 재시도는 같은 키, 새 줄은 새 키다 (아래 `approve`). */
  const keys = useRef(new Map<string, IdempotencyKey>());
  const started = useRef(false);

  /**
   * 이미 등록된 이름. 후보에서 걸러 내는 데 쓴다 — 승인하고 나서 409 를 보는 일이 없게.
   * 🚨 **프로필과 같은 쿼리 키다.** 여기서 등록한 것이 뒤로 갔을 때 목록에 있어야 한다.
   */
  const safety = useQuery({
    queryKey: qk.healthSafety(childId),
    queryFn: () => api.get<HealthSafetyListResponse>(`/children/${childId}/health-safety`),
  });

  /**
   * 🚨 **이미 등록된 줄을 렌더마다 다시 판단한다.** 등록 목록은 검사지를 읽는 동안에도
   *    도착하고, 이 화면에서 한 줄 등록할 때마다 바뀐다 (`invalidateQueries`). 읽는 순간의
   *    스냅샷으로 굳히면 아직 안 온 목록 = 빈 목록이라, 이미 있는 항목이 고를 수 있는 줄로
   *    서고 승인하고 나서 409 를 본다.
   */
  const decorated = useMemo(() => {
    const registered = new Set(safety.data?.items.map((item) => item.label) ?? []);
    return rows.map((row) => ({
      ...row,
      alreadyRegistered: row.label !== "" && registered.has(row.label),
    }));
  }, [rows, safety.data]);

  /**
   * 🚨 **읽기는 한 번만 시작한다.** StrictMode 가 effect 를 두 번 돌리는데 두 번 올리면 사진이
   *    두 번 나가고 후보가 두 벌이 된다 (로그인 1회용 코드와 같은 가드다).
   * 🚨 **cleanup 에서 결과를 버리지 않는다.** `cancelled` 플래그를 두면 StrictMode 의 첫 cleanup
   *    이 먼저 돌아서 **방금 받은 후보를 그대로 버린다** — 가드가 있어서 두 번째 실행은 아무것도
   *    새로 시작하지 않으므로, 버리면 화면이 영영 "읽는 중" 에 멈춘다.
   * 🚨 **넘겨받은 사진은 스토어에서 뗀다** (`release`). 남겨 두면 이 화면을 다시 열 때 지난번
   *    검사지가 저절로 올라간다 — 보호자가 고른 적 없는 사진이 분석으로 들어가는 경로다.
   */
  useEffect(() => {
    if (!photo || started.current) return;
    started.current = true;
    useSafetyScanDraftStore.getState().release();

    void (async () => {
      try {
        const form = new FormData();
        form.append("photo", photo.file);
        const result = await api.post<SafetyScanResponse>(
          `/children/${childId}/health-safety/scan`,
          form,
        );
        setScan(result);
        setRows(result.candidates.map(toRow));
      } catch (error) {
        // 🚨 일반 실패로 뭉뚱그리지 않는다 — 동의가 없으면 다시 눌러도 같은 403 이다.
        setReadError(isApiError(error, "consent_required") ? error : "failed");
      } finally {
        setStep("review");
      }
    })();
  }, [photo, childId]);

  function patch(id: string, next: Partial<ScanRow>) {
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, ...next } : row)));
  }

  /** 이 줄은 등록하지 않는다. 🚨 저장된 것이 없으니 지우는 것이 아니라 **안 싣는** 것이다. */
  function drop(id: string) {
    setRows((prev) => prev.filter((row) => row.id !== id));
  }

  function leave() {
    router.push(`/child/${childId}/profile`);
  }

  /**
   * 🚨 **한 줄씩 부른다.** 알레르기의 쓰기 경로는 하나뿐이라(최상위 §2) 묶음 엔드포인트를
   *    만들지 않았다. 실패한 줄은 `failed` 로 남고 성공한 줄은 `done` 이 되어, 다시 누르면
   *    **실패한 것만** 같은 키로 재시도한다.
   * 🚨 **재료 한 건이 사용자 동작 하나라 키도 줄마다 하나다** (`ApprovalSheet` 와 같은 처리).
   *    하나로 묶으면 두 번째 줄이 "같은 키 · 다른 요청" 이라 422 다.
   */
  async function approve() {
    setSaving(true);
    const picked = decorated.filter(
      (row) => row.checked && row.status !== "done" && !row.alreadyRegistered,
    );

    for (const row of picked) {
      patch(row.id, { status: "saving" });
      const key = keys.current.get(row.id) ?? newIdempotencyKey();
      keys.current.set(row.id, key);
      try {
        await addHealthSafety(
          childId,
          {
            type: "allergy",
            label: row.label.trim(),
            category: row.category,
            ...(row.severity ? { severity: row.severity } : {}),
            ...(row.reactions.length > 0 ? { reactions: row.reactions } : {}),
            notes: "검사지 사진에서 옮겨 적고 보호자가 확인",
          },
          key,
        );
        patch(row.id, { status: "done", checked: false });
      } catch {
        patch(row.id, { status: "failed" });
      }
    }
    setSaving(false);
    // 안전 정보 하나가 Food Agent 의 실행 조건까지 바꾼다. 아이 스코프를 통째로 무효화한다.
    await queryClient.invalidateQueries({ queryKey: qk.child(childId) });
  }

  const failedCount = rows.filter((row) => row.status === "failed").length;

  return (
    <Screen className="gap-6">
      <header className="flex flex-col gap-3">
        {/* 🚨 **뒤로 가는 길을 화살표 하나로 두지 않는다** (11-1 과 같은 처리). `router.back()` 이
            아니라 `Link` 다 — 주소로 바로 들어오면 히스토리에 프로필이 없어 앱 밖으로 나간다. */}
        <Link
          href={`/child/${childId}/profile`}
          className="text-body-sm text-ink-muted ease-standard active:text-ink hover:text-ink min-h-touch -my-2 -ml-1 flex w-fit items-center gap-1 py-2 pr-2 pl-1 transition-colors duration-120"
        >
          <ChevronLeft aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} />
          아이 프로필
        </Link>

        <div>
          <PageTitle>검사지에서 가져오기</PageTitle>
          <p className="text-body-sm text-ink-muted mt-2">
            검사지에 적힌 것만 옮겨 와요. 확인하고 승인해야 등록돼요.
          </p>
        </div>
      </header>

      {!photo ? (
        <NoPhoto childId={childId} />
      ) : step === "reading" ? (
        <Reading previewUrl={photo.url} />
      ) : readError !== null && readError !== "failed" ? (
        // 🚨 동의 없음을 일반 실패로 뭉뚱그리지 않는다 — 다시 시도해도 같은 403 이고,
        //    부모가 할 수 있는 일은 동의 화면으로 가는 것뿐이다.
        <ConsentRequiredCard
          childId={childId}
          error={readError}
          what="검사지를 읽어 드릴 수 없고"
        />
      ) : readError === "failed" ? (
        <ReadFailed childId={childId} />
      ) : (
        <SafetyScanReview
          rows={decorated}
          onPatch={patch}
          onDrop={drop}
          unreadableCount={scan?.unreadable_count ?? 0}
          previewUrl={photo.url}
          onApprove={() => void approve()}
          saving={saving}
          failedCount={failedCount}
          onLeave={leave}
        />
      )}
    </Screen>
  );
}

/* ── 사진 없이 이 주소로 바로 들어온 경우 ────────────────────────────── */

/**
 * 🚨 **주된 경로가 아니다.** 보통은 프로필의 시트에서 고른 검사지를 들고 들어와서 이 칸을
 *    보지 않는다. 직접 링크로 들어왔거나 새로고침해서 들고 온 파일이 사라진 경우에만 선다.
 *
 * 🚨 **여기서 파일 입력을 열지 않는다.** 고르는 자리는 프로필의 시트 하나다 — 같은 일을 두 곳에
 *    두면 한쪽만 규칙이 바뀐다. 되돌아갈 길만 준다.
 */
function NoPhoto({ childId }: { childId: string }) {
  const router = useRouter();

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-title text-ink">고른 검사지가 없어요</h2>
        <p className="text-body-sm text-ink-muted mt-2">
          새로고침하면 고른 사진이 사라져요. 저장된 것은 하나도 없으니 프로필에서 다시 골라 주세요.
        </p>
      </div>
      <div>
        <Button onClick={() => router.push(`/child/${childId}/profile`)}>
          <Camera aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
          프로필에서 다시 고르기
        </Button>
      </div>
    </div>
  );
}

/* ── 읽는 중 ──────────────────────────────────────────────────────────── */

function Reading({ previewUrl }: { previewUrl: string }) {
  return (
    <div className="flex flex-col items-center gap-4 py-6">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={previewUrl}
        alt="읽고 있는 검사지 사진"
        className="rounded-card border-line bg-surface-muted h-56 w-full border object-contain"
      />
      <div className="flex items-center gap-2">
        <Spinner />
        {/* 🚨 `prefers-reduced-motion` 에서는 스피너가 숨는다 — 문구가 상태를 대신 말한다. */}
        <p role="status" className="text-body text-ink">
          검사지를 읽고 있어요
        </p>
      </div>
      <p className="text-body-sm text-ink-muted text-center">
        적힌 것을 옮겨 오기만 해요. 저장은 확인하신 뒤에 해요.
      </p>
    </div>
  );
}

/** 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). 기본값으로 대체하지도 않는다. */
function ReadFailed({ childId }: { childId: string }) {
  const router = useRouter();

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-title text-ink">검사지를 읽지 못했어요</h2>
        <p className="text-body-sm text-ink-muted mt-2">
          잘못 등록하지 않으려고 아무것도 저장하지 않았어요. 프로필에서 직접 적어 등록하거나, 글자가
          더 잘 보이는 사진으로 다시 고를 수 있어요.
        </p>
      </div>
      <div>
        <Button onClick={() => router.push(`/child/${childId}/profile`)}>프로필로 돌아가기</Button>
      </div>
    </div>
  );
}
