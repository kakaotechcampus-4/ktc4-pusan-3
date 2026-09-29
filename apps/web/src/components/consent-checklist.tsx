"use client";

import { useState } from "react";

import { BottomSheet } from "@/components/ui/bottom-sheet";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { TERMS_NOT_FINAL, type ConsentItem, type ConsentScope } from "@/lib/consent";

/**
 * 동의 항목 목록 + 전문 시트. **가입 동의(계정 2건)와 01 아이 만들기(아이 2건)가 같이 쓴다.**
 *
 * 왜 한 컴포넌트인가 — 두 화면이 묻는 것은 다르지만 **묻는 방식은 같아야 한다.** 한쪽만
 * 전문 시트가 없거나 `[필수]` 표기가 다르면, 부모는 같은 종류의 물음을 두 가지 무게로 읽는다.
 *
 * 🚨 **"전체 동의" 를 두지 않는다.** 민감정보(`child_health`)는 다른 동의와 **구분해서**
 *    받아야 한다 (개인정보보호법 제23조). 한 번에 쓸어 담는 버튼이 그 구분을 없앤다.
 * 🚨 **선택 동의도 기본값은 꺼짐이다.** 미리 체크해 두면 "고르지 않음" 이 동의가 된다.
 * 🚨 **승인 게이트가 아니다** — `btn-approve` · `caution` 색을 쓰지 않는다. 그 둘은 되돌릴 수
 *    없는 2곳(캘린더 쓰기 · 건강 기록 확정) 전용이다 (최상위 CLAUDE.md §2).
 */
export function ConsentChecklist({
  items,
  checked,
  onChange,
}: {
  items: ConsentItem[];
  checked: Partial<Record<ConsentScope, boolean>>;
  onChange: (scope: ConsentScope, next: boolean) => void;
}) {
  /** 전문을 펼쳐 볼 항목. null 이면 시트가 닫혀 있다. */
  const [detail, setDetail] = useState<ConsentItem | null>(null);

  return (
    <>
      <div className="flex flex-col gap-3">
        {items.map((item) => (
          <Card key={item.scope}>
            <Checkbox
              checked={checked[item.scope] === true}
              onChange={(next) => onChange(item.scope, next)}
              label={
                <>
                  <span className="text-ink-muted">{item.required ? "[필수] " : "[선택] "}</span>
                  {item.label}
                </>
              }
              description={item.description}
            />
            {item.legalBasis ? (
              <p className="text-caption text-ink-subtle mt-1 pl-8">{item.legalBasis}</p>
            ) : null}
            <div className="pl-6">
              {/* 🚨 10 설정과 **같은 말**을 쓴다. 같은 시트를 같은 `details` 로 여는데
                  화면마다 이름이 다르면 두 곳을 오가는 사람이 다른 것으로 읽는다. */}
              <Button variant="tertiary" size="compact" onClick={() => setDetail(item)}>
                내용 보기
              </Button>
            </div>
          </Card>
        ))}
      </div>

      <BottomSheet
        open={detail !== null}
        onClose={() => setDetail(null)}
        /* 🚨 약관 전문은 통째로 `font-doc` 이다. 제목만 손글씨로 남기지 않는다. */
        variant="document"
        title={detail?.label ?? ""}
        description={detail?.legalBasis}
        footer={
          /* 🚨 10 설정의 전문 시트와 **같은 버튼**이다. 같은 시트를 여는 두 화면에서
             한쪽만 채운 버튼이면 같은 행동이 다른 무게로 읽힌다. */
          <Button block variant="secondary" onClick={() => setDetail(null)}>
            닫기
          </Button>
        }
      >
        {detail ? <ConsentDetail item={detail} /> : null}
      </BottomSheet>
    </>
  );
}

/**
 * 🚨 약관 전문이 아니다. 보관 기간·삭제 범위가 아직 미정이라(최상위 CLAUDE.md §10) 정식
 *    문구를 쓸 수 없고, 없는 조항을 지어 넣으면 그대로 배포된다. 확정된 사실만 보여주고
 *    아직 최종본이 아니라는 것을 화면에 밝힌다.
 */
function ConsentDetail({ item }: { item: ConsentItem }) {
  return (
    <div className="flex flex-col gap-5">
      {item.details.map((section) => (
        <section key={section.heading}>
          <h3 className="text-section text-ink">{section.heading}</h3>
          <ul className="text-body-sm text-ink-muted marker:text-ink-subtle mt-2 flex list-disc flex-col gap-1.5 pl-5">
            {section.lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </section>
      ))}
      <p className="text-caption text-ink-subtle border-line border-t pt-4">{TERMS_NOT_FINAL}</p>
    </div>
  );
}

/**
 * 필수 항목이 전부 체크됐는가.
 *
 * 🚨 **막는 것은 `required` 뿐이다.** 목록 길이로 세지 않는다 — 선택 동의를 이 화면에
 *    올리는 날 조용히 그것까지 막게 된다. 필수/선택의 정본은 `lib/consent.ts` 다.
 */
export function requiredConsentsChecked(
  items: ConsentItem[],
  checked: Partial<Record<ConsentScope, boolean>>,
): boolean {
  return items.filter((i) => i.required).every((item) => checked[item.scope] === true);
}
