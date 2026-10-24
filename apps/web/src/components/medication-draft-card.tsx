"use client";

import { ArrowRight } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardFailed } from "@/components/ui/card";
import { DateField } from "@/components/ui/date-field";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { TextInput } from "@/components/ui/text-input";
import { cn } from "@/lib/cn";
import { formatDay, parseISODate } from "@/lib/format";
import {
  MED_FORM_LABEL,
  MISSING_REASON,
  STORAGE_LABEL,
  editableOf,
  medicationDiff,
  missingFields,
  timesLabel,
  type MedicationFields,
} from "@/lib/medication";
import type { MedicationDraft, MedicationForm, MedicationStorage } from "@/lib/api/types";

/**
 * 복약 초안 한 장 (#537 · `docs/web/medication-v1.md`).
 *
 * 🚨 **여기가 승인 게이트 ㉡ 이다** (건강 기록 확정). 제출이 코스와 기간만큼의 회차를 만든다 — 그래서
 *    `btn-approve` 를 쓴다. 새 게이트가 아니라 ㉡ 안에 메서드가 하나 는 것이다 (최상위 §2 — 딱 2곳).
 *    초안을 만드는 것은 아무것도 쓰지 않아서 게이트가 아니다.
 *
 * 🚨 **실제로 울릴 시각(`notice_times`)을 반드시 보여 준다.** 보호자가 승인하는 것은 "식후" 라는 말이 아니라
 *    그 말이 바뀐 시각이다 — "하루 세 번 식후" 가 08:30 · 12:30 · 19:30 이 된 것을 보고 넣어야 한다.
 * 🚨 **시각을 고치는 칸이 없다.** 시각은 서버가 기준 시각 + 오프셋으로 만든 값이라(#546) 화면이 다시 계산하지
 *    않는다. 다르면 대화로 다시 말하게 한다 — 그 문장을 카드에 적는다.
 * 🚨 **비어 온 필수 칸(`missing`)을 오늘로 채우지 않는다.** 기간이 비어 오는 일이 가장 많다 — 발화에 기간이
 *    없으면 서버가 짐작하지 않는다. 보호자가 고르기 전에는 제출이 잠기고, 왜 잠겼는지 버튼 위에 말한다.
 * 🚨 **취소하면 아무것도 부르지 않는다.** 카드에 "안 넣을래요" 를 두지 않는다 — 넣지 않을 사람은 시트를 닫는다.
 *
 * 🚨 **`caution` 은 버튼 옆 글자 한 덩이다** (`EventDraftCard` 와 같은 자리 · 게이트 2곳 전용 색).
 * 🚨 **도메인 색을 쓰지 않는다.** 건강 기록이라고 `health` 색을 칠하면 대화 안에서 도메인 색 규칙(한 화면 2색)을 넘는다.
 */

/** 게이트를 통과한 뒤. 🚨 보호자의 선택이 아니라 **서버가 저장을 끝냈는가**다. */
export type MedicationSubmitState = "idle" | "submitting" | "submitted" | "failed";

/**
 * 고를 수 있는 날의 범위 — **입력 제약**이다 (apps/web/CLAUDE.md §4 의 예외). 미래를 막지 않는다 — 내일부터
 * 먹일 약도 있다. 과거도 막지 않는다 — 이미 먹이기 시작한 약을 뒤늦게 넣는다(지난 날 회차는 서버가 만들지 않는다).
 */
const FROM_DATE = new Date(2020, 0, 1);
const TO_DATE = new Date(new Date().getFullYear() + 2, 11, 31);

const STORAGE_OPTIONS = (Object.keys(STORAGE_LABEL) as MedicationStorage[]).map((value) => ({
  value,
  label: STORAGE_LABEL[value],
}));
const MED_FORM_OPTIONS = (Object.keys(MED_FORM_LABEL) as MedicationForm[]).map((value) => ({
  value,
  label: MED_FORM_LABEL[value],
}));

export function MedicationDraftCard({
  draft,
  state = "idle",
  error,
  rejected = false,
  onSubmit,
}: {
  draft: MedicationDraft;
  state?: MedicationSubmitState;
  /** 제출이 실패한 이유. `state === "failed"` 일 때만 쓴다. */
  error?: string;
  /**
   * 다시 눌러도 같은 거절이 온다 (`consent_required` · `not_found`). 🚨 버튼을 잠근다 — 켜 두면 같은 실패만 되풀이한다.
   */
  rejected?: boolean;
  /** 🚨 보호자가 확인한 **최종 상태**를 통째로 넘긴다. 잠긴 동안에는 부르지 않는다. */
  onSubmit: (fields: MedicationFields) => void;
}) {
  /** 🚨 **정본은 여기다.** 제출은 이 값을 보낸다 — 서버가 보낸 `draft` 를 다시 읽지 않는다. */
  const [fields, setFields] = useState<MedicationFields>(() => editableOf(draft.schedule));
  /** 🚨 넣은 뒤에 보여줄 것은 로컬 값이 아니라 **보낸 값**이다 (`EventDraftCard` 의 `sent` 와 같은 이유). */
  const [sent, setSent] = useState<MedicationFields | null>(null);

  const done = state === "submitted";
  const busy = state === "submitting";
  const missing = missingFields(fields);
  const canSubmit = missing.length === 0 && !busy && !done && !rejected;
  const kind = draft.op === "update" ? "복약 수정" : "새 복약";

  function patch(next: Partial<MedicationFields>) {
    setFields((prev) => ({ ...prev, ...next }));
  }

  if (done) {
    const shown = sent ?? fields;
    return (
      <Card>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-label text-ink-subtle">{kind}</span>
          <span className="text-caption text-brand-ink bg-brand-soft rounded-field px-2 py-0.5">
            넣었어요
          </span>
        </div>
        <p className="text-body text-ink mt-3">{shown.title.trim()}</p>
        <p className="text-body-sm text-ink-muted mt-1">
          매일 {timesLabel(draft.notice_times)} · {formatDay(shown.starts_on)}부터{" "}
          {formatDay(shown.ends_on)}까지
        </p>
      </Card>
    );
  }

  // 🚨 시작 · 끝이 서로를 넘지 못하게 막는 것은 **입력 제약**이다. 날짜를 계산하지 않고 고를 수 있는 칸만 줄인다.
  const startsDate = parseISODate(fields.starts_on);
  const endsDate = parseISODate(fields.ends_on);

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-label text-ink-subtle">{kind}</span>
        {/* 🚨 아직 아무것도 저장되지 않았다는 것은 경고가 아니라 사실이라 중립 면이다. */}
        <span className="text-caption text-ink-muted bg-surface-muted rounded-field px-2 py-0.5">
          아직 저장되지 않았어요
        </span>
      </div>

      {/* 🚨 **알림 시각이 카드의 본체다** — 맨 위에 둔다. 서버 값 그대로다. */}
      <div className="border-line rounded-field mt-4 border px-3 py-2.5">
        <p className="text-label text-ink-muted">매일 알려드릴 시각</p>
        <p className="text-body text-ink mt-1">{timesLabel(draft.notice_times)}</p>
        <p className="text-caption text-ink-subtle mt-1">
          시각을 바꾸려면 대화에서 다시 말씀해 주세요. 예: 저녁 8시에 먹일게
        </p>
      </div>

      {/* 복용 이유는 고치는 칸이 아니다 — 증상 코드가 이 문장에서 나왔다 (`lib/medication.ts` 의 `editableOf`). */}
      {draft.schedule.indication_text ? (
        <p className="text-body-sm text-ink-muted mt-3">
          복용 이유: {draft.schedule.indication_text}
        </p>
      ) : null}

      <div className="mt-4 flex flex-col gap-4">
        <TextInput
          label="약 이름"
          value={fields.title}
          disabled={busy}
          onChange={(e) => patch({ title: e.target.value })}
        />
        <TextInput
          label="용량 (선택)"
          value={fields.dosage}
          disabled={busy}
          onChange={(e) => patch({ dosage: e.target.value })}
        />

        {/* 🚨 비어 와도 오늘로 채우지 않는다. 막는 말은 버튼 위 한 곳에서 한다 — 칸마다 같은 말을 두지 않는다. */}
        <DateField
          label="언제부터"
          placeholder="시작하는 날을 골라주세요"
          value={fields.starts_on}
          onChange={(next) => patch({ starts_on: next })}
          fromDate={FROM_DATE}
          toDate={endsDate ?? TO_DATE}
        />
        <DateField
          label="언제까지"
          placeholder="마지막 날을 골라주세요"
          value={fields.ends_on}
          onChange={(next) => patch({ ends_on: next })}
          fromDate={startsDate ?? FROM_DATE}
          toDate={TO_DATE}
        />

        {/* 🚨 보관 · 약 모양 · 특이사항은 **보호자가 적는 선택 칸**이다 — Agent 는 늘 비워 낸다. 투약의뢰서에 실린다. */}
        <Select
          label="보관 (선택)"
          shape="field"
          placeholder="고르지 않음"
          value={fields.storage}
          options={STORAGE_OPTIONS}
          onChange={(value) => patch({ storage: value })}
        />
        <Select
          label="약 모양 (선택)"
          shape="field"
          placeholder="고르지 않음"
          value={fields.med_form}
          options={MED_FORM_OPTIONS}
          onChange={(value) => patch({ med_form: value })}
        />
        <TextInput
          label="특이사항 (선택)"
          value={fields.note}
          disabled={busy}
          onChange={(e) => patch({ note: e.target.value })}
        />
      </div>

      {draft.before ? (
        <Diff rows={medicationDiff(draft.before, fields, draft.notice_times)} />
      ) : null}

      {error ? (
        // 🚨 "넣지 않았어요" 를 여기서 붙이지 않는다 — 응답을 못 받은 실패는 서버가 받았을 수 있다. 말은 그릇이 정한다.
        <CardFailed className="mt-4">
          <p>{error}</p>
        </CardFailed>
      ) : null}

      <div className="mt-5">
        {/* 🚨 **게이트의 마지막 확인은 버튼 옆에 선다** (`EventDraftCard` 와 같은 자리). 막는 것이 여럿이면 다 말한다. */}
        <div
          className={cn(
            "ease-standard mb-3 flex flex-col gap-1 transition-colors duration-120",
            canSubmit ? "text-caution" : "text-ink-subtle",
          )}
        >
          {missing.map((field) => (
            <p key={field} className="text-body-sm">
              {MISSING_REASON[field]}
            </p>
          ))}
          {/* 🚨 등록과 수정은 일어나는 일이 다르다 — 수정은 새 코스가 아니라 남은 회차를 바꾼 시각으로 다시 만든다.
              지난 회차와 체크한 회차는 서버가 그대로 둔다 (`docs/medication/medication-tables-v1.md` §5). */}
          {canSubmit ? (
            <p className="text-body-sm">
              {draft.op === "update"
                ? "누르면 아직 오지 않은 회차가 바뀐 내용으로 다시 만들어져요. 먹인 기록은 그대로 남아요. 함께 보는 보호자에게도 보여요."
                : "누르면 복약 일정이 등록되고 날마다 먹일 회차가 생겨요. 함께 보는 보호자에게도 보여요."}
            </p>
          ) : null}
        </div>
        <Button
          variant="approve"
          disabled={!canSubmit}
          aria-busy={busy}
          onClick={() => {
            setSent(fields);
            onSubmit(fields);
          }}
        >
          {busy ? <Spinner /> : null}
          {busy
            ? "넣는 중이에요"
            : draft.op === "update"
              ? "확인했어요, 이대로 바꿀게요"
              : "확인했어요, 복약 일정에 넣을게요"}
        </Button>
      </div>
    </Card>
  );
}

/** 수정 초안에서 달라지는 것. 🚨 "19:30 → 20:00" 처럼 값의 변화를 보여 준다 (#537). */
function Diff({ rows }: { rows: ReturnType<typeof medicationDiff> }) {
  if (rows.length === 0) {
    return (
      <p className="text-body-sm text-ink-muted border-line mt-4 border-t pt-3">
        원래 복약 일정과 달라진 것이 없어요.
      </p>
    );
  }
  return (
    <div className="border-line mt-4 border-t pt-3">
      <p className="text-label text-ink-muted">이렇게 달라져요</p>
      <dl className="mt-2 flex flex-col gap-1.5">
        {rows.map((row) => (
          <div key={row.term} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <dt className="text-caption text-ink-subtle w-16 shrink-0">{row.term}</dt>
            <dd className="text-body-sm text-ink-muted flex flex-wrap items-baseline gap-1.5">
              <span className="line-through">{row.from}</span>
              <ArrowRight
                aria-hidden
                size={ICON_SIZE.sm}
                strokeWidth={ICON_STROKE}
                className="translate-y-0.5"
              />
              <span className="text-ink">{row.to}</span>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
