"use client";

import { josa } from "es-hangul";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useState, type ReactNode } from "react";

import { SafetyScanRowSheet } from "@/components/safety-scan-row-sheet";
import {
  SEVERITY_LABEL,
  isComplete,
  needsReview,
  reviewReason,
  type ScanRow,
} from "@/components/safety-scan-fields";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/cn";

/**
 * 11-2 알레르기 검사지 — 옮겨 적은 것을 보호자가 확인하는 칸. 🚨 **승인 게이트 ㉡** 이다.
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
 * ## 🚨 왜 시트가 아니라 화면인가
 *
 * 한동안 바텀시트 하나였다. 검사지 한 장에서 열 줄 넘게 나오는데, 그러면 `caution` 배너 ·
 * 사진 · 목록 · 승인 버튼이 **시트 높이 안에서** 경쟁한다 — 실제로 사진을 96px 까지 줄여야
 * 목록이 보였다. 08 사진과 같은 모양으로 화면을 내주고, 화면이 두 무리로 가른다:
 *
 *   ㉠ **확인이 필요한 것**을 위에 **펼쳐서**,
 *   ㉡ **잘 읽은 것**은 아래에 **접어서**.
 *
 * 열다섯 줄이 한 줄씩 늘어서면 정작 봐야 할 두세 줄이 묻힌다 — 보호자가 볼 것은
 * "기계가 못 읽은 곳" 이다. 🚨 **양쪽 다 고칠 수 있다**: 잘못 읽은 것은 보호자만 알고,
 * 고칠 길이 못 읽은 줄에만 있으면 "기계가 확신한 것은 못 고친다" 가 된다.
 *
 * 🚨 **08 사진과 달리 여기는 승인 게이트다.** 08 이 만드는 `event` 는 `draft` 지만
 *    이 화면의 등록은 되돌릴 수 없다 — 그래서 `banner-caution` 과 `btn-approve` 를 쓴다
 *    (08 은 둘 다 쓰지 않는다). 게이트를 늘린 것이 아니라 **있던 게이트가 화면으로 옮겨온
 *    것**이다.
 *
 * 🚨 **저장 경로는 늘리지 않았다.** 승인하면 `POST /children/{cid}/health-safety`
 *    를 **고른 줄 수만큼** 부른다 — 알레르기의 유일한 쓰기 경로는 그대로 하나다.
 *    묶음 저장 엔드포인트를 만들지 않은 이유가 그것이다.
 * 🚨 **승인 전에는 아무것도 저장되지 않는다.** 읽기(`/scan`)는 저장하지 않는다.
 * 🚨 **`source_text` 는 OCR 로 들어온 외부 텍스트다.** 그대로 텍스트로만 그린다 —
 *    `dangerouslySetInnerHTML` 를 쓰지 않는다 (apps/web/CLAUDE.md §4).
 */
export function SafetyScanReview({
  rows,
  onPatch,
  onDrop,
  unreadableCount,
  previewUrl,
  onApprove,
  saving,
  failedCount,
  onLeave,
}: {
  rows: ScanRow[];
  onPatch: (id: string, next: Partial<ScanRow>) => void;
  onDrop: (id: string) => void;
  /** 줄은 있는데 통째로 못 읽은 것의 수. 서버가 센다. */
  unreadableCount: number;
  previewUrl: string | null;
  onApprove: () => void;
  saving: boolean;
  failedCount: number;
  /** 등록하지 않고 프로필로. */
  onLeave: () => void;
}) {
  const [editing, setEditing] = useState<ScanRow | null>(null);
  const [showChecked, setShowChecked] = useState(false);
  const [showRegistered, setShowRegistered] = useState(false);

  const live = rows.filter((row) => row.status !== "done");
  const review = live.filter((row) => needsReview(row));
  const ready = live.filter((row) => !needsReview(row) && !row.alreadyRegistered);
  const registered = live.filter((row) => row.alreadyRegistered);

  const picked = live.filter((row) => row.checked && isComplete(row) && !row.alreadyRegistered);
  const savedCount = rows.filter((row) => row.status === "done").length;

  /**
   * 🚨 **남은 줄이 있으면 승인 버튼을 거두지 않는다.** 예전에는 고른 것을 다 저장하면 닫기만
   *    남겼는데, 그러면 일부만 등록한 뒤 **아직 고를 수 있는 줄이 화면에 보이는데 등록할
   *    방법이 없었다.** 끝난 것은 등록된 줄과 이미 있던 줄뿐이다.
   */
  const nothingLeft = review.length + ready.length === 0;

  function saveRow(next: ScanRow) {
    onPatch(next.id, next);
    setEditing(null);
  }

  return (
    <div className="flex flex-col gap-6">
      {/* 🚨 `caution` 은 승인 게이트 2곳 전용이다 (디자인 시스템 §3). 여기가 그중 하나다. */}
      <Banner tone="caution" title="검사지에 적힌 것만 옮겼어요">
        읽지 못한 칸은 비워 뒀어요. 검사지를 보고 직접 채워 주세요. 승인 전에는 저장되지 않아요.
      </Banner>

      {previewUrl ? <Preview url={previewUrl} /> : null}

      {/* 🚨 못 읽은 줄 수를 그대로 말한다 — "다 읽었다" 고 넘기면 빠진 항목을 모른다 (머리말 ㉣). */}
      {unreadableCount > 0 ? (
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field px-3 py-2">
          읽지 못한 줄이 {unreadableCount}개 있어요. 검사지를 보고 직접 더해 주세요.
        </p>
      ) : null}

      {rows.length === 0 ? (
        <p className="text-body-sm text-ink-muted">
          검사지에서 항목을 찾지 못했어요. 프로필에서 직접 적어 등록할 수 있어요.
        </p>
      ) : (
        <>
          <ReviewGroup rows={review} onPatch={onPatch} onEdit={setEditing} />

          <CheckedGroup
            rows={ready}
            open={showChecked}
            onToggle={() => setShowChecked((v) => !v)}
            onPatch={onPatch}
            onEdit={setEditing}
          />

          <RegisteredGroup
            rows={registered}
            open={showRegistered}
            onToggle={() => setShowRegistered((v) => !v)}
          />
        </>
      )}

      <Card>
        <p className="text-section text-ink">이렇게 등록할게요</p>
        {/* 🚨 중립 면이다. 아직 저장된 것이 없다는 것은 경고가 아니라 **사실**이다.
            🚨 **등록을 한 번 하고 나면 이 문장은 거짓이 된다.** 한동안 그대로 세워 뒀더니
               바로 아래 "등록한 것 2건" 과 같은 화면에서 서로를 부정했다 — 승인 게이트에서
               화면이 자기 말을 뒤집으면 그다음 문장도 못 믿는다. */}
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-2 px-3 py-2">
          {savedCount === 0
            ? "지금까지는 아무것도 저장되지 않았어요. 아래를 눌러야 등록돼요."
            : "아래에서 고른 것은 아직 저장되지 않았어요. 승인해야 등록돼요."}
        </p>

        <dl className="mt-4 flex flex-col gap-2">
          <SummaryRow
            term="고른 것"
            value={picked.length > 0 ? `${picked.length}건` : "아직 없음"}
          />
          {savedCount > 0 ? <SummaryRow term="등록한 것" value={`${savedCount}건`} /> : null}
          <SummaryRow term="출처" value="알레르기 검사지 · 보호자 확인" />
        </dl>

        {/* 🚨 **몇 건이 빠지는지 숫자로 말한다.** 확인 안 한 것이 조용히 사라지면
            "승인 전에는 저장되지 않아요" 의 반대말이 된다. */}
        {review.length > 0 ? (
          <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-3 px-3 py-2">
            확인하지 않은 {review.length}건은 등록하지 않아요.
          </p>
        ) : null}

        {failedCount > 0 ? (
          // 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3).
          <p
            role="status"
            className="bg-surface-muted rounded-field text-body-sm text-ink-muted mt-3 px-3 py-2"
          >
            {failedCount}건을 등록하지 못했어요. 나머지는 저장됐으니 다시 눌러 주세요.
          </p>
        ) : null}

        {nothingLeft ? (
          <Button block className="mt-4" onClick={onLeave}>
            프로필로 돌아가기
          </Button>
        ) : (
          <>
            {/* 🚨 승인 게이트 ㉡ — 되돌릴 수 없는 확정이라 `btn-approve` 다. */}
            <Button
              variant="approve"
              className="mt-4"
              onClick={onApprove}
              disabled={saving || picked.length === 0}
              aria-busy={saving}
            >
              {saving ? <Spinner /> : null}
              {saving
                ? "등록하는 중이에요"
                : picked.length === 0
                  ? "고른 것이 없어요"
                  : `확인했어요, ${picked.length}건 등록할게요`}
            </Button>
            {/* 🚨 저장한 것이 있으면 "그만두기" 가 아니다 — 되돌리는 것처럼 읽힌다. */}
            <Button variant="tertiary" block className="mt-2" onClick={onLeave} disabled={saving}>
              {savedCount > 0 ? "그만 보기" : "등록하지 않고 나가기"}
            </Button>
          </>
        )}
      </Card>

      <p className="text-caption text-ink-subtle">
        등록한 것은 AI 가 만들거나 고치지 못해요. 식사 제안이 이 목록을 보고 걸러요.
      </p>

      <SafetyScanRowSheet
        row={editing}
        onClose={() => setEditing(null)}
        onSave={saveRow}
        onDrop={(id) => {
          onDrop(id);
          setEditing(null);
        }}
      />
    </div>
  );
}

/* ── ㉠ 확인이 필요한 것 ──────────────────────────────────────────────── */

/**
 * 🚨 **펼친 채로 맨 위에 둔다.** 보호자가 이 화면에서 할 일은 "기계가 못 읽은 곳을 메우는 것"
 *    하나고, 그게 화면 아래에 있으면 열다섯 줄을 지나야 닿는다.
 */
function ReviewGroup({
  rows,
  onPatch,
  onEdit,
}: {
  rows: ScanRow[];
  onPatch: (id: string, next: Partial<ScanRow>) => void;
  onEdit: (row: ScanRow) => void;
}) {
  if (rows.length === 0) return null;

  return (
    <Group
      heading={`확인이 필요해요 ${rows.length}건`}
      note="검사지를 보고 채워야 등록할 수 있어요."
    >
      {rows.map((row) => (
        <li key={row.id}>
          <ScanRowCard row={row} onPatch={onPatch} onEdit={onEdit} />
        </li>
      ))}
    </Group>
  );
}

/* ── ㉡ 잘 읽은 것 ────────────────────────────────────────────────────── */

/**
 * 🚨 **기본이 접힘이다.** 검사지 한 장에서 열 줄 넘게 나오는데 잘 읽은 것을 전부 펼쳐 두면
 *    위의 "확인이 필요해요" 가 화면 밖으로 밀린다. 건수는 접힌 채로도 보인다.
 * 🚨 **접혀 있어도 고른 상태는 그대로다.** 안 보이는 것이 안 등록되는 것은 아니다 —
 *    그래서 아래 "이렇게 등록할게요" 가 건수를 항상 말한다.
 * 🚨 **등장 애니메이션을 만들지 않는다** (디자인 시스템 §8). 펼침은 즉시다.
 */
function CheckedGroup({
  rows,
  open,
  onToggle,
  onPatch,
  onEdit,
}: {
  rows: ScanRow[];
  open: boolean;
  onToggle: () => void;
  onPatch: (id: string, next: Partial<ScanRow>) => void;
  onEdit: (row: ScanRow) => void;
}) {
  if (rows.length === 0) return null;

  const pickedCount = rows.filter((row) => row.checked).length;

  return (
    <FoldableGroup
      id="safety-scan-checked"
      heading={`잘 읽었어요 ${rows.length}건`}
      note={
        open
          ? "잘못 읽은 것이 있으면 눌러서 고쳐요."
          : `${pickedCount}건을 등록해요. 눌러서 하나씩 확인할 수 있어요.`
      }
      open={open}
      onToggle={onToggle}
    >
      {rows.map((row) => (
        <li key={row.id}>
          <ScanRowCard row={row} onPatch={onPatch} onEdit={onEdit} />
        </li>
      ))}
    </FoldableGroup>
  );
}

/* ── ㉢ 이미 등록된 것 ────────────────────────────────────────────────── */

/**
 * 🚨 **숨기지 않고 접어 둔다.** 검사지에 있는데 화면에서 사라지면 보호자는 "이건 왜 안 옮겨졌지"
 *    를 확인할 길이 없다. 할 일이 없는 줄이라 위의 두 무리와 섞지 않을 뿐이다.
 * 🚨 **고칠 수 없다.** 이미 있는 기록을 고치는 자리는 프로필 목록이고, 여기서 열어 주면
 *    검사지에 없는 변경이 검사지에서 온 것처럼 된다.
 */
function RegisteredGroup({
  rows,
  open,
  onToggle,
}: {
  rows: ScanRow[];
  open: boolean;
  onToggle: () => void;
}) {
  if (rows.length === 0) return null;

  return (
    <FoldableGroup
      id="safety-scan-registered"
      heading={`이미 등록되어 있어요 ${rows.length}건`}
      note="프로필 목록에서 고치거나 내릴 수 있어요."
      open={open}
      onToggle={onToggle}
    >
      {rows.map((row) => (
        <li key={row.id}>
          <div className="rounded-card border-line bg-surface border p-4">
            <p className="text-body text-ink-muted">{row.label}</p>
            {row.sourceText ? <SourceText text={row.sourceText} /> : null}
          </div>
        </li>
      ))}
    </FoldableGroup>
  );
}

/* ── 무리의 틀 ────────────────────────────────────────────────────────── */

function Group({
  heading,
  note,
  children,
}: {
  heading: string;
  note: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <div>
        <h2 className="text-label text-brand">{heading}</h2>
        <p className="text-body-sm text-ink-muted mt-1">{note}</p>
      </div>
      <ul className="flex flex-col gap-2">{children}</ul>
    </section>
  );
}

function FoldableGroup({
  id,
  heading,
  note,
  open,
  onToggle,
  children,
}: {
  id: string;
  heading: string;
  note: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={id}
        className="min-h-touch flex w-full items-center justify-between gap-2 text-left"
      >
        <span>
          <span className="text-label text-brand block">{heading}</span>
          <span className="text-body-sm text-ink-muted mt-1 block">{note}</span>
        </span>
        {/* 🚨 방향은 아이콘을 갈아 끼워 말한다 — 회전 애니메이션을 만들지 않는다 (`Select` 와 같다). */}
        {open ? (
          <ChevronUp
            aria-hidden
            size={ICON_SIZE.md}
            strokeWidth={ICON_STROKE}
            className="text-ink-subtle shrink-0"
          />
        ) : (
          <ChevronDown
            aria-hidden
            size={ICON_SIZE.md}
            strokeWidth={ICON_STROKE}
            className="text-ink-subtle shrink-0"
          />
        )}
      </button>

      {open ? (
        <ul id={id} className="flex flex-col gap-2">
          {children}
        </ul>
      ) : null}
    </section>
  );
}

/* ── 후보 한 줄 ───────────────────────────────────────────────────────── */

function ScanRowCard({
  row,
  onPatch,
  onEdit,
}: {
  row: ScanRow;
  onPatch: (id: string, next: Partial<ScanRow>) => void;
  onEdit: (row: ScanRow) => void;
}) {
  const complete = isComplete(row);
  const meta = [row.category || null, row.severity ? SEVERITY_LABEL[row.severity] : null]
    .filter(Boolean)
    .join(" · ");
  const review = needsReview(row);

  return (
    <div
      className={cn(
        "rounded-card bg-surface border p-4",
        // 🚨 확인이 필요한 것을 빨강으로 칠하지 않는다. 사고가 아니라 **아직 안 한 일**이다 —
        //    테두리만 진하게 해서 눈에 먼저 들어오게 한다.
        review ? "border-line-strong" : "border-line",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {/* 🚨 **필수 칸이 비면 고를 수 없다.** 반쯤 읽은 줄이 승인 목록에 조용히 섞이지 않게
              체크를 잠근다 — 채우면 열린다 (머리말 ㉡). */}
          {/* 🚨 **비활성 체크박스를 두지 않는다** ("왜 안 눌리지" 를 만든다 · 디자인 시스템 §2-5).
              누를 수는 있되 **아무 일도 일어나지 않고**, 왜 그런지는 바로 아래 사유 줄이
              글자로 말한다 — 그 줄이 없으면 이 처리가 그냥 고장난 체크박스가 된다. */}
          <Checkbox
            checked={row.checked && complete}
            onChange={(checked) => {
              if (!complete || row.status === "saving") return;
              onPatch(row.id, { checked });
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
        </div>
        <Button
          variant="tertiary"
          size="compact"
          onClick={() => onEdit(row)}
          disabled={row.status === "saving"}
        >
          고치기
        </Button>
      </div>

      {/* 🚨 **무엇을 보고 옮겼는지 함께 세운다** (머리말 ㉢). 원문이 없으면 그 사실을 말하고,
          그 줄은 미리 고르지도 않는다 — 대조할 것이 없으면 확인이 아니다. */}
      {row.sourceText ? <SourceText text={row.sourceText} /> : null}

      {review ? (
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field mt-3 px-3 py-2">
          {reviewReason(row)}
        </p>
      ) : null}

      {row.status === "failed" ? (
        <p role="status" className="text-caption text-ink-muted mt-3">
          {josa(row.label, "은/는")} 등록하지 못했어요. 저장된 것은 없어요.
        </p>
      ) : null}
    </div>
  );
}

/** 🚨 OCR 로 들어온 외부 텍스트다. 텍스트로만 그린다 (apps/web/CLAUDE.md §4). */
function SourceText({ text }: { text: string }) {
  return (
    <p className="text-caption text-ink-subtle mt-3">
      검사지에 적힌 것 <span className="text-ink-muted">{text}</span>
    </p>
  );
}

function SummaryRow({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex gap-3">
      {/* 🚨 폭을 고정하지 않는다. 글자를 키우면 라벨이 잘려서 무엇을 등록하는지가 가려진다. */}
      <dt className="text-body-sm text-ink-subtle shrink-0 basis-24">{term}</dt>
      <dd className="text-body-sm text-ink min-w-0">{value}</dd>
    </div>
  );
}

/**
 * 고른 검사지. 🚨 **승인 화면에 원본을 함께 세운다** — 옮겨 적은 것이 맞는지 대조할 것이
 * 없으면 그건 확인이 아니다.
 *
 * 🚨 **시트일 때는 96px 이었다.** 시트 높이 안에서 배너·목록과 경쟁하느라 그 값까지 내려갔는데,
 *    화면으로 나오면서 그 제약이 사라졌다. 검사지를 화면에서 읽을 수 있을 만큼은 준다.
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
      className="rounded-card border-line bg-surface-muted h-56 w-full border object-contain"
    />
  );
}
