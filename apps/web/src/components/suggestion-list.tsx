"use client";

import { Fragment, useState } from "react";

import { domainBar, domainField, domainLabel } from "@/components/domain-chip";
import { Checkbox } from "@/components/ui/checkbox";
import { DOMAIN_ICON } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { PanelTabs } from "@/components/ui/tabs";
import type { Agent, Evidence, Suggestion, SuggestionGroup } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 05 제안 후보 목록 — **한 결정에 대한 대안 셋.**
 *
 * 이 화면은 **고르는 화면**이지 읽는 화면이 아니다. 한 Agent 가 후보를 **3가지씩** 낸다
 * (저녁 제안으로 김치찌개 · 계란말이 · 멸치볶음). Agent 는 최대 2개라(NF-01) 화면에 여섯 줄이
 * 서는데, **그 셋은 서로 다른 제안이 아니라 한 결정에 대한 대안**이다. 한 덩어리로 늘어놓으면
 * 보호자는 여섯 개의 독립 제안으로 읽고 "지금 무엇을 정하는 중인가" 가 사라진다 —
 * 그래서 **Agent 묶음이 1차 구조**이고, 그 아래 줄이 대안이다.
 *
 * ## 🚨 **접지 않는다**
 *
 * 한동안 아코디언이었다 (한 줄씩 훑고 하나만 여는). 후보가 Agent 당 하나씩이던 때의 구조다.
 * 셋이 되면서 접는 것이 **틀린 답**이 됐다 — 대안을 견주려면 이유와 근거가 같이 보여야 하고,
 * 접힌 목록은 그 비교를 스크롤과 기억으로 미룬다. 하나만 열어 두는 것도 안 된다:
 * 열린 하나가 추천처럼 보이는데 셋은 동등하다.
 *
 * 🚨 **줄을 끌고 가는 것은 제안 문장이다.** `body-sm` 회색으로 앉히면 목록에서 제일 약한 것이
 *    목록의 내용이 된다.
 *
 * 🚨 **근거를 접지 않는다.** 근거를 달고 나간다는 것이 이 제품의 유일한 차별점이라
 *    (PRODUCT.md), 그것을 "+N" 뒤에 두면 구조가 약속을 깬다.
 *
 * ## 🚨 **줄에는 도메인 색이 없다**
 *
 * 예전 열린 줄은 머리가 `{domain}-soft` 였다. 전부 펼쳐지면서 그 면이 **여섯 개**가 되는데,
 * 디자인 시스템 §3 의 "한 화면에 도메인 색 2개" 는 칩을 세던 규칙이라 면으로 채우면 화면이
 * 색 덩어리가 된다. 도메인은 **묶음 머리줄의 칩 하나**가 진다.
 *
 * 🚨 **줄에 도메인 칩도 달지 않는다.** 묶음 안은 전부 같은 도메인이라 나르는 정보가 0이고,
 *    같은 칩이 셋씩 반복되면 훑는 눈이 매번 그것을 지나쳐야 한다.
 *
 * 🚨 **묶음 머리줄에 브랜드를 쓰지 않는다.** 디자인 시스템 §7 의 "구역 머리줄" 은 라벨을
 *    `label`/`brand` 로 정해 뒀지만, 이 화면의 구역은 **도메인**이다. 초록을 얹으면 "어디서
 *    왔나"(도메인)와 브랜드가 같은 줄에서 겹쳐 색 하나가 두 뜻을 나른다 (§2-2).
 *
 * ## 🚨 **제안 문장이 체크박스의 라벨이다**
 *
 * 제안을 **여러 개 고를 수 있다.** 고르는 수단은 체크박스 하나뿐이다 —
 *
 * 🚨 **줄마다 "이걸로" 버튼을 두지 않는다.** 두 번 틀린 자리다. ㉠ 여러 개를 고르는 화면에서
 *    그 버튼은 "이거 하나만" 이라는 뜻이 되어 체크박스와 싸운다. ㉡ 전부 펼쳐지면서 그 버튼이
 *    **여섯 개**가 되어 화면의 `primary` 가 하단 CTA 와 경쟁한다. 다음 행동은 목록 **아래 한 곳**이다.
 *
 * 🚨 **체크박스의 라벨은 제안 문장 전체다.** 고르는 대상이 곧 제안이라 그것이 타깃이 되는 것이
 *    맞고, 클릭 면적도 20px 사각형이 아니라 문장이 된다.
 *
 * 🚨 **근거가 없는 줄을 그릴 수 있는 경로를 만들지 않는다.** `evidence` 가 빈 suggestion 은 서버가
 *    버리고 `scarcity` 로 내린다 — 그 상태를 화면에 그리면 버그를 UI 로 덮는 것이다.
 *
 * 🚨 **일반 추천(`card-general`)을 이 컴포넌트에 플래그로 넣지 않는다** (CLAUDE.md §2).
 *    개인화와 일반이 한 컴포넌트가 되는 순간 근거 0건이 개인화로 그려질 길이 생긴다.
 */
export function SuggestionList({
  suggestions,
  groups,
  selectedIds,
  onToggleSelect,
  busy,
}: {
  suggestions: Suggestion[];
  /**
   * 묶음 머리말. ⚠️ 서버가 안 보내면 빈 배열이고, 그때 머리줄은 **도메인 라벨만** 쓴다 —
   *    "오늘 저녁 뭐 먹을까요" 같은 문구도, "한 끼로 묶여요" 도 화면이 지어내지 않는다.
   */
  groups: SuggestionGroup[];
  /** 지금 고른 제안. 🚨 **여러 개다** — 고른 것을 한 번에 일정으로 만든다. */
  selectedIds: string[];
  onToggleSelect: (suggestion: Suggestion) => void;
  /** 초안을 만드는 중. 고르기를 잠근다 — 보내는 값이 바뀌면 안 된다. */
  busy: boolean;
}) {
  /**
   * 지금 보고 있는 묶음. 🚨 **서버 상태가 아니라 화면 상태다** — 주소에 두지 않는다.
   *    탭이 주소를 바꾸면 흐름 중인 화면에서 뒤로가기가 탭을 되짚느라 화면을 못 벗어난다
   *    (`ui/tabs.tsx` 의 `PanelTabs` 머리말).
   */
  const [open, setOpen] = useState<Agent | null>(null);

  const drawable = suggestions.filter((s) => {
    if (s.evidence.length > 0) return true;
    if (process.env.NODE_ENV !== "production") {
      // 🚨 원문이 아니라 id 만 남긴다 (CLAUDE.md §2 개인정보).
      console.error(
        `[suggestion] evidence 0건인 개인화 추천이 내려왔다 (id: ${s.id}). 서버가 scarcity 로 내렸어야 한다.`,
      );
    }
    return false;
  });

  if (drawable.length === 0) return null;

  /**
   * 🚨 **순서는 서버가 준 순서다.** 화면이 도메인으로 정렬하지 않는다 — Supervisor 가 고른
   *    Agent 순서에 "무엇을 먼저 묻는가" 가 들어 있다.
   */
  const order: Agent[] = [];
  for (const s of drawable) if (!order.includes(s.agent)) order.push(s.agent);

  /**
   * 🚨 **지운 묶음으로 남아 있지 않게 한다.** "안 할래요" 로 한 묶음이 통째로 비면 그 탭이
   *    사라지는데, 고른 탭이 그것이면 화면에 아무것도 안 남는다.
   */
  const agent = open !== null && order.includes(open) ? open : order[0];
  const rows = drawable.filter((s) => s.agent === agent);
  const group = groups.find((g) => g.agent === agent);
  const chosen = rows.filter((s) => selectedIds.includes(s.id)).length;

  return (
    <div className="flex flex-col gap-4">
      {/* 🚨 **묶음이 하나면 탭을 세우지 않는다.** 고를 것이 없는 탭 줄은 자리만 먹고, 누를 수
          있어 보이는데 아무 일도 안 일어난다. */}
      {order.length > 1 ? (
        <PanelTabs
          label="어느 영역의 제안을 볼까요"
          panelId={PANEL_ID}
          active={agent}
          onChange={(key) => setOpen(key as Agent)}
          items={order.map((a) => ({
            key: a,
            /**
             * 🚨 **도메인 색을 탭에 얹지 않는다.** 두 탭에 `-soft` 면을 깔면 꺼진 탭이 켜진
             *    것처럼 보이고, 활성 신호(`brand` 밑줄 + 잉크)와 도메인 신호가 같은 줄에서
             *    싸운다. 남는 것은 **아이콘 모양**이고 색은 탭의 상태색을 따라간다.
             */
            icon: a,
            label: `${domainLabel(a)} ${drawable.filter((s) => s.agent === a).length}가지`,
          }))}
        />
      ) : null}

      <section
        id={PANEL_ID}
        // 🚨 탭이 없으면 `tabpanel` 도 아니다 — 가리킬 탭이 없는 패널은 보조기술에 거짓말이다.
        role={order.length > 1 ? "tabpanel" : undefined}
        aria-labelledby={order.length > 1 ? `${PANEL_ID}-tab-${agent}` : undefined}
        className="flex flex-col gap-2"
      >
        {/* 🚨 머리줄은 **무엇을 정하는 중인가**를 말한다. 문구는 서버가 주고(`prompt`) —
            질문을 화면이 지어내지 않는다.
            🚨 **질문이 후보보다 커야 한다** (`display` 24 > `title` 20). 이 셋이 **무엇에 대한
            답인지**가 답보다 작게 서면 위계가 뒤집힌다. 본문 서체가 단일 웨이트라 굵기로는
            못 만들고(§4), 위계를 지는 것은 크기뿐이다.
            🚨 **`h2` 다** — 화면 제목(`h1`) 바로 아래 층이다. `h3` 로 두면 화면에서 제일 큰
            글자가 건너뛴 층에 앉는다.
            🚨 **도메인 색은 아이콘 타일 하나까지다** (문서 §3 예외 ㉡ 와 같은 값·같은 이유).
            줄에서 색 면을 걷어낸 뒤로 이 화면에 도메인 색이 없었는데, 타일은 **한 화면에 한 개**라
            여섯 면이 서로 싸우던 문제가 돌아오지 않는다. 🚨 타일이 지는 것은 `aria-hidden` 아이콘
            하나라 **색이 단독 신호가 아니다** — 이름은 탭이(묶음이 하나면 아래 캡션이) 글자로 진다.
            🚨 **브랜드를 쓰지 않는다** — 이 구역을 가르는 것은 도메인이다 (§2-2). */}
        <div className="flex items-center gap-3">
          <IconTile icon={DOMAIN_ICON[agent]} tone="plain" className={domainField(agent)} />
          <div className="min-w-0">
            {group?.prompt ? <h2 className="text-display text-ink">{group.prompt}</h2> : null}
            {/* 🚨 탭이 있으면 이름·건수를 여기서 다시 말하지 않는다 — 탭이 "놀이 3가지" 로
                이미 말해서 60px 안에 같은 말이 두 번 선다. 탭이 없을 때(묶음 하나)만 선다. */}
            {order.length > 1 ? null : (
              <p className="text-caption text-ink-subtle mt-0.5">
                {domainLabel(agent)} · {rows.length}가지 중에서
              </p>
            )}
          </div>
        </div>

        {/* 🚨 **후보는 각자 카드다.** 한 컨테이너 안에 선으로만 나눠 뒀더니 셋이 한 덩어리로
            읽혀서, 고르는 대상이 **줄**인지 **묶음 전체**인지가 흐려졌다.
            ⚠️ 원래 한 컨테이너였던 이유는 "카드 더미로 펼치면 흰 상자 여섯이 줄줄이 서서 무엇이
            한 묶음인지 안 보인다" 였는데, **탭이 그 일을 가져갔다** — 한 번에 한 묶음만 서고
            묶음의 이름과 질문은 탭과 머리줄이 말한다. 그래서 상자는 최대 셋이다. */}
        <ul className="flex flex-col gap-3">
          {rows.map((suggestion) => (
            <li
              key={suggestion.id}
              /**
               * 🚨 **고른 카드는 테두리와 띠가 **함께** `brand` 가 된다.** 색의 뜻은
               *    `chip-choice`(고르는 칩)와 같은 "골랐음" 이다.
               * 🚨 **띠만 도메인 색으로 남기지 않는다.** 그렇게 두면 왼쪽에서 초록 테두리와
               *    도메인 띠가 맞닿아 **띠가 두 줄로 보이고**, 둥근 모서리에서 띠가 잘려
               *    턱이 생긴다 (실제로 그렇게 보였다). 프레임은 한 가지 색으로 한 가지를 말한다.
               * 🚨 **도메인이 사라지는 것이 아니다** — 어느 영역인지는 탭과 머리줄이 글자로
               *    지고 있고, 같은 묶음 안 카드는 전부 같은 도메인이다. 고른 카드의 프레임이
               *    말하는 것은 **지금 고른 것**이다.
               * 🚨 **색만으로 말하지 않는다** — 체크 아이콘이 함께 선다 (문서 §3).
               */
              className={cn(
                "rounded-card bg-surface flex overflow-hidden border",
                selectedIds.includes(suggestion.id) ? "border-brand" : "border-line",
              )}
            >
              {/* 🚨 **도메인 색은 왼쪽 띠 하나다.** 한동안 카드 머리를 `-soft` 면으로 칠했는데,
                  면이 커지면 그 위의 글자가 전부 그 도메인의 `-ink` 가 되어야 해서(§2-3)
                  제목·이유·근거의 회색 위계를 먹었다. 띠는 **글자를 하나도 건드리지 않으면서**
                  같은 말을 한다.
                  🚨 `aria-hidden` 이다 — 색은 단독 신호가 될 수 없고(§3), 어느 영역인지는
                  탭과 머리줄이 글자로 진다. */}
              <span
                aria-hidden
                className={cn(
                  "w-1.5 shrink-0",
                  selectedIds.includes(suggestion.id) ? "bg-brand" : domainBar(suggestion.agent),
                )}
              />
              <div className="min-w-0 flex-1">
                <SuggestionRow
                  suggestion={suggestion}
                  selected={selectedIds.includes(suggestion.id)}
                  onToggleSelect={() => onToggleSelect(suggestion)}
                  busy={busy}
                />
              </div>
            </li>
          ))}
        </ul>

        {/* 🚨 **묶인다는 것을 고르기 전에 말한다.** 셋을 고르고 일정 1건이 나오면 놀란다.
            🚨 서버가 `merges_into_one` 을 안 보내면 **아무 말도 하지 않는다** — 어느 Agent 가
               묶이는지는 도메인 지식이라 화면이 알 수 없다. */}
        {group?.merges_into_one && chosen > 1 ? (
          <p role="status" className="text-body-sm text-ink-muted">
            고른 {chosen}가지는 일정 하나로 묶여요.
          </p>
        ) : null}
      </section>
    </div>
  );
}

/** 탭과 패널을 잇는 id. 한 화면에 이 목록은 하나뿐이라 고정값으로 둔다. */
const PANEL_ID = "suggestion-group";

function SuggestionRow({
  suggestion,
  selected,
  onToggleSelect,
  busy,
}: {
  suggestion: Suggestion;
  selected: boolean;
  onToggleSelect: () => void;
  busy: boolean;
}) {
  /**
   * 🚨 Health Agent 는 진단하지 않는다 (CLAUDE.md §2). 그래서 이 줄에는 고르는 칸이 없다 —
   *    식단이나 놀이를 정하지 않고 모아 둔 기록을 보여주기만 한다.
   */
  const isHealth = suggestion.agent === "health";

  return (
    <div className="px-4 py-4">
      {isHealth ? (
        <p className="text-caption text-ink-subtle mb-1">참고용이에요. 진단이 아니에요</p>
      ) : null}

      {/* 🚨 **제안 문장이 고르는 칸이다.** 줄마다 "이걸로 고르기" 를 따로 두면 체크박스와 같은
          일을 하는 버튼이 여섯 개가 되고, 화면이 초록으로 덮여 §7 "한 화면에 primary 하나" 가
          깨진다. 고르는 대상이 곧 제안이므로 문장을 라벨로 준다 — 클릭 면적도 체크박스 20px 이
          아니라 문장 전체가 된다.
          🚨 **거절 버튼을 두지 않는다.** 고르는 칸이 체크박스라 **안 고르는 것이 곧 거절**이다
          (있던 "안 할래요" 는 서버로 아무것도 보내지 않고 화면에서만 접었다). 제안에 대한
          평가는 07 피드백이 **해 보고 나서** 받는다. */}
      {isHealth ? (
        <p className="text-title text-ink">{suggestion.content}</p>
      ) : (
        <Checkbox
          checked={selected}
          onChange={() => !busy && onToggleSelect()}
          label={<span className="text-title text-ink">{suggestion.content}</span>}
          className="py-0"
        />
      )}

      {/* 🚨 체크박스 칸(20)과 사이(12)만큼 들여써서 이유·근거가 제안 문장과 한 세로선에 선다.
          health 줄은 체크박스가 없으니 들여쓰지 않는다. */}
      <div className={isHealth ? "" : "pl-8"}>
        <Reasons
          className="mt-4"
          items={
            isHealth
              ? [
                  { term: "무엇을 봤나", value: suggestion.reason.why_this },
                  { term: "참고할 점", value: suggestion.reason.why_now },
                ]
              : [
                  { term: "왜 이걸", value: suggestion.reason.why_this },
                  { term: "왜 지금", value: suggestion.reason.why_now },
                ]
          }
        />

        {/* 🚨 **근거는 선 아래에 둔다.** 제안·이유와 같은 무게로 쌓으면 이 제품의 차별점인
            근거가 본문에 묻힌다. 나누는 것은 카드 속 카드가 아니라 **가는 선 하나**다. */}
        <div className="border-line mt-3 border-t pt-3">
          <EvidenceList evidence={suggestion.evidence} />
        </div>

        {isHealth ? (
          <p className="text-body-sm text-ink-muted mt-3">
            기록을 모아 보여드릴 뿐이에요. 진단하지 않고, 식단이나 놀이를 정하지 않아요. 걱정되면
            의료진에게 물어보세요.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/**
 * 이유 두 줄. 라벨을 한 열로 맞춰 세운다 — 값이 길어 줄이 바뀌어도 라벨이 같은 세로선에 남아서
 * 두 줄이 한 덩어리로 읽힌다.
 *
 * 🚨 **도메인 잉크를 쓰지 않는다.** 줄이 색 면을 입지 않게 되면서(위 머리말) 바탕이 `surface` 라,
 *    도메인 잉크를 얹으면 대비가 그 면 기준으로 계산되지 않는다.
 */
function Reasons({
  items,
  className,
}: {
  items: Array<{ term: string; value: string }>;
  className?: string;
}) {
  return (
    <dl className={cn("grid grid-cols-[auto_1fr] gap-x-3 gap-y-1", className)}>
      {items.map((item) => (
        <Fragment key={item.term}>
          <dt className="text-caption text-ink-subtle">{item.term}</dt>
          <dd className="text-body-sm text-ink-muted">{item.value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

/**
 * 사용한 기록. 🚨 **칩이 아니라 목록이다.** 칩은 이름만 나르는데, 부모가 실제로 확인하는 것은
 * "왜 그게 근거가 되는가" 다 — 반복 몇 회인지, 언제 것인지, 누가 말한 것인지.
 *
 * 🚨 **설명 줄은 서버 문구(`detail`)를 그대로 쓴다.** 근거 종류마다 뜻이 다르고(성향이면 반복
 *    횟수, 급식 행이면 기관 기록, 알레르기면 규칙 확인), 화면이 종류별로 문장을 조립하려면
 *    근거의 의미를 프론트가 알아야 한다 (CLAUDE.md §3). 서버가 안 보내면 **가진 것으로만**
 *    만든다 — 날짜와 출처 라벨. 없는 것을 지어내지 않는다.
 *
 * 🚨 **접지 않는다.** 예전에는 4개를 넘으면 "+N" 으로 접었는데, 그건 칩 한 줄에 다 안 들어가서였다.
 *    목록이 되면 접을 이유가 없고, 근거를 접는 것은 이 제품이 제일 하면 안 되는 일이다.
 */
function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  return (
    <div>
      {/* 🚨 질문형으로 쓰지 않는다. health 줄의 이유 라벨이 이미 "무엇을 봤나" 라서
          같은 질문을 한 블록 안에서 두 번 하게 된다. */}
      <p className="text-label text-ink-muted">사용한 기록 {evidence.length}건</p>

      <ul className="mt-2 flex flex-col gap-2.5">
        {evidence.map((item) => (
          <li key={`${item.ref.kind}:${item.ref.id}`}>
            <p className="text-body-sm text-ink">{item.label}</p>
            <p className="text-caption text-ink-subtle mt-0.5">
              {item.note}
              {/* 🚨 6개월 지난 근거는 단독으로 쓰지 않는다 (NF-08) — 그 사실을 글자로 말한다.
                  색·점선 하나로는 단독 신호가 되고, 이 목록엔 칩이 없어서 얹을 자리도 없다. */}
              {item.is_stale ? " · 6개월이 지난 기록이에요" : ""}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
