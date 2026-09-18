"use client";

import { useQueryClient } from "@tanstack/react-query";
import { josa } from "es-hangul";
import { useEffect, useRef, useState } from "react";

import { Banner } from "@/components/ui/banner";
import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import {
  addHealthSafety,
  api,
  isApiError,
  newIdempotencyKey,
  qk,
  type IdempotencyKey,
  type SafetyScanCandidate,
  type SafetyScanResponse,
} from "@/lib/api";

/**
 * 11 알레르기 검사지 읽기 — 사진에서 **옮겨 적고**, 보호자가 확인해 **한 번에** 등록한다.
 *
 * ## 🚨 이 화면이 §2 를 지키는 방식
 *
 * 최상위 `CLAUDE.md` §2: **"알레르기·검진·건강 정보는 LLM 이 생성·추론·수정하지 않는다.
 * 보호자 직접 입력 또는 **의료 기록**만."** 알레르기 검사지는 그 의료 기록이고, 그래서 이
 * 경로가 허용된다. 다만 허용되는 것은 **옮겨 적기**뿐이고 **채워 넣기**가 아니다.
 * 그 경계를 화면이 지키는 장치가 넷이다.
 *
 *   ㉠ **못 읽은 칸은 비어서 온다.** 서버가 `null` 로 내리고 화면이 그 자리를 비운 채
 *      보호자에게 넘긴다 — 기본값으로 넘기지 않는다.
 *   ㉡ **필수 칸이 빈 줄은 고를 수 없다.** 채우기 전에는 체크박스가 잠겨서, 반쯤 읽은 것이
 *      승인 목록에 조용히 섞이지 않는다.
 *   ㉢ **줄마다 검사지 원문을 함께 보여준다.** 무엇을 보고 이렇게 옮겼는지 없이 승인하면
 *      그건 확인이 아니다. 원문을 못 읽은 줄은 **미리 고르지 않는다.**
 *   ㉣ **읽지 못한 줄 수를 그대로 말한다.** "다 읽었다" 고 넘기면 보호자가 빠진 항목을
 *      모른 채 승인한다.
 *
 * 🚨 **저장 경로는 늘리지 않았다.** 승인하면 `POST /children/{cid}/health-safety`(승인 게이트 ㉡)
 *    를 **고른 줄 수만큼** 부른다 — 알레르기의 유일한 쓰기 경로는 그대로 하나다.
 *    묶음 저장 엔드포인트를 만들지 않은 이유가 그것이다.
 * 🚨 **재료 한 건이 사용자 동작 하나라 키도 줄마다 하나다** (`ApprovalSheet` 와 같은 처리).
 *    하나로 묶으면 두 번째 줄이 "같은 키 · 다른 요청" 이라 422 다.
 * 🚨 **승인 전에는 아무것도 저장되지 않는다.** 읽기(`/scan`)는 저장하지 않는다.
 */

const CATEGORY_OPTIONS = [
  { value: "", label: "고르지 않음" },
  { value: "식품", label: "음식" },
  { value: "약", label: "약" },
  { value: "환경", label: "환경 · 계절" },
  { value: "기타", label: "그 밖에" },
] as const;

const SEVERITY_LABEL: Record<string, string> = {
  mild: "가볍게",
  moderate: "보통",
  severe: "심하게",
};

/** 저장할 때의 한 줄. 서버가 준 후보 + 보호자가 채운 것 + 저장 결과. */
interface ScanRow {
  id: string;
  label: string;
  category: string;
  severity: string | null;
  reactions: string[];
  sourceText: string | null;
  /** 이미 등록되어 있는 항목. 고를 수 없다 — 승인하고 나서 409 를 보는 일이 없게. */
  alreadyRegistered: boolean;
  checked: boolean;
  status: "idle" | "saving" | "done" | "failed";
}

function toRow(candidate: SafetyScanCandidate, registered: Set<string>): ScanRow {
  const label = candidate.label ?? "";
  const category = candidate.category ?? "";
  const alreadyRegistered = label !== "" && registered.has(label);

  return {
    id: candidate.id,
    label,
    category,
    severity: candidate.severity,
    reactions: candidate.reactions,
    sourceText: candidate.source_text,
    alreadyRegistered,
    // 🚨 **미리 고르는 조건이 좁다.** 필수 칸이 다 있고 · 원문이 있고 · 아직 등록 전인 줄만.
    //    하나라도 어긋나면 보호자가 직접 골라야 한다 (위 머리말 ㉡ ㉢).
    checked: label !== "" && category !== "" && candidate.source_text !== null && !alreadyRegistered,
    status: "idle",
  };
}

/** 필수 칸이 다 찼는가. 심각도·증상은 없어도 등록된다 (추측해 채우지 않는다). */
function isComplete(row: ScanRow): boolean {
  return row.label.trim() !== "" && row.category !== "";
}

export function HealthSafetyScanSheet({
  open,
  onClose,
  childId,
  file,
  registeredLabels,
}: {
  open: boolean;
  onClose: () => void;
  childId: string;
  /** 보호자가 고른 검사지 사진. 시트를 열 때 한 번 받는다. */
  file: File | null;
  /** 이미 등록된 항목 이름. 후보에서 걸러 내는 데 쓴다. */
  registeredLabels: string[];
}) {
  const queryClient = useQueryClient();

  /**
   * 🚨 **사진 미리보기 URL 을 effect cleanup 에서 해제하지 않는다.** StrictMode 가 mount 직후
   *    cleanup 을 한 번 돌려서, 방금 받은 사진이 그 자리에서 해제된다 (`ERR_FILE_NOT_FOUND`).
   *    닫을 때 명시적으로 해제한다 (08 사진 작업에서 낸 사고와 같다).
   * 🚨 **`useState` 초기값으로 만든다.** effect 에서 만들면 첫 프레임이 사진 없는 화면이라
   *    한 번 깜빡인다.
   */
  const [previewUrl] = useState(() => (file ? URL.createObjectURL(file) : null));

  const [step, setStep] = useState<"reading" | "review">("reading");
  const [scan, setScan] = useState<SafetyScanResponse | null>(null);
  const [rows, setRows] = useState<ScanRow[]>([]);
  const [readError, setReadError] = useState<"consent" | "failed" | null>(null);
  const [saving, setSaving] = useState(false);

  /** 🚨 줄마다 키 하나. 재시도는 같은 키, 새 줄은 새 키다 (위 머리말). */
  const keys = useRef(new Map<string, IdempotencyKey>());
  const started = useRef(false);
  /**
   * 이미 등록된 이름은 **읽기를 시작한 시점의 것**을 쓴다. 배열이라 렌더마다 참조가 바뀌는데,
   * effect 의 의존성에 넣으면 읽기가 다시 돈다 — 사진을 두 번 올리게 된다.
   */
  const registeredRef = useRef(registeredLabels);

  /**
   * 🚨 **읽기는 한 번만 시작한다.** StrictMode 가 effect 를 두 번 돌리는데 두 번 올리면 사진이
   *    두 번 나가고 후보가 두 벌이 된다 (로그인 1회용 코드와 같은 가드다).
   * 🚨 **cleanup 에서 결과를 버리지 않는다.** `cancelled` 플래그를 두면 StrictMode 의 첫 cleanup 이
   *    먼저 돌아서 **방금 받은 후보를 그대로 버린다** — 가드가 있어서 두 번째 실행은 아무것도
   *    새로 시작하지 않으므로, 버리면 화면이 영영 "읽는 중" 에 멈춘다.
   */
  useEffect(() => {
    if (!file || started.current) return;
    started.current = true;

    void (async () => {
      try {
        const form = new FormData();
        form.append("photo", file);
        const result = await api.post<SafetyScanResponse>(
          `/children/${childId}/health-safety/scan`,
          form,
        );
        const registered = new Set(registeredRef.current);
        setScan(result);
        setRows(result.candidates.map((candidate) => toRow(candidate, registered)));
      } catch (error) {
        // 🚨 일반 실패로 뭉뚱그리지 않는다 — 동의가 없으면 다시 눌러도 같은 403 이다.
        setReadError(isApiError(error, "consent_required") ? "consent" : "failed");
      } finally {
        setStep("review");
      }
    })();
  }, [file, childId]);

  function close() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    onClose();
  }

  function patch(id: string, next: Partial<ScanRow>) {
    setRows((prev) => prev.map((row) => (row.id === id ? { ...row, ...next } : row)));
  }

  const picked = rows.filter((row) => row.checked && row.status !== "done");
  const savedCount = rows.filter((row) => row.status === "done").length;
  const failedCount = rows.filter((row) => row.status === "failed").length;

  /**
   * 🚨 **한 줄씩 부른다.** 알레르기의 쓰기 경로는 하나뿐이라(최상위 §2) 묶음 엔드포인트를
   *    만들지 않았다. 실패한 줄은 `failed` 로 남고 성공한 줄은 `done` 이 되어, 다시 누르면
   *    **실패한 것만** 같은 키로 재시도한다.
   */
  async function approve() {
    setSaving(true);
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

  /**
   * 🚨 **남은 줄이 있으면 승인 버튼을 거두지 않는다.** 예전에는 "고른 것을 다 저장했으면"
   *    바로 닫기 버튼만 남겼는데, 그러면 일부만 등록한 뒤 **아직 고를 수 있는 줄이 화면에
   *    보이는데 등록할 방법이 없었다.** 끝난 것은 등록된 줄과 이미 있던 줄뿐이다.
   */
  const remaining = rows.filter((row) => row.status !== "done" && !row.alreadyRegistered);
  const nothingLeft = remaining.length === 0;

  return (
    <BottomSheet
      open={open}
      onClose={close}
      title="검사지에서 가져오기"
      description={step === "reading" ? undefined : "저장하기 전에 한 줄씩 확인해 주세요."}
      // 🚨 승인이 걸린 화면은 스크림 탭으로 닫히지 않는다 (디자인 시스템 §7). 읽는 중에는
      //    아직 승인할 것이 없어서 닫을 수 있다.
      dismissible={step === "reading"}
      footer={
        step === "reading" ? null : nothingLeft ? (
          <Button block onClick={close}>
            닫기
          </Button>
        ) : (
          <div className="flex flex-col gap-2">
            {failedCount > 0 ? (
              <p role="status" className="text-body-sm text-ink-muted">
                {failedCount}건을 등록하지 못했어요. 나머지는 저장됐으니 다시 눌러 주세요.
              </p>
            ) : null}
            <Button variant="approve" onClick={approve} disabled={saving || picked.length === 0}>
              {saving ? <Spinner /> : null}
              {saving
                ? "등록하는 중이에요"
                : picked.length === 0
                  ? "고른 것이 없어요"
                  : `확인했어요, ${picked.length}건 등록할게요`}
            </Button>
            {/* 🚨 저장한 것이 있으면 "그만두기" 가 아니다 — 되돌리는 것처럼 읽힌다. */}
            <Button variant="tertiary" block onClick={close} disabled={saving}>
              {savedCount > 0 ? "닫기" : "그만두기"}
            </Button>
          </div>
        )
      }
    >
      {step === "reading" ? (
        <Reading previewUrl={previewUrl} />
      ) : readError ? (
        <ReadFailed kind={readError} />
      ) : (
        <div className="flex flex-col gap-4">
          {/* 🚨 `caution` 은 승인 게이트 2곳 전용이다 (디자인 시스템 §3). 여기가 그중 하나다. */}
          <Banner tone="caution" title="검사지에 적힌 것만 옮겼어요">
            읽지 못한 칸은 비워 뒀어요. 검사지를 보고 직접 골라 주세요. 승인 전에는 저장되지 않아요.
          </Banner>

          {previewUrl ? <Preview url={previewUrl} /> : null}

          {/* 🚨 못 읽은 줄 수를 그대로 말한다 — "다 읽었다" 고 넘기면 빠진 항목을 모른다. */}
          {scan && scan.unreadable_count > 0 ? (
            <p className="text-body-sm text-ink-muted">
              읽지 못한 줄이 {scan.unreadable_count}개 있어요. 검사지를 보고 직접 더해 주세요.
            </p>
          ) : null}

          {rows.length === 0 ? (
            <p className="text-body-sm text-ink-muted">
              검사지에서 항목을 찾지 못했어요. 직접 적어서 등록할 수 있어요.
            </p>
          ) : (
            <ul className="border-line divide-line rounded-card bg-surface divide-y overflow-hidden border">
              {rows.map((row) => (
                <li key={row.id}>
                  <ScanRowItem row={row} onPatch={(next) => patch(row.id, next)} />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </BottomSheet>
  );
}

/* ── 읽는 중 ──────────────────────────────────────────────────────────── */

function Reading({ previewUrl }: { previewUrl: string | null }) {
  return (
    <div className="flex flex-col items-center gap-4 py-6">
      {previewUrl ? <Preview url={previewUrl} /> : null}
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

/** 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). */
function ReadFailed({ kind }: { kind: "consent" | "failed" }) {
  return (
    <p role="status" className="text-body-sm text-ink-muted">
      {kind === "consent"
        ? "건강 정보 동의를 받기 전이라 검사지를 읽을 수 없어요. 저장된 것은 하나도 없어요."
        : "검사지를 읽지 못했어요. 저장된 것은 하나도 없으니 직접 적어서 등록할 수 있어요."}
    </p>
  );
}

/**
 * 고른 검사지. 🚨 **승인 화면에 원본을 함께 세운다** — 옮겨 적은 것이 맞는지 대조할 것이
 * 없으면 그건 확인이 아니다.
 *
 * 🚨 **작게 둔다.** 한동안 224px 였는데, 위의 `caution` 배너까지 더해지니 **정작 승인할 목록이
 *    시트 밖으로 밀렸다** — 확인하라고 세운 것이 확인할 것을 가린 셈이다. 이 자리의 일은
 *    "검사지를 읽는 것" 이 아니라 "내가 고른 그 사진이 맞나" 라서 96px 로 충분하다.
 * 🚨 `object-contain` + `surface-muted` 바탕이다. `cover` 로 채우면 문서가 잘려서 어느 검사지인지
 *    알아볼 수가 없다.
 * 🚨 `next/image` 를 쓰지 않는다. `blob:` URL 이라 최적화가 걸리지 않고 도메인 설정도 못 한다.
 */
function Preview({ url }: { url: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt="고른 검사지 사진"
      className="rounded-card border-line bg-surface-muted h-24 w-full border object-contain"
    />
  );
}

/* ── 후보 한 줄 ───────────────────────────────────────────────────────── */

function ScanRowItem({ row, onPatch }: { row: ScanRow; onPatch: (next: Partial<ScanRow>) => void }) {
  const complete = isComplete(row);
  const meta = [row.category || null, row.severity ? SEVERITY_LABEL[row.severity] : null]
    .filter(Boolean)
    .join(" · ");

  if (row.status === "done") {
    return (
      <div className="flex items-center gap-3 px-4 py-3.5">
        <p className="text-body text-ink-muted flex-1">{row.label}</p>
        <p role="status" className="text-caption text-ink-subtle">
          등록했어요
        </p>
      </div>
    );
  }

  if (row.alreadyRegistered) {
    return (
      <div className="flex items-center gap-3 px-4 py-3.5">
        <p className="text-body text-ink-muted flex-1">{row.label}</p>
        <p className="text-caption text-ink-subtle">이미 등록되어 있어요</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      {/* 🚨 **필수 칸이 비면 고를 수 없다.** 반쯤 읽은 줄이 승인 목록에 조용히 섞이지 않게
          체크를 잠근다 — 채우면 열린다 (머리말 ㉡). */}
      <Checkbox
        checked={row.checked && complete}
        onChange={(checked) => {
          if (!complete) return;
          onPatch({ checked });
        }}
        label={
          row.label ? (
            <span className="text-body text-ink">{row.label}</span>
          ) : (
            <span className="text-body text-ink-subtle">이름을 읽지 못했어요</span>
          )
        }
        description={
          meta ? <span className="text-caption text-ink-subtle">{meta}</span> : undefined
        }
      />

      {/* 🚨 **못 읽은 필수 칸을 그 자리에서 채우게 한다.** 시트를 닫고 직접 적기로 가라고 하면
          검사지 한 장에 두 가지 흐름을 쓰게 된다. */}
      {row.label === "" ? (
        <TextInput
          label="무엇인가요"
          hint="검사지를 보고 적어주세요."
          value={row.label}
          onChange={(e) => onPatch({ label: e.target.value })}
          autoComplete="off"
        />
      ) : null}

      {row.category === "" ? (
        <Select
          label="분류"
          value={row.category}
          options={CATEGORY_OPTIONS}
          onChange={(category) => onPatch({ category })}
        />
      ) : null}

      {/* 🚨 **무엇을 보고 옮겼는지 함께 세운다** (머리말 ㉢). 원문이 없으면 그 사실을 말하고,
          그 줄은 미리 고르지도 않는다 — 대조할 것이 없으면 확인이 아니다. */}
      {row.sourceText ? (
        <p className="text-caption text-ink-subtle">검사지에 적힌 것 {row.sourceText}</p>
      ) : (
        <p className="text-caption text-ink-muted">
          검사지 원문을 읽지 못했어요. 검사지를 보고 확인한 뒤에 골라 주세요.
        </p>
      )}

      {!complete ? (
        <p className="text-caption text-ink-muted">
          {row.label === "" ? "이름" : "분류"}을 채워야 고를 수 있어요.
        </p>
      ) : null}

      {row.status === "failed" ? (
        <p role="status" className="text-caption text-ink-muted">
          {josa(row.label, "은/는")} 등록하지 못했어요. 저장된 것은 없어요.
        </p>
      ) : null}
    </div>
  );
}
