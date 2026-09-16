"use client";

import { Camera, ChevronRight, Images, type LucideIcon } from "lucide-react";
import { useRef, useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";

/**
 * 사진 한 장을 어디서 가져올지 고르는 시트 (03 홈 · 09 캘린더 · 08 의 되돌아온 자리).
 *
 * 🚨 **고르는 화면을 따로 만들지 않는다.** 사진을 고르는 것은 화면 하나를 채울 일이 아니라
 *    **두 갈래 한 번**이라, 부모가 카메라 버튼을 누른 그 자리에서 끝나고 바로 읽은 결과로
 *    넘어간다. 08 은 "읽어낸 것을 확인하는 화면" 하나만 맡는다.
 *
 * 🚨 **입력이 두 개인 이유는 `capture` 때문이다.** `accept="image/*"` 만 주면 모바일 브라우저가
 *    "촬영 / 앨범" 선택지를 자기 방식으로 한 번 더 띄운다 — 우리 시트에서 이미 고른 것을
 *    OS 가 또 묻는 꼴이다. `capture="environment"` 가 붙은 입력은 카메라로 바로 간다.
 *    ⚠️ 데스크톱 브라우저는 `capture` 를 무시하고 둘 다 파일 대화상자를 연다. 실제 사용 기기가
 *    폰이라(PRODUCT.md) 그 쪽을 기준으로 잡았고, 데스크톱에서는 두 줄이 같은 일을 한다.
 *
 * 🚨 **고른 파일을 여기서 올리지 않는다.** 이 컴포넌트는 `File` 하나를 돌려주고 끝난다 —
 *    업로드·스트림·저장은 전부 08 화면이 소유한다. 시트가 요청까지 들면 홈과 캘린더가
 *    같은 흐름을 두 벌 갖게 된다.
 */
export function PhotoSourceSheet({
  open,
  onClose,
  onPick,
}: {
  open: boolean;
  onClose: () => void;
  /** 고른 파일. 호출자가 08 로 넘긴다. */
  onPick: (file: File) => void;
}) {
  const cameraRef = useRef<HTMLInputElement>(null);
  const albumRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);

  function handleChange(input: HTMLInputElement) {
    const picked = input.files?.[0];
    // 취소는 실패가 아니다 — 아무 말도 하지 않고 시트를 그대로 둔다 (문구 규칙 · PRODUCT.md).
    if (!picked) return;

    // 🚨 `accept` 는 브라우저가 지켜 주기를 기대하는 값이라 여기서 한 번 더 본다.
    if (!picked.type.startsWith("image/")) {
      setError("사진 파일만 넣을 수 있어요.");
      input.value = "";
      return;
    }

    setError(null);
    // 🚨 같은 사진을 다시 고를 수 있어야 한다 — 비우지 않으면 change 가 안 뜬다.
    input.value = "";
    onPick(picked);
  }

  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      title="사진으로 적기"
      description="읽어낸 내용을 보여드리고, 승인하기 전에는 아무것도 저장하지 않아요."
      // 🚨 다른 시트(07 기억 상세 · 06 승인)와 같이 닫는 버튼을 세운다. 웹뷰에는 ESC 가 없어서
      //    스크림 탭 하나만 남으면 닫는 길이 화면에 안 보인다. "취소" 가 아니라 "닫기" 다 —
      //    아직 시작한 것이 없어서 취소할 것도 없다.
      footer={
        <Button variant="secondary" block onClick={onClose}>
          닫기
        </Button>
      }
    >
      {/* 입력은 시트 안에 두고 화면에서는 감춘다 — 누르는 것은 아래 두 줄이다. */}
      <input
        ref={cameraRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => handleChange(e.currentTarget)}
      />
      <input
        ref={albumRef}
        type="file"
        accept="image/*"
        className="sr-only"
        tabIndex={-1}
        aria-hidden
        onChange={(e) => handleChange(e.currentTarget)}
      />

      <ul className="flex flex-col">
        <SourceRow
          icon={Camera}
          label="사진 촬영"
          hint="알림장이나 식단표를 지금 찍어요"
          onClick={() => cameraRef.current?.click()}
        />
        <SourceRow
          icon={Images}
          label="앨범에서 고르기"
          hint="오늘 찍어 둔 사진에서 골라요"
          onClick={() => albumRef.current?.click()}
        />
      </ul>

      {error ? (
        // 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3).
        <p
          role="status"
          className="bg-surface-muted rounded-field text-body-sm text-ink-muted mt-3 p-3"
        >
          {error}
        </p>
      ) : null}

      <p className="text-caption text-ink-subtle mt-4">
        얼굴이나 사람은 분석하지 않아요. 문서는 글자만 읽고, 아이 활동 사진은 활동 태그만 뽑아요.
      </p>
    </BottomSheet>
  );
}

/**
 * 시트 안의 한 줄. 🚨 **카드로 만들지 않는다** — 두 갈래를 고르는 자리라 줄이 맞고,
 * 상자 두 개를 세우면 시트 안에서 카드 안의 카드가 된다.
 */
function SourceRow({
  icon,
  label,
  hint,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <li className="border-line border-b last:border-b-0">
      <button
        type="button"
        onClick={onClick}
        // 🚨 `active:` 를 빠뜨리면 웹뷰에서 눌러도 아무 반응이 없다 (apps/web/CLAUDE.md §5).
        className="min-h-touch ease-standard hover:bg-surface-muted active:bg-surface-muted flex w-full items-center gap-3 py-3 text-left transition-colors duration-120"
      >
        <IconTile icon={icon} />
        <span className="min-w-0 flex-1">
          <span className="text-body text-ink block">{label}</span>
          <span className="text-body-sm text-ink-muted mt-0.5 block">{hint}</span>
        </span>
        {/* 🚨 아이콘은 lucide 한 곳에서 온다 — 손으로 그린 SVG 를 섞지 않는다 (§3 아이콘). */}
        <ChevronRight
          aria-hidden
          size={ICON_SIZE.sm}
          strokeWidth={ICON_STROKE}
          className="text-ink-subtle shrink-0"
        />
      </button>
    </li>
  );
}
