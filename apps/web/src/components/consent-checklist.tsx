"use client";

import { ButtonLink } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  isConsentChecked,
  policyHref,
  POLICY_TEXT_PENDING,
  type ConsentChecked,
  type ConsentChoice,
} from "@/lib/consent";
import type { Policy } from "@/lib/api";

/**
 * 동의 항목 목록. **가입 동의(계정)와 01 아이 만들기(아이)가 같이 쓴다.**
 *
 * 왜 한 컴포넌트인가 — 두 화면이 묻는 것은 다르지만 **묻는 방식은 같아야 한다.** 한쪽만
 * 전문 보기가 없거나 `[필수]` 표기가 다르면, 부모는 같은 종류의 물음을 두 가지 무게로 읽는다.
 *
 * 🚨 **그리는 값은 전부 서버가 준다** (#90). 제목 · 근거 조문 · 필수 여부 · 순서 · 그리고
 *    동의로 기록될 버전까지 `GET /policies` 의 응답이다. 화면이 들고 있는 것은 체크박스 밑
 *    한 줄(`copy.description`)뿐이다.
 *
 * 🚨 **전문은 화면이 그리지 않는다.** 서버가 저장해 둔 정본 HTML 을 새 창으로 연다 —
 *    화면이 본문을 직접 그리면 문단을 숨기거나 순서를 바꿀 수 있어 "보호자가 본 글" 을
 *    증명하지 못한다 (멘토 #71-4 · #179). 정본이 아직 없으면 **버튼을 숨긴다.**
 *
 * 🚨 **"전체 동의" 를 두지 않는다.** 민감정보(`child_health`)는 다른 동의와 **구분해서**
 *    받아야 한다 (개인정보보호법 제23조). 한 번에 쓸어 담는 버튼이 그 구분을 없앤다.
 * 🚨 **선택 동의도 기본값은 꺼짐이다.** 미리 체크해 두면 "고르지 않음" 이 동의가 된다.
 * 🚨 **승인 게이트가 아니다** — `btn-approve` · `caution` 색을 쓰지 않는다. 그 둘은 되돌릴 수
 *    없는 2곳(캘린더 쓰기 · 건강 기록 확정) 전용이다 (최상위 CLAUDE.md §2).
 */
export function ConsentChecklist({
  choices,
  checked,
  onChange,
}: {
  choices: ConsentChoice[];
  checked: ConsentChecked;
  /** 🚨 체크는 boolean 이 아니라 **어느 버전에 동의했는가**로 기록된다 (`lib/consent.ts`). */
  onChange: (policy: Policy, next: boolean) => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      {choices.map(({ policy, copy }) => {
        const href = policyHref(policy);

        return (
          <Card key={policy.scope}>
            <Checkbox
              checked={isConsentChecked(policy, checked)}
              onChange={(next) => onChange(policy, next)}
              label={
                <>
                  {/* 🚨 서버의 `required` 만 본다. 화면이 따로 표를 들고 있으면 두 곳이 어긋난다. */}
                  <span className="text-ink-muted">{policy.required ? "[필수] " : "[선택] "}</span>
                  {policy.label}
                </>
              }
              description={copy?.description}
            />
            {policy.legal_basis ? (
              <p className="text-caption text-ink-subtle mt-1 pl-8">{policy.legal_basis}</p>
            ) : null}
            {href ? (
              /* 🚨 10 설정과 **같은 말**을 쓴다. 같은 페이지를 여는데 화면마다 이름이 다르면
                 두 곳을 오가는 사람이 다른 것으로 읽는다.
                 🚨 새 창이다 — 전문을 읽고 돌아왔을 때 체크가 그대로 있어야 한다. */
              <div className="pl-6">
                <ButtonLink external href={href} variant="tertiary" size="compact">
                  전문 보기
                </ButtonLink>
              </div>
            ) : (
              /* 🚨 **대신 보여줄 글을 만들지 않는다.** 예전에는 화면이 들고 있던 요약을
                 폈는데, 그건 서버가 기록하는 글이 아니라서 "보호자가 본 글" 과 어긋났다.
                 지금 사실은 "아직 없다" 하나뿐이고, 그대로 말한다.
                 🚨 설명·근거와 **같은 들여쓰기**로 세운다. 버튼 자리(`pl-6`)에 두면 법적 근거
                    줄과 한 칸 어긋나 같은 문단이 두 번 꺾인 것처럼 보인다. */
              <p className="text-caption text-ink-subtle mt-2 pl-8">{POLICY_TEXT_PENDING}</p>
            )}
          </Card>
        );
      })}
    </div>
  );
}
