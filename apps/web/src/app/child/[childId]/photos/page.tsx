"use client";

import { useMutation } from "@tanstack/react-query";
import { Camera, ChevronLeft } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { PhotoReview } from "@/components/photo-review";
import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { PhotoCard, PhotoSlotButton } from "@/components/ui/photo-card";
import { ProgressSteps } from "@/components/ui/progress-steps";
import { Screen } from "@/components/ui/screen";
import { useChildId } from "@/hooks/use-child-id";
import { useRunStream } from "@/hooks/use-run-stream";
import {
  commitPhotoRun,
  isApiError,
  photoFormData,
  reanalyzePhotoRun,
  uploadPhoto,
  type PhotoCommitRequest,
  type PhotoCommitResponse,
} from "@/lib/api";
import { useIdempotencyKey } from "@/lib/api/use-idempotency-key";
import { formatDay, parseISODate, toISODate } from "@/lib/format";

/**
 * 08 사진으로 적기 (`/child/[childId]/photos?date=YYYY-MM-DD`).
 *
 * 🚨 **승인 전에는 아무것도 저장되지 않는다.** `POST /children/{cid}/photos` 는 읽기만 하고,
 *    저장은 `POST /photo-runs/{rid}/commit` 하나다. 그래서 화면을 그냥 나가면 남는 것이 없다 —
 *    "나가면 사라져요" 같은 확인 창을 만들지 않는다 (승인 게이트는 2곳뿐이다 · CLAUDE.md §2).
 *
 * 🚨 **여기는 그 승인 게이트가 아니다.** 문서 lane 이 만드는 `event` 는 `draft` 고, 캘린더에
 *    확정하는 것은 09 화면의 `POST /events/{eid}/confirm` 이다. `btn-approve` · `caution` 금지.
 *
 * 🚨 **하단 네비(`ChildNav`)를 붙이지 않는다.** 흐름 중인 화면이라 고르는 도중에 새는 길을
 *    만들지 않는다 (apps/web/CLAUDE.md §3 — 04·05 와 같은 이유).
 *
 * 🚨 **사진 원본은 이 컴포넌트 밖으로 나가지 않는다.** 미리보기는 `URL.createObjectURL` 이고
 *    언마운트·교체 때 반드시 `revokeObjectURL` 한다 — 안 그러면 탭이 사는 동안 사진이 메모리에
 *    남는다.
 */
export default function PhotosPage() {
  return (
    // 🚨 `useSearchParams()` 는 Suspense 경계 안에 있어야 한다 (05·07·09 와 같은 이유).
    <Suspense fallback={null}>
      <AuthGate>
        <PhotosScreen />
      </AuthGate>
    </Suspense>
  );
}

function PhotosScreen() {
  const childId = useChildId();
  const router = useRouter();
  const searchParams = useSearchParams();

  /**
   * 이 사진을 어느 날에 남기는지. 캘린더에서 들어오면 그날, 홈에서 들어오면 오늘이다.
   * 🚨 잘못된 값이면 오늘로 떨어진다 — `parseISODate` 가 `null` 을 주고 판단은 여기서 한다 (09 와 같다).
   */
  const dateParam = searchParams.get("date");
  const date = toISODate((dateParam ? parseISODate(dateParam) : null) ?? new Date());

  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  const [saved, setSaved] = useState<PhotoCommitResponse | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const run = useRunStream(childId);
  const [runId, setRunId] = useState<string | null>(null);

  /**
   * 🚨 재시도할 때 키를 새로 만들지 않는다 — 같은 사진을 다시 올리는 것이 재시도다.
   *    **다른 사진으로 바꿀 때만** 돌린다(`resetToPicker`). 같은 키에 다른 본문을 보내면
   *    422 idempotency_key_reuse 다 (03 홈의 `closeRun` 과 같은 규칙).
   */
  const idempotencyKey = useIdempotencyKey();

  // 🚨 objectURL 은 명시적으로 해제한다. 화면을 떠날 때 마지막 것까지 반드시 지운다.
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const upload = useMutation({
    mutationFn: (picked: File) =>
      uploadPhoto(childId, photoFormData(picked, date), idempotencyKey.current()),
    onSuccess: (res) => {
      setRunId(res.run_id);
      run.start(res.run_id);
    },
  });

  const commit = useMutation({
    mutationFn: (body: PhotoCommitRequest) => {
      if (!runId) throw new Error("저장할 사진이 없어요.");
      return commitPhotoRun(runId, body);
    },
    // 🚨 낙관적 업데이트를 하지 않는다. 응답을 받은 뒤에 "저장했어요" 를 그린다.
    onSuccess: (res) => setSaved(res),
  });

  const reanalyze = useMutation({
    mutationFn: (hint: string) => {
      if (!runId) throw new Error("다시 읽을 사진이 없어요.");
      return reanalyzePhotoRun(runId, { hint_text: hint });
    },
    onSuccess: (res) => {
      // 새 run 이다. 사진은 그대로 두고 읽기만 다시 한다.
      setRunId(res.run_id);
      run.start(res.run_id);
    },
  });

  /**
   * 🚨 **고르는 것과 보내는 것이 한 동작이다.** 고르기만 하고 멈추면 화면에 "이제 보내기" 버튼이
   *    하나 더 생기는데, 그 버튼은 아무것도 승인하지 않으면서 확인처럼 보인다 —
   *    이 화면의 확인은 **읽어낸 것을 보고 저장할 때** 한 번뿐이다 (CLAUDE.md §2).
   */
  function pickFile(picked: File) {
    setPickError(null);
    setFile(picked);
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(picked);
    });
    upload.mutate(picked);
  }

  /** 사진을 고르는 칸으로 되돌린다. 저장된 것이 없으므로 되돌릴 것도 없다. */
  function resetToPicker() {
    run.reset();
    upload.reset();
    commit.reset();
    reanalyze.reset();
    setRunId(null);
    setSaved(null);
    setFile(null);
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    // 🚨 여기서 돌린다. 다음 요청은 **다른 사진**이라 같은 키로 보내면 422 다.
    idempotencyKey.rotate();
    // 같은 파일을 다시 고를 수 있어야 한다 — `value` 를 비우지 않으면 change 가 안 뜬다.
    if (inputRef.current) inputRef.current.value = "";
  }

  const consentBlocked = isApiError(upload.error, "consent_required") ? upload.error : null;

  return (
    <Screen className="gap-6">
      <header className="flex items-center gap-1">
        <IconButton label="뒤로 가기" onClick={() => router.back()}>
          <ChevronLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
        </IconButton>
        <div className="min-w-0">
          <PageTitle>사진으로 적기</PageTitle>
        </div>
      </header>

      {/* 파일 입력은 화면 밖에 둔다 — 누르는 것은 4:3 칸 안의 버튼이다 (`PhotoSlotButton` 주석). */}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => {
          const picked = e.target.files?.[0];
          if (!picked) return;
          // 🚨 `accept` 는 브라우저가 지켜 주기를 기대하는 값이라 여기서 한 번 더 본다.
          //    사진이 아닌 파일을 올려 보내고 서버 에러로 알게 하지 않는다.
          if (!picked.type.startsWith("image/")) {
            setPickError("사진 파일만 넣을 수 있어요.");
            e.target.value = "";
            return;
          }
          pickFile(picked);
        }}
      />

      {saved ? (
        <SavedResult
          result={saved}
          childId={childId}
          fallbackDate={date}
          previewUrl={previewUrl}
          onAnother={resetToPicker}
        />
      ) : consentBlocked ? (
        <ConsentRequiredCard
          childId={childId}
          error={consentBlocked}
          what="사진을 읽어 드릴 수 없어요."
        />
      ) : !file || !previewUrl ? (
        <Picker date={date} error={pickError} onPick={() => inputRef.current?.click()} />
      ) : /* 🚨 **보내기 실패를 먼저 본다.** 요청이 실패하면 run 은 `idle` 그대로라, 아래
             "아직 시작 전" 과 구분되지 않으면 실패한 화면이 영원히 읽는 중으로 남는다. */
      upload.isError ? (
        <Failed
          title="사진을 보내지 못했어요"
          note="저장된 것은 없어요. 같은 사진을 그대로 다시 보낼 수 있어요."
          onRetry={() => upload.mutate(file)}
          onPickAnother={resetToPicker}
        />
      ) : /* 🚨 `idle` 을 실패로 떨어뜨리지 않는다. 사진을 고른 직후의 한 틱과 요청이 나가기 전이
             여기 걸리는데, 그 상태를 "읽어낼 게 없었어요" 로 그리면 **보내 보지도 않고 실패라고
             말하는 화면**이 된다 (실제로 그렇게 났다). 실패는 스트림이 그렇게 말할 때만이다. */
      upload.isPending || run.state.status === "streaming" || run.state.status === "idle" ? (
        <Reading
          previewUrl={previewUrl}
          index={run.state.step?.index ?? 0}
          total={run.state.step?.total ?? 2}
          label={run.state.step?.label ?? "사진을 열고 있어요"}
        />
      ) : run.state.parsed && run.state.lane ? (
        // 🚨 run 마다 새로 세운다. 다시 읽기(`reanalyze`)는 새 run 이라, 앞 run 에서 고른 항목이
        //    넘어오면 **부모가 확인한 적 없는 것**이 저장 대상으로 남는다.
        <PhotoReview
          key={runId}
          guess={run.state.lane.guess}
          confidence={run.state.lane.confidence}
          parsed={run.state.parsed}
          date={date}
          previewUrl={previewUrl}
          onCommit={(body) => commit.mutate(body)}
          committing={commit.isPending}
          commitError={
            commit.isError
              ? commit.error instanceof Error
                ? commit.error.message
                : "저장하지 못했어요. 아직 아무것도 저장되지 않았어요."
              : null
          }
          onReanalyze={(hint) => reanalyze.mutate(hint)}
          reanalyzing={reanalyze.isPending}
          onPickAnother={resetToPicker}
        />
      ) : (
        // `failed` · 20초 안전망(`partial`) · 스트림 오류가 여기로 온다. 셋 다 읽어낸 것이 없다.
        <Failed
          title="이 사진에서는 읽어낼 게 없었어요"
          note="잘못 저장하지 않으려고 아무것도 저장하지 않았어요. 글자가 더 잘 보이는 사진이면 읽을 수 있어요."
          onRetry={() => upload.mutate(file)}
          onPickAnother={resetToPicker}
        />
      )}
    </Screen>
  );
}

/* ── 사진을 고르기 전 ─────────────────────────────────────────────────── */

/**
 * 할 일이 하나뿐인 화면이라 다른 것을 세우지 않는다. 4:3 칸이 화면 폭을 그대로 쓴다.
 *
 * 🚨 **무엇을 안 하는지를 먼저 쓴다.** 얼굴을 분석하지 않는다는 사실은 사진을 넣기 **전에**
 *    알아야 쓸모가 있다 — 넣고 나서 알려 주면 그건 통보다.
 */
function Picker({
  date,
  error,
  onPick,
}: {
  date: string;
  error: string | null;
  onPick: () => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-title text-ink">사진 한 장이 한 줄을 대신해요</h2>
        <p className="text-body-sm text-ink-muted mt-2">
          {formatDay(date)}에 남겨요. 알림장이나 식단표는 글자를 읽어 준비물과 일시로 정리하고, 아이
          활동 사진은 활동 태그만 뽑아요.
        </p>
      </div>

      <PhotoCard>
        <PhotoSlotButton
          icon={Camera}
          label="사진 고르기"
          hint="찍어서 넣거나 앨범에서 고를 수 있어요"
          onClick={onPick}
        />
      </PhotoCard>

      {error ? <CardFailed>{error}</CardFailed> : null}

      <ul className="text-body-sm text-ink-muted flex list-disc flex-col gap-1.5 pl-5">
        <li>얼굴이나 사람은 분석하지 않아요.</li>
        <li>읽어낸 내용을 보여드리고, 승인하기 전에는 아무것도 저장하지 않아요.</li>
      </ul>
    </div>
  );
}

/* ── 읽는 중 ──────────────────────────────────────────────────────────── */

function Reading({
  previewUrl,
  index,
  total,
  label,
}: {
  previewUrl: string;
  index: number;
  total: number;
  label: string;
}) {
  return (
    <div className="flex flex-col gap-4">
      <PhotoCard>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={previewUrl} alt="읽고 있는 사진" className="h-full w-full object-cover" />
      </PhotoCard>
      <Card>
        <ProgressSteps index={index} total={total} label={label} />
      </Card>
      <p className="text-body-sm text-ink-muted">
        읽은 내용을 보여드린 다음에 저장해요. 지금은 아무것도 저장되지 않아요.
      </p>
    </div>
  );
}

/* ── 못 읽었을 때 ─────────────────────────────────────────────────────── */

/** 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). 기본값으로 대체하지도 않는다. */
function Failed({
  title,
  note,
  onRetry,
  onPickAnother,
}: {
  title: string;
  note: string;
  onRetry: () => void;
  onPickAnother: () => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-title text-ink">{title}</h2>
        <p className="text-body-sm text-ink-muted mt-2">{note}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button onClick={onRetry}>다시 시도</Button>
        <Button variant="secondary" onClick={onPickAnother}>
          다른 사진 고르기
        </Button>
      </div>
    </div>
  );
}

/* ── 저장한 뒤 ────────────────────────────────────────────────────────── */

/**
 * 🚨 **저장한 것을 화면에 그대로 세운다.** 처음에는 "저장했어요" 한 줄과 버튼 둘만 남겼는데,
 *    방금까지 확인하던 사진과 항목이 통째로 사라지면서 **무엇을 저장했는지 알 수 없는 빈 화면**이
 *    됐다. 이 화면이 이미 아는 사실을 07·09 에 가서 확인하게 만들지 않는다.
 *
 * 🚨 사진은 그대로 두되 `accent` 는 떼어낸다 — 확인할 것이 더 없는 자리라, 이제 무게를 가져가는
 *    것은 다음에 갈 곳이다 (문서 §7 "한 화면에 한 장" 도 이렇게 유지된다).
 */
function SavedResult({
  result,
  childId,
  fallbackDate,
  previewUrl,
  onAnother,
}: {
  result: PhotoCommitResponse;
  childId: string;
  /** 서버가 `calendar_date` 를 안 줬을 때 캘린더가 열 날. 화면이 들고 온 날짜다. */
  fallbackDate: string;
  /** 저장한 사진. 화면을 떠나기 전까지는 살아 있다 (`revokeObjectURL` 은 언마운트에서). */
  previewUrl: string | null;
  onAnother: () => void;
}) {
  const router = useRouter();
  const day = result.calendar_date ?? fallbackDate;
  /** 🚨 서버가 저장한 것을 그대로 읽는다 — 화면이 들고 있던 선택 목록을 다시 쓰지 않는다. */
  const savedItems = result.observations.flatMap((observation) => {
    const fields = observation.domain_fields;
    const list = fields.items ?? fields.tags;
    return Array.isArray(list) ? (list as string[]) : [];
  });

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-title text-ink">저장했어요</h2>
        <p className="text-body-sm text-ink-muted mt-2">
          기록 {result.observations.length}건을 남겼어요.
          {result.event
            ? ` 일정은 ${formatDay(result.event.starts_at)} 초안으로 올라갔어요. 캘린더에 확정하는 것은 그 화면에서 따로 승인해요.`
            : ""}
        </p>
      </div>

      {previewUrl ? (
        <PhotoCard
          footer={
            savedItems.length > 0 ? (
              <div>
                <p className="text-caption text-ink-subtle">함께 남긴 것</p>
                <p className="text-body-sm text-ink mt-1">{savedItems.join(", ")}</p>
              </div>
            ) : undefined
          }
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={previewUrl} alt="저장한 사진" className="h-full w-full object-cover" />
        </PhotoCard>
      ) : null}

      {/* 🚨 `Button` 의 클래스를 링크에 베껴 붙이지 않는다 — 같은 모양이 두 벌이 되는 순간
          한쪽이 뒤처진다 (한 화면에서 primary 버튼이 두 가지로 보이게 되는 길이다).
          이동은 03 홈이 쓰는 것과 같은 `router.push` 다. */}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => router.push(`/child/${childId}/calendar?date=${day}`)}>
          캘린더에서 보기
        </Button>
        <Button variant="secondary" onClick={onAnother}>
          사진 하나 더 넣기
        </Button>
      </div>
    </div>
  );
}
