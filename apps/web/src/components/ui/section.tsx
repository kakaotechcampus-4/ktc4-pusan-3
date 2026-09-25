"use client";

import { Plus, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { CardFailed } from "@/components/ui/card";
import { CountChip } from "@/components/ui/chip";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButton } from "@/components/ui/icon-button";

/**
 * 화면 안의 **구역**. 11 아이 프로필이 원본이고 02 아이 정보도 같은 것을 쓴다.
 *
 * 🚨 **구역마다 카드로 두르지 않는다.** 02 는 한동안 네 덩어리가 각자 테두리를 갖고 있었는데,
 *    그러면 화면이 **상자의 나열**이 된다 (10 설정의 줄을 카드로 감싸지 않는 것과 같은 이유 ·
 *    디자인 시스템 §7). 상자는 안에 든 목록·카드가 가진다.
 */
/**
 * 구역 제목 + 설명 + 내용.
 *
 * 🚨 **라벨은 `label` / `brand` 다** (디자인 시스템 §2-2 "브랜드색이 서는 자리" 표 첫 줄 ·
 *    03 홈의 `Section` 과 같은 device). 이 화면은 한동안 `section` / `ink` 를 썼는데,
 *    그게 §2-2 가 경고한 상태를 그대로 만들었다 — **"브랜드를 어디에 둘지 정해 두지 않으면
 *    화면이 회색 카드의 나열이 된다."** 이 화면은 도메인 4색을 하나도 안 쓰기로 했고(위 머리말)
 *    `caution`·`danger` 는 알레르기 구역 전용이라, 브랜드가 규칙적으로 들어오는 자리가
 *    섹션 라벨 말고는 남지 않는다.
 *
 * 🚨 **구역을 카드로 감싸지 않는다** — 세 구역을 전부 흰 상자에 넣으면 상자가 줄줄이 서고
 *    (04 저장 결과에서 한 번 그랬다), 무엇이 다른 무게인지가 사라진다. 무게는 **구역 안의
 *    내용**이 진다. 카드는 구역이 아니라 **묶이는 내용**이 받는다 (03 홈과 같은 처리).
 *
 * `count` 는 **쌓인 건수를 그대로 보여주는 자리**다 (최상위 CLAUDE.md §2 · `EmptyState` 가
 * 건수를 필수로 받는 것과 같은 이유). 🚨 **여기에 키·몸무게 값을 넣지 않는다** — 건수는
 * 기록의 수고 지표가 아니지만, 잰 값이 큰 글자로 서는 순간 그건 지표다 (`DESIGN.md` Don't).
 *
 * 🚨 **0 건이면 넘기지 않는다.** 그때는 `EmptyState` 가 같은 숫자를 훨씬 크게 말하고 있어서,
 *    한 구역 안에 같은 건수가 두 번 선다 (실제로 그렇게 보였다). 건수를 숨기는 게 아니라
 *    **말하는 자리를 하나로 두는 것**이다 — 0 도 정보라는 규칙은 `EmptyState` 가 계속 진다.
 */
export function Section({
  title,
  description,
  count,
  action,
  children,
}: {
  title: string;
  /**
   * 구역이 하는 일을 한 줄로. 🚨 **설명이 필요 없는 구역에는 넘기지 않는다** — 라벨만으로
   * 뜻이 서는데 문장을 하나 더 깔면 그건 설명이 아니라 빈 줄이다 ("기본 정보" 가 그렇다).
   */
  description?: string;
  /** 쌓인 건수. 아직 안 불러왔거나 0 이면 넘기지 않는다 (위 주석). */
  count?: number;
  /**
   * 이 구역에 **더하는** 행동. 🚨 **목록 아래 전체 폭 버튼으로 두지 않는다** — `secondary` 는
   * `surface` 바탕에 테두리라 목록 상자와 실루엣이 같고, 목록 바로 밑에 붙으면 둥근 사각형이
   * 두 개 연달아 선다. 구역이 셋이라 그것만으로 화면이 **상자의 나열**이 됐다.
   * 머리줄로 올리면 상자 둘이 사라지고, "무엇이 몇 건 · 여기서 더한다" 가 한 줄에 모인다.
   */
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        {/* 🚨 라벨 · 건수 · 행동이 한 줄이다. 건수를 라벨 아래로 내리지 않는다 —
            설명 줄과 같은 높이가 되면 "몇 건인지" 가 설명의 일부로 읽힌다.
            🚨 `items-center` 다. 버튼이 있는 줄에서 `items-baseline` 을 쓰면 버튼 상자가
            글자 기준선에 매달려 라벨보다 아래로 처진다.
            🚨 **줄 높이를 `min-h-touch` 로 깔지 않는다.** 버튼이 이미 자기 높이(44)를 갖고
            있어서 있는 줄은 어차피 44 인데, 없는 줄("기본 정보")까지 44 가 되면 13px 라벨
            위아래로 빈 공간이 15px 씩 생겨 라벨과 설명이 남남처럼 떨어진다. */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <h2 className="text-label text-brand">{title}</h2>
            {count !== undefined ? <CountChip>{count}건</CountChip> : null}
          </div>
          {action}
        </div>
        {description ? <p className="text-body-sm text-ink-muted mt-1">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

/**
 * 구역에 **더하는** 행동. 아이콘 하나다.
 *
 * 🚨 **글자 버튼이 아니다.** 13px 라벨 옆에 테두리 있는 글자 버튼이 서면 그 줄에서 제일 무거운
 *    것이 버튼이 되고, 구역이 셋이라 같은 상자가 화면에 여럿 생긴다 (목록 아래 전체 폭 버튼을
 *    걷어낸 것과 같은 이유 · 디자인 시스템 §7 버튼).
 * 🚨 **`label` 을 빼지 않는다.** 글자가 없으니 스크린리더에 남는 것이 그것뿐이고, 툴팁도
 *    거기서 나온다 (`IconButton`). 무엇에 더하는지까지 담는다 — 화면에 같은 아이콘이 둘이라
 *    "추가" 만으로는 어느 구역인지 알 수 없다.
 * 🚨 아이콘이 단독 신호가 되지 않는다 — 바로 왼쪽에 구역 이름이 글자로 서 있다 (§3 · §10).
 */
export function SectionAction({
  label,
  icon: Icon = Plus,
  onClick,
}: {
  label: string;
  /** 기본은 더하기다. 고치는 구역만 연필을 넘긴다 — 뜻이 다르면 모양도 달라야 한다. */
  icon?: LucideIcon;
  onClick: () => void;
}) {
  return (
    <IconButton label={label} onClick={onClick} className="-my-1">
      <Icon aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
    </IconButton>
  );
}

/** 🚨 실패를 빨강으로 칠하지 않는다 (디자인 시스템 §3). `CardFailed` 는 뉴트럴이다. */
export function SectionError({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <CardFailed>
      <p className="text-body text-ink">{what}</p>
      <Button variant="secondary" size="compact" onClick={onRetry} className="mt-3">
        다시 불러오기
      </Button>
    </CardFailed>
  );
}
