"use client";

import { useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { TextInput } from "@/components/ui/text-input";
import { CATEGORY_OPTIONS, SEVERITY_OPTIONS, type ScanRow } from "@/components/safety-scan-fields";

/**
 * 11-2 검사지 — 옮겨 적은 **한 줄**을 고치는 시트.
 *
 * 🚨 **여기서 고친 값이 등록되는 값이다.** 모델이 읽은 것을 보호자가 덮어쓰는 유일한 자리다.
 *    최상위 §2 가 허용하는 것은 의료 기록을 **옮겨 적기**까지고, 빠진 칸을 메우는 것은 언제나
 *    사람이다 — 그 "사람" 이 서는 자리가 이 시트다.
 *
 * 🚨 **잘 읽은 줄도 연다.** 검사지를 잘못 읽었는지는 보호자만 알고, 고칠 길이 못 읽은 줄에만
 *    있으면 "기계가 확신한 것은 못 고친다" 가 된다.
 *
 * 🚨 **승인 게이트가 아니다.** 이 시트는 아무것도 저장하지 않는다 — 등록은 화면 아래
 *    `btn-approve` 한 번뿐이다 (게이트는 2곳이고 늘리지 않는다 · 최상위 §2).
 *    그래서 `caution` 도 `btn-approve` 도 쓰지 않고 스크림 탭으로 닫힌다.
 *
 * 🚨 **화면 하나로 만들지 않는다.** 검사지 한 장에 줄이 여럿이라 고칠 때마다 화면을 옮기면
 *    목록에서의 자리를 잃는다 — 시트는 덮고, 닫히면 있던 자리로 돌아온다.
 *
 * 🚨 **`type` 을 묻지 않는다.** 이 경로로 들어오는 것은 알레르기 검사지라 전부 `allergy` 고,
 *    지병(`condition`)은 직접 적기가 받는다. 여기서 종류를 고르게 하면 검사지에 없는 것을
 *    검사지에서 온 것처럼 등록하는 길이 생긴다.
 */
export function SafetyScanRowSheet({
  row,
  onClose,
  onSave,
  onDrop,
}: {
  /** `null` 이면 닫혀 있다. */
  row: ScanRow | null;
  onClose: () => void;
  /** 고친 값. 호출부가 목록의 그 줄을 갈아 끼운다. */
  onSave: (next: ScanRow) => void;
  /** 이 줄을 등록 대상에서 뺀다. */
  onDrop: (id: string) => void;
}) {
  return (
    <BottomSheet
      open={row !== null}
      onClose={onClose}
      title="옮겨 적은 것 고치기"
      description="여기서 고친 값이 등록돼요. 등록은 아래 승인 버튼을 눌러야 돼요."
    >
      {/* 🚨 열 때마다 그 줄의 값에서 시작한다 — 앞 줄을 고치던 값이 남으면 다른 줄에 덮인다. */}
      {row ? <RowForm key={row.id} row={row} onSave={onSave} onDrop={onDrop} /> : null}
    </BottomSheet>
  );
}

function RowForm({
  row,
  onSave,
  onDrop,
}: {
  row: ScanRow;
  onSave: (next: ScanRow) => void;
  onDrop: (id: string) => void;
}) {
  const [label, setLabel] = useState(row.label);
  const [category, setCategory] = useState(row.category);
  /** 🚨 `""`(못 읽음)과 `"unknown"`(모르겠어요)을 같은 칸에서 다룬다 — 화면의 말은 하나다. */
  const [severity, setSeverity] = useState(row.severity ?? "unknown");
  const [reactions, setReactions] = useState(row.reactions.join(", "));
  const [errors, setErrors] = useState<{ label?: string; category?: string }>({});

  function submit() {
    const nextLabel = label.trim();
    if (nextLabel === "" || category === "") {
      setErrors({
        ...(nextLabel === "" ? { label: "검사지를 보고 적어주세요" } : {}),
        ...(category === "" ? { category: "무엇에 대한 것인지 골라주세요" } : {}),
      });
      return;
    }

    onSave({
      ...row,
      label: nextLabel,
      category,
      // 🚨 "모르겠어요" 는 값을 **안 보내는** 것이다. 심각도를 추측해 채우지 않는다 (NF-03).
      severity: severity === "unknown" ? null : severity,
      reactions: reactions
        .split(",")
        .map((v) => v.trim())
        .filter((v) => v !== ""),
      /** 🚨 보호자가 직접 봤으므로 원문이 없어도 대조가 끝났다 — 이 전환이 "잘 읽은 것" 으로 내린다. */
      confirmed: true,
      /**
       * 🚨 **확인하면 고른 상태가 된다.** 버튼이 "이 내용으로 확인" 이라고 말해 놓고 그 줄이
       *    체크 안 된 채로 목록에 내려가면, 보호자는 채워 넣은 줄이 등록되는 줄 알고 승인한다 —
       *    실제로 "확인이 필요해요 2건 → 1건" 이 됐는데 등록 건수는 1건 그대로였다.
       *    빼고 싶으면 아래 "이 줄은 등록하지 않기" 나 목록의 체크박스가 있다.
       */
      checked: true,
    });
  }

  return (
    <div className="flex flex-col gap-4">
      {/* 🚨 **무엇을 보고 옮겼는지 폼 안에도 세운다.** 고치는 자리에서 원문이 안 보이면
          보호자가 목록으로 돌아가 외운 다음 다시 들어와야 한다. */}
      {row.sourceText ? (
        <div className="bg-surface-muted rounded-field px-3 py-2">
          <p className="text-caption text-ink-subtle">검사지에 적힌 것</p>
          {/* 🚨 OCR 로 들어온 외부 텍스트다. 텍스트로만 그린다 (apps/web/CLAUDE.md §4). */}
          <p className="text-body-sm text-ink mt-0.5 whitespace-pre-line">{row.sourceText}</p>
        </div>
      ) : (
        <p className="text-body-sm text-ink-muted bg-surface-muted rounded-field px-3 py-2">
          이 줄은 검사지 원문을 읽지 못했어요. 검사지를 직접 보고 적어주세요.
        </p>
      )}

      <TextInput
        label="무엇인가요"
        hint="검사지에 적힌 이름 그대로가 좋아요."
        value={label}
        error={errors.label}
        onChange={(e) => {
          setLabel(e.target.value);
          if (errors.label) setErrors((prev) => ({ ...prev, label: undefined }));
        }}
        autoComplete="off"
      />

      <Select
        label="분류"
        value={category}
        options={CATEGORY_OPTIONS}
        error={errors.category}
        onChange={(next) => {
          setCategory(next);
          if (errors.category) setErrors((prev) => ({ ...prev, category: undefined }));
        }}
      />

      {/* 🚨 **"모르겠어요" 가 기본값이다** (직접 적기와 같은 규칙). 검사지의 등급을 심각도로
          옮기는 것은 판단이라, 못 읽었으면 비워 두는 편이 지어내는 것보다 낫다. */}
      <Select
        label="얼마나 심한가요"
        value={severity}
        options={SEVERITY_OPTIONS}
        onChange={setSeverity}
      />

      <TextInput
        label="어떤 증상이 있었나요"
        hint="쉼표로 나눠 적어요. 없으면 비워 둬도 돼요."
        value={reactions}
        onChange={(e) => setReactions(e.target.value)}
        autoComplete="off"
      />

      <div className="flex flex-col gap-2">
        <Button block onClick={submit}>
          이 내용으로 확인
        </Button>
        {/* 🚨 `btn-danger` 가 아니다 — 아직 저장된 것이 없어서 지우는 것이 아니라 **안 싣는**
            것이다. 빨강은 알레르기·건강 **중단**에만 쓴다 (디자인 시스템 §3). */}
        <Button variant="tertiary" block onClick={() => onDrop(row.id)}>
          이 줄은 등록하지 않기
        </Button>
      </div>
    </div>
  );
}
