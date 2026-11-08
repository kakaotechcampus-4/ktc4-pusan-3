"use client";

import { ImageOff } from "lucide-react";
import { useState } from "react";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";

/**
 * 08 사진 칸(`PhotoCard`) 안의 미리보기 한 장 (#723).
 *
 * 🚨 **못 그린 사진을 빈칸으로 두지 않는다.** 갤럭시의 고효율 사진(HEIC)은 고르고 올리는 데는
 *    문제가 없는데(서버가 연다 · `photo-source-sheet.tsx`), 안드로이드 웹뷰가 그 형식을 그리지 못해
 *    미리보기가 비어 보였다. 빈 회색 칸은 "사진이 안 올라갔다" 로 읽혀서 보호자가 다시 고른다 —
 *    그리지 못한 것이지 못 받은 것이 아니라고 글자로 말한다.
 * 🚨 **브라우저에서 변환하지 않는다.** HEIC 를 바꾸는 라이브러리는 무겁고, 사진 원본을 한 번 더
 *    메모리에 펼친다. 확인에 필요한 것은 사진이 아니라 아래의 읽어낸 글자다.
 */
export function PhotoPreview({ src, alt }: { src: string; alt: string }) {
  /** 그리지 못한 주소. 🚨 주소로 들고 있어서 다른 사진으로 바뀌면 저절로 다시 그려 본다. */
  const [failedSrc, setFailedSrc] = useState<string | null>(null);

  if (failedSrc === src) {
    return (
      // 🚨 `role="img"` 를 주지 않는다 — 그러면 안의 글자가 스크린리더에서 사라진다. 글자가 곧 대체 설명이다.
      <div className="text-ink-muted flex h-full w-full flex-col items-center justify-center gap-2 px-6 text-center">
        <ImageOff aria-hidden size={ICON_SIZE.lg} strokeWidth={ICON_STROKE} />
        <p className="text-body-sm">
          이 사진은 미리보기를 띄우지 못했어요. 글자를 읽는 데는 문제없어요.
        </p>
      </div>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      className="h-full w-full object-cover"
      onError={() => setFailedSrc(src)}
    />
  );
}
