"use client";

import { useMutation } from "@tanstack/react-query";
import { Camera, ChevronLeft } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { AuthGate } from "@/components/auth-gate";
import { ConsentRequiredCard } from "@/components/consent-required-card";
import { PhotoReview } from "@/components/photo-review";
import { PhotoSourceSheet } from "@/components/photo-source-sheet";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";
import { PageTitle } from "@/components/ui/page-title";
import { PhotoCard, PhotoSlotButton } from "@/components/ui/photo-card";
import { ProgressSteps } from "@/components/ui/progress-steps";
import { Screen } from "@/components/ui/screen";
import { useChildId } from "@/hooks/use-child-id";
import { useRunStream } from "@/hooks/use-run-stream";
import { usePhotoDraftStore, type PendingPhoto } from "@/stores/photo-draft";
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
 *
 * 🚨 **사진을 고르는 자리는 이 화면이 아니다.** 부모는 03 홈(또는 09 하루 패널)에서 카메라를
 *    누르고, 그 자리에 뜬 시트에서 촬영/앨범을 고른다 — 고르는 것은 화면 하나를 채울 일이
 *    아니라 두 갈래 한 번이라서다. 고른 파일은 `stores/photo-draft.ts` 로 넘어오고 이 화면은
 *    받자마자 올린다. 부모가 보는 08 의 첫 장면은 **읽는 중**이고, 곧 확인 화면이다.
 *    파일 없이 이 주소로 바로 들어온 경우(직접 링크 · 새로고침)에만 고르는 칸을 그린다.
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

  /**
   * 지금 보고 있는 사진. **처음 값은 홈·캘린더의 시트가 넘겨준 것**이다.
   *
   * 🚨 **effect 가 아니라 초기값으로 받는다.** effect 안에서 `setState` 를 하면 렌더가 한 번
   *    더 도는 것 말고도, 부모가 08 에 도착한 첫 프레임이 **사진 없는 화면**이 된다 —
   *    "고르면 바로 읽는 화면" 이 한 번 깜빡이게 된다.
   * 🚨 `peek` 은 **순수한 읽기**라 초기값으로 안전하다. objectURL 은 스토어가 이벤트 안에서
   *    이미 만들어 뒀다 (`stores/photo-draft.ts` — 렌더 중에 만들면 StrictMode 에서 샌다).
   * 🚨 **아이가 맞을 때만 온다.** 스토어가 `childId` 를 함께 들고 확인한다 — 아이를 바꾼 뒤
   *    들어오면 다른 아이의 사진이 이 아이 기록으로 올라간다.
   */
  const [photo, setPhoto] = useState<PendingPhoto | null>(() =>
    usePhotoDraftStore.getState().peek(childId),
  );
  const [saved, setSaved] = useState<PhotoCommitResponse | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);

  const run = useRunStream(childId);
  const [runId, setRunId] = useState<string | null>(null);

  /**
   * 🚨 재시도할 때 키를 새로 만들지 않는다 — 같은 사진을 다시 올리는 것이 재시도다.
   *    **다른 사진으로 바꿀 때만** 돌린다(`resetToPicker`). 같은 키에 다른 본문을 보내면
   *    422 idempotency_key_reuse 다 (03 홈의 `closeRun` 과 같은 규칙).
   */
  const idempotencyKey = useIdempotencyKey();

  /**
   * 🚨 **넘겨받은 사진은 스토어에서 뗀다.** 남겨 두면 08 을 다시 열 때 지난번 사진이 저절로
   *    올라간다 — 부모가 고른 적 없는 사진이 분석으로 들어가는 경로다.
   *    `release` 는 URL 을 해제하지 않는다(아래가 그 책임을 진다). `setState` 를 하지 않으므로
   *    StrictMode 가 두 번 돌려도 같은 결과다.
   */
  useEffect(() => {
    usePhotoDraftStore.getState().release();
  }, []);

  /**
   * 🚨 **objectURL 해제를 effect 의 cleanup 에 걸지 않는다.** StrictMode 가 mount 직후
   *    cleanup 을 한 번 돌려서, 방금 넘겨받은 사진의 URL 이 그 자리에서 해제되고 미리보기가
   *    깨진다 (`ERR_FILE_NOT_FOUND` 로 실제로 났다). 해제는 **다음 사진을 고르는 순간**과
   *    **로그아웃** 두 이벤트에서만 하고, 그 책임은 `stores/photo-draft.ts` 한 곳에 있다 —
   *    살아 있는 URL 은 언제나 최대 한 개다.
   */

  const upload = useMutation({
    mutationFn: (picked: PendingPhoto) =>
      uploadPhoto(
        childId,
        // 🚨 부모가 고른 lane 을 함께 싣는다 — 서버가 추측하게 두지 않는다 (operations.ts).
        photoFormData(picked.file, { lane: picked.lane, date }),
        idempotencyKey.current(),
      ),
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
  const startUpload = upload.mutate;

  /**
   * 사진 한 장당 **한 번만** 올린다.
   *
   * 🚨 `setState` 를 하지 않는다 — 사진은 이미 상태에 들어와 있고, 여기는 그 상태를 바깥
   *    시스템(서버)에 흘려보내는 자리다. 파일 자체를 기억해 두는 이유는 재렌더마다 다시
   *    올리지 않기 위해서고, 실패 뒤 "다시 시도" 는 버튼이 직접 부른다 (같은 키로 재시도).
   */
  const uploadedRef = useRef<File | null>(null);
  useEffect(() => {
    if (!photo || uploadedRef.current === photo.file) return;
    uploadedRef.current = photo.file;
    startUpload(photo);
  }, [photo, startUpload]);

  /**
   * 다른 사진으로 바꾼다. 저장된 것이 없으므로 되돌릴 것도 없다.
   *
   * 부모가 방금 "다른 사진" 을 요구했으니 시트를 바로 연다 — 고르는 칸을 한 번 더 거치게
   * 하면 같은 뜻의 버튼을 두 번 누르는 화면이 된다.
   */
  function pickAnother() {
    run.reset();
    upload.reset();
    commit.reset();
    reanalyze.reset();
    setRunId(null);
    setSaved(null);
    // 앞 사진의 objectURL 은 위의 cleanup 이 해제한다 (상태가 바뀌면 그때 돈다).
    setPhoto(null);
    // 🚨 여기서 돌린다. 다음 요청은 **다른 사진**이라 같은 키로 보내면 422 다.
    idempotencyKey.rotate();
    setSheetOpen(true);
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

      {saved ? (
        <SavedResult
          result={saved}
          childId={childId}
          fallbackDate={date}
          previewUrl={photo?.url ?? null}
          onAnother={pickAnother}
        />
      ) : consentBlocked ? (
        <ConsentRequiredCard
          childId={childId}
          error={consentBlocked}
          what="사진을 읽어 드릴 수 없어요."
        />
      ) : !photo ? (
        <Picker date={date} onPick={() => setSheetOpen(true)} />
      ) : /* 🚨 **보내기 실패를 먼저 본다.** 요청이 실패하면 run 은 `idle` 그대로라, 아래
             "아직 시작 전" 과 구분되지 않으면 실패한 화면이 영원히 읽는 중으로 남는다. */
      upload.isError ? (
        <Failed
          title="사진을 보내지 못했어요"
          note="저장된 것은 없어요. 같은 사진을 그대로 다시 보낼 수 있어요."
          // 🚨 같은 사진의 재시도라 Idempotency-Key 를 그대로 쓴다 (키를 돌리지 않는다).
          onRetry={() => startUpload(photo)}
          onPickAnother={pickAnother}
        />
      ) : /* 🚨 `idle` 을 실패로 떨어뜨리지 않는다. 사진을 고른 직후의 한 틱과 요청이 나가기 전이
             여기 걸리는데, 그 상태를 "읽어낼 게 없었어요" 로 그리면 **보내 보지도 않고 실패라고
             말하는 화면**이 된다 (실제로 그렇게 났다). 실패는 스트림이 그렇게 말할 때만이다. */
      upload.isPending || run.state.status === "streaming" || run.state.status === "idle" ? (
        <Reading
          previewUrl={photo.url}
          index={run.state.step?.index ?? 0}
          total={run.state.step?.total ?? 2}
          label={run.state.step?.label ?? "사진을 열고 있어요"}
        />
      ) : run.state.parsed ? (
        // 🚨 run 마다 새로 세운다. 다시 읽기(`reanalyze`)는 새 run 이라, 앞 run 에서 고른 항목이
        //    넘어오면 **부모가 확인한 적 없는 것**이 저장 대상으로 남는다.
        <PhotoReview
          key={runId}
          declared={photo.lane}
          guess={run.state.lane?.guess ?? null}
          confidence={run.state.lane?.confidence ?? 0}
          parsed={run.state.parsed}
          date={date}
          previewUrl={photo.url}
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
          onPickAnother={pickAnother}
        />
      ) : (
        // `failed` · 20초 안전망(`partial`) · 스트림 오류가 여기로 온다. 셋 다 읽어낸 것이 없다.
        <Failed
          title="이 사진에서는 읽어낼 게 없었어요"
          note="잘못 저장하지 않으려고 아무것도 저장하지 않았어요. 글자가 더 잘 보이는 사진이면 읽을 수 있어요."
          onRetry={() => startUpload(photo)}
          onPickAnother={pickAnother}
        />
      )}

      {/* 🚨 홈·캘린더와 **같은 시트**다. 고르는 방법이 화면마다 다르면 부모가 매번 다시 찾는다. */}
      <PhotoSourceSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        onPick={({ file, lane }) => {
          setSheetOpen(false);
          // 🚨 이벤트 핸들러라 여기서 만들어도 된다. 만드는 것도 해제하는 것도 스토어가
          //    한 곳에서 맡는다 — 올리는 것은 위의 effect 다 (사진 한 장당 한 번).
          setPhoto(usePhotoDraftStore.getState().adoptPhoto(file, lane));
        }}
      />
    </Screen>
  );
}

/* ── 사진 없이 이 주소로 바로 들어온 경우 ────────────────────────────── */

/**
 * 🚨 **주된 경로가 아니다.** 보통은 홈·캘린더의 시트에서 고른 사진을 들고 들어와서 이 칸을
 *    보지 않는다. 직접 링크로 들어왔거나 새로고침해서 들고 온 파일이 사라진 경우에만 선다 —
 *    그때 빈 화면을 주지 않으려고 남겨 둔 자리다.
 *
 * 🚨 **무엇을 안 하는지를 먼저 쓴다.** 얼굴을 분석하지 않는다는 사실은 사진을 넣기 **전에**
 *    알아야 쓸모가 있다 — 넣고 나서 알려 주면 그건 통보다.
 */
function Picker({ date, onPick }: { date: string; onPick: () => void }) {
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
