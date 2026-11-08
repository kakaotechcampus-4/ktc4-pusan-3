import { TriangleAlert } from "lucide-react";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { cn } from "@/lib/cn";

/**
 * 추천 하나에 붙는 위험 경고 문구 (#402 · #672). 05 의 개인화 줄(`SuggestionList`)과 일반 카드
 * (`GeneralSuggestionCard`)가 같이 쓴다 — 카드는 나누되 이 조각만 공유한다.
 *
 * 🚨 **서버 코드 문구 그대로다.** 위험 용어 사전의 `warning_text` 상수라 모델이 쓰지 않았고, 화면도 고치거나
 *    줄이지 않는다. 고르기 전에 읽어야 하는 말이라 **접지 않는다.** 둘 이상이면 다 보인다.
 * 🚨 **`caution` · `danger` 색을 쓰지 않는다** — 둘 다 승인 게이트와 알레르기 차단 전용이라, 경고에 입히면
 *    확인 버튼을 눌러야 하는 자리로 읽힌다 (#380 · `docs/web/screen-rules-v1.md` §2). 이유 · 기준 문구(`ink-muted`)
 *    보다 진한 `ink` 글자와 아이콘으로만 눈에 띄게 한다. 아이콘은 `aria-hidden` 이고 뜻은 목록 이름이 진다.
 * 🚨 **근거가 아니다.** "사용한 기록" 목록 밖, 제안 문장 아래에 둔다 — 체크박스 라벨에도 섞지 않는다.
 *
 * 비어 있으면 아무것도 그리지 않는다 — 빈 목록이나 라벨만 선 줄을 세우지 않는다.
 */
export function SuggestionWarnings({
  warnings,
  className,
}: {
  warnings: readonly string[];
  className?: string;
}) {
  const lines = warnings.map((text) => text.trim()).filter(Boolean);
  if (lines.length === 0) return null;

  return (
    <ul aria-label="주의할 점" className={cn("flex flex-col gap-1.5", className)}>
      {lines.map((text) => (
        <li key={text} className="flex items-start gap-2">
          {/* 글자 첫 줄 가운데에 맞춘다 (body-sm 줄 높이 약 22 · 아이콘 16) */}
          <TriangleAlert
            aria-hidden
            size={ICON_SIZE.sm}
            strokeWidth={ICON_STROKE}
            className="text-ink-muted mt-0.5 shrink-0"
          />
          <p className="text-body-sm text-ink min-w-0 break-keep">{text}</p>
        </li>
      ))}
    </ul>
  );
}
