"use client";

import { Button } from "@/components/ui/button";

/**
 * "고쳐 쓰기" 버튼. 🚨 입력창에 글이 있으면 잠그고 **왜 잠겼는지를 글자로** 말한다 — 눌러도 아무 일이
 * 없는 버튼을 만들지 않는다.
 */
export function RewriteButton({ blocked, onClick }: { blocked: boolean; onClick: () => void }) {
  return (
    <div className="flex flex-col items-start gap-1">
      <Button variant="tertiary" size="compact" disabled={blocked} onClick={onClick}>
        고쳐 쓰기
      </Button>
      {blocked ? (
        <p className="text-caption text-ink-subtle">
          입력창에 쓰던 글을 보내거나 지우면 고쳐 쓸 수 있어요.
        </p>
      ) : null}
    </div>
  );
}
