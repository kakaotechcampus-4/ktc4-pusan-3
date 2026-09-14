"use client";

import { ChevronDown, ChevronUp } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { DomainChip, domainInk, domainField, domainPress } from "@/components/domain-chip";
import { Button } from "@/components/ui/button";
import { CountChip, EvidenceChip, EvidenceRow } from "@/components/ui/chip";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { Spinner } from "@/components/ui/spinner";
import type { Evidence, Suggestion } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 05 제안 후보 목록 — **훑고 나서 펼치기.**
 *
 * 이 화면은 **고르는 화면**이지 읽는 화면이 아니다. 후보를 전부 펼쳐 놓고 부모가 비교하게 하는
 * 카드 더미 배치를 거부한다 — 한 줄씩 훑고, 마음이 가는 하나만 그 자리에서 연다. 밤에 한 손으로
 * 여는 화면에서 "둘 다 읽고 고르기" 는 그 자체가 인지 노동이다 (CLAUDE.md §1).
 *
 * 🚨 **접힌 줄을 끌고 가는 것은 제안 문장이다.** 도메인 칩도 건수도 아니다 — 훑는 사람이 읽는 건
 *    문장이고, 그게 `body-sm` 회색으로 앉으면 목록에서 제일 약한 것이 목록의 내용이 된다.
 *
 * 🚨 **접힌 줄에도 기록 건수가 붙는다.** 근거를 달고 나간다는 것이 이 제품의 유일한 차별점이라
 *    (PRODUCT.md), 접힌 동안 그 사실이 사라지면 구조가 약속을 깬 것이다.
 *
 * 🚨 **열린 줄의 머리는 그 제안의 도메인 색이다** (문서 §2-3 · §3). 브랜드 초록이 아니다 —
 *    초록으로 통일하면 네 Agent 의 제안이 전부 같은 면이 되어, 이미 뜻을 정해 둔 도메인 4색이
 *    칩 하나로만 쓰인다. 색의 뜻은 그대로 "어느 Agent 에서 왔는가" 이고, 칠하는 면만 커진다.
 *    한 번에 한 줄만 열리므로 화면에 색 면은 항상 하나다.
 *
 * 🚨 **열린 줄은 두 칸이다** (문서 §7 "왜 두 칸인가"). 제안·이유는 도메인 면 위에, 근거 칩과
 *    고르는 버튼은 그 아래 `canvas` 칸에 둔다. 한 바탕에 넷을 쌓으면 제목·이유·근거·버튼이
 *    같은 무게로 읽혀서 이 제품의 차별점인 근거가 본문에 묻힌다 — 색을 바꿔도 마찬가지다.
 *    나누는 수단은 선이 아니라 바탕색이고, 아래 칸은 줄 가장자리까지 꽉 찬다.
 *
 * 🚨 **고르는 버튼은 도메인 색을 따라가지 않는다.** 실행은 브랜드(`primary`)다 — 도메인 색은
 *    "어디서 왔나", 브랜드는 "무엇을 하는가" 라서 둘을 섞으면 두 신호가 같은 색이 된다.
 *
 * 🚨 **근거가 없는 줄을 그릴 수 있는 경로를 만들지 않는다.** `evidence` 가 빈 suggestion 은 서버가
 *    버리고 `scarcity` 로 내린다 — 그 상태를 화면에 그리면 버그를 UI 로 덮는 것이다.
 *
 * 🚨 **일반 추천(`card-general`)을 이 컴포넌트에 플래그로 넣지 않는다** (CLAUDE.md §2).
 *    개인화와 일반이 한 컴포넌트가 되는 순간 근거 0건이 개인화로 그려질 길이 생긴다.
 */
export function SuggestionList({
  suggestions,
  pendingId,
  onApprove,
  onReject,
}: {
  suggestions: Suggestion[];
  /** 지금 초안을 만드는 중인 제안. 🚨 목록 전체를 잠그지 않는다 — 누른 줄만 기다린다. */
  pendingId: string | null;
  onApprove: (suggestion: Suggestion) => void;
  onReject: (suggestion: Suggestion) => void;
}) {
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

  /**
   * 🚨 여는 줄을 `useState` **초기값**으로 잡지 않는다. 이 컴포넌트는 쿼리가 돌아오기 전에도
   *    빈 배열로 한 번 렌더되는데, 그때 초기값이 `null` 로 굳어 **첫 줄이 영영 안 열렸다.**
   *    상태는 "부모가 직접 고른 것" 만 들고, 실제로 열 줄은 렌더마다 파생한다.
   *
   *    `undefined` = 아직 아무것도 안 눌렀다(첫 줄을 연다) · `null` = 부모가 닫았다(전부 접힘) ·
   *    문자열 = 그 줄. 열어 둔 줄이 "안 할래요" 로 사라지면 남은 첫 줄로 돌아간다.
   */
  const [chosenId, setChosenId] = useState<string | null | undefined>(undefined);
  const fallbackId = drawable[0]?.id ?? null;
  const openId =
    chosenId === undefined || (chosenId !== null && !drawable.some((s) => s.id === chosenId))
      ? fallbackId
      : chosenId;

  if (drawable.length === 0) return null;

  return (
    <ul className="border-line divide-line rounded-card bg-surface divide-y overflow-hidden border">
      {drawable.map((suggestion) => (
        <li key={suggestion.id}>
          <SuggestionRow
            suggestion={suggestion}
            open={openId === suggestion.id}
            onToggle={() => setChosenId(openId === suggestion.id ? null : suggestion.id)}
            pending={pendingId === suggestion.id}
            busy={pendingId !== null}
            onApprove={() => onApprove(suggestion)}
            onReject={() => onReject(suggestion)}
          />
        </li>
      ))}
    </ul>
  );
}

function SuggestionRow({
  suggestion,
  open,
  onToggle,
  pending,
  busy,
  onApprove,
  onReject,
}: {
  suggestion: Suggestion;
  open: boolean;
  onToggle: () => void;
  pending: boolean;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const id = useId();
  const panelId = `${id}-panel`;
  const titleId = `${id}-title`;

  /**
   * 🚨 Health Agent 는 진단하지 않는다 (CLAUDE.md §2). 그래서 이 줄에는 고르는 버튼이 없다 —
   *    식단이나 놀이를 정하지 않고 모아 둔 기록을 보여주기만 한다. 색은 다른 도메인과 같은
   *    규칙으로 자기 색(플럼)을 쓴다: 면의 뜻은 "고를 수 있음" 이 아니라 "어느 Agent" 다.
   */
  const isHealth = suggestion.agent === "health";
  /** 도메인 면을 입는가. 입으면 그 위 글자는 전부 그 도메인의 잉크다 (문서 §2-3). */
  const onField = open;
  const ink = domainInk(suggestion.agent);

  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className={cn(
          "min-h-touch ease-standard flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors duration-120",
          // 🚨 포커스 링을 안쪽에 그린다. 목록이 `overflow-hidden` 이라 바깥으로 그리면
          //    첫 줄과 마지막 줄의 링이 모서리에서 잘린다 (키보드 사용자에게만 보이는 사고).
          "focus-visible:-outline-offset-2",
          onField && domainField(suggestion.agent),
          // 🚨 누르면 **그 줄이 열릴 색**을 미리 보여준다. 뉴트럴 틴트로 누르면 눌린 색과 열린 색이
          //    달라서 두 동작이 남남처럼 보인다. 열린 줄에는 얹지 않는다 — 이미 그 바탕이라 싸운다.
          !open && domainPress(suggestion.agent),
        )}
      >
        {/* 메타 줄 — 어느 Agent 인지와, 접혀 있는 동안 근거가 있다는 사실.
            🚨 건수를 열린 줄에는 달지 않는다. 아래 칸이 근거를 실물로 보여주므로 두 번 말하게 된다 —
               건수는 **접혀 있는 동안** 그 사실을 지키려고 있는 것이다. */}
        <span className="flex w-full flex-wrap items-center gap-x-2 gap-y-1">
          <DomainChip agent={suggestion.agent} onField={onField} />
          {isHealth ? (
            <span className={cn("text-caption", onField ? ink : "text-ink-subtle")}>
              참고용이에요. 진단이 아니에요
            </span>
          ) : null}
          {open ? null : (
            <span className="text-caption text-ink-subtle">
              사용한 기록 {suggestion.evidence.length}건
            </span>
          )}
        </span>

        {/* 목록을 끌고 가는 것은 이 문장이다. 열리면 카드 제목 크기로 올라선다.
            🚨 쉐브론이 이 줄에 붙는다. 메타 줄에 두면 **여는 표시가 여는 대상에서 한 줄 떨어지고**,
               접힌 줄이 세 단이 되어 훑는 목록이 불필요하게 길어진다. */}
        <span className="flex w-full items-start gap-2">
          <span
            id={titleId}
            className={cn(
              "min-w-0 flex-1",
              open ? "text-title" : "text-body",
              onField ? ink : "text-ink",
            )}
          >
            {suggestion.content}
          </span>
          {open ? (
            <ChevronUp
              aria-hidden
              size={ICON_SIZE.md}
              strokeWidth={ICON_STROKE}
              className={cn("mt-1 shrink-0", onField ? ink : "text-ink-subtle")}
            />
          ) : (
            <ChevronDown
              aria-hidden
              size={ICON_SIZE.md}
              strokeWidth={ICON_STROKE}
              className="text-ink-subtle mt-0.5 shrink-0"
            />
          )}
        </span>
      </button>

      {/* 🚨 접혔다고 DOM 에서 빼지 않는다. `aria-controls` 가 가리키는 id 가 사라져서
          보조기술에는 아무 데도 안 가리키는 버튼이 된다. 숨기는 것은 `hidden` 이 한다. */}
      <div id={panelId} role="region" aria-labelledby={titleId} hidden={!open}>
        <div className={cn("px-4 pb-4", onField && domainField(suggestion.agent))}>
          <Reasons
            ink={onField ? ink : null}
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
        </div>

        {/* 아래 칸 — 쓴 기록과 어떻게 할까. 줄 가장자리까지 꽉 차서 "카드 속 카드" 가 아니라
            바닥으로 읽힌다.
            🚨 바탕이 `surface` 가 아니라 `canvas` 다. 접힌 줄이 `surface` 라서, 아래 칸까지
               `surface` 로 두면 **행동 버튼이 아래 후보와 한 덩어리로 묶여 보였다** — 열린 줄이
               초록이 끝나는 데서 끝나는 것처럼 읽혔다. 열린 줄은 초록 머리 + 웜 바닥 한 덩어리고,
               접힌 줄은 흰 줄이다. 그래야 후보 사이 경계가 줄 안의 경계보다 세다. */}
        <div className="bg-canvas px-4 py-4">
          <EvidenceList evidence={suggestion.evidence} />

          {isHealth ? (
            <p className="text-body-sm text-ink-muted mt-3">
              기록을 모아 보여드릴 뿐이에요. 진단하지 않고, 식단이나 놀이를 정하지 않아요. 걱정되면
              의료진에게 물어보세요.
            </p>
          ) : (
            <div className="mt-4 flex items-center gap-2">
              {/* 🚨 목록 항목의 primary 는 "한 화면에 하나" 의 예외다 (문서 §7). 열린 줄이 하나뿐이라
                  화면에 초록 버튼도 하나다. 여기서 확정하지 않는다 — 승인 게이트는 이 버튼이 여는 시트다. */}
              <Button size="compact" onClick={onApprove} disabled={busy}>
                {pending ? <Spinner /> : null}
                {pending ? "여는 중…" : "이걸로"}
              </Button>
              <Button variant="tertiary" size="compact" onClick={onReject} disabled={busy}>
                안 할래요
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * 이유 두 줄. 라벨을 한 열로 맞춰 세운다 — 값이 길어 줄이 바뀌어도 라벨이 같은 세로선에 남아서
 * 두 줄이 한 덩어리로 읽힌다.
 *
 * 🚨 도메인 면 위의 글자는 전부 그 도메인 잉크다 (문서 §2-3). 회색 잉크를 얹으면 대비를
 *    `canvas` 기준으로 잘못 계산하게 되고, 색 바탕에 회색 글씨가 얹힌 것처럼 보인다.
 */
function Reasons({
  items,
  ink,
}: {
  items: Array<{ term: string; value: string }>;
  /** 도메인 면 위면 그 잉크, 아니면 뉴트럴. */
  ink: string | null;
}) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
      {items.map((item) => (
        <ReasonRow key={item.term} term={item.term} ink={ink}>
          {item.value}
        </ReasonRow>
      ))}
    </dl>
  );
}

function ReasonRow({
  term,
  ink,
  children,
}: {
  term: string;
  ink: string | null;
  children: ReactNode;
}) {
  return (
    <>
      <dt className={cn("text-caption", ink ?? "text-ink-subtle")}>{term}</dt>
      <dd className={cn("text-body-sm", ink ?? "text-ink-muted")}>{children}</dd>
    </>
  );
}

/**
 * 근거 칩 줄. 🚨 **4개를 넘으면 "+N" 으로 접는다** (문서 §7) — 근거를 숨기는 게 아니라
 * 카드에서 목록으로 옮기는 것이다. 목록(07 기억 화면)은 다음 이슈라, 지금은 건수만 남긴다.
 */
const EVIDENCE_VISIBLE = 4;

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  const visible = evidence.slice(0, EVIDENCE_VISIBLE);
  const hidden = evidence.length - visible.length;

  return (
    <div>
      {/* 🚨 질문형으로 쓰지 않는다. health 줄의 이유 라벨이 이미 "무엇을 봤나" 라서
          같은 질문을 한 줄 안에서 두 번 하게 된다. 접힌 줄의 건수와도 같은 말을 쓴다. */}
      <p className="text-caption text-ink-subtle">사용한 기록</p>
      <EvidenceRow>
        {visible.map((item) => (
          <EvidenceChip
            key={`${item.ref.kind}:${item.ref.id}`}
            label={item.label}
            stale={item.is_stale}
          />
        ))}
        {hidden > 0 ? <CountChip>외 {hidden}건</CountChip> : null}
      </EvidenceRow>
    </div>
  );
}
