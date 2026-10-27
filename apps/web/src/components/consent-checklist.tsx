"use client";

import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { SkeletonGroup, SkeletonText } from "@/components/ui/skeleton";
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
              /**
               * 🚨 **상자가 아니라 밑줄 친 글자다** (디자인 시스템 §7 동의 목록 — "전문으로 가는
               *    길은 줄의 이름이다"). 하는 일이 **읽으러 가는 것**이라 10 설정이 같은 약관을
               *    여는 방식과 같아야 하고, 카드 안에서 제일 무거운 것이 이 길이 되면 안 된다
               *    (84×44 상자가 12~15px 글자뿐인 카드에서 그랬다).
               * 🚨 **밑줄을 지우지 않는다** — 이 시스템에는 누를 수 있는 글자가 거의 없어서
               *    표시가 없으면 아무도 안 누른다 (색은 단독 신호가 될 수 없다 · 문서 §3).
               * 🚨 **보이는 것은 글자지만 누르는 자리는 44px 이다** (`py-3`). 칩이 28px 표식에
               *    투명 여백으로 44 를 채우는 것과 같은 방식이다 (문서 §9).
               * 🚨 위 글자와 **같은 들여쓰기**(`ml-8`)로 선다. 상자였을 때는 테두리를 맞추려고
               *    `pl-6` 이었는데, 그러면 글자끼리는 8px 어긋난다.
               * 🚨 10 설정과 **같은 말**을 쓴다 — 같은 페이지를 여는데 화면마다 이름이 다르면
               *    두 곳을 오가는 사람이 다른 것으로 읽는다.
               * 🚨 새 창이다 — 전문을 읽고 돌아왔을 때 체크가 그대로 있어야 한다.
               */
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-body-sm text-ink ease-standard decoration-line-strong hover:decoration-ink-muted active:text-ink-muted mt-1 -mb-2 ml-8 inline-block py-3 underline decoration-1 underline-offset-4 transition-colors duration-120 focus-visible:-outline-offset-2"
              >
                전문 보기
              </a>
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

/**
 * 약관을 받는 동안의 목록. `ConsentChecklist` 카드와 **같은 상자 · 같은 줄 높이**다 — 체크 줄(이름 + 설명) ·
 * 법적 근거 · "전문 보기" 를 잡는다 (CLS · `components/ui/skeleton.tsx`). 스피너 한 줄로 기다리던 때는 약관이
 * 오는 순간 카드 두 장이 자라 아래 "시작하기" 가 밀렸다. 🚨 진짜 카드의 여백을 바꾸면 여기도 같이 바꾼다.
 */
export function ConsentChecklistSkeleton({ count = 2 }: { count?: number }) {
  return (
    <SkeletonGroup label="동의 항목을 불러오는 중" className="flex flex-col gap-3">
      {Array.from({ length: count }, (_, i) => (
        <Card key={i}>
          <div className="min-h-touch flex items-start gap-3 py-2">
            {/* 🚨 빈 체크 칸처럼 그리지 않는다 — 법적 동의 앞에 누를 수 없는 체크 칸이 서는 셈이다. 채운 자리만 잡는다. */}
            <span className="bg-line animate-skeleton mt-0.5 size-5 shrink-0 rounded-full" />
            <span className="flex flex-1 flex-col gap-1">
              <SkeletonText width="2/3" />
              <SkeletonText size="caption" width="full" />
            </span>
          </div>
          <SkeletonText size="caption" width="1/2" className="mt-1 pl-8" />
          <SkeletonText size="body-sm" width="1/4" className="mt-1 -mb-2 py-3 pl-8" />
        </Card>
      ))}
    </SkeletonGroup>
  );
}
