import { DomainChip } from "@/components/domain-chip";
import { Button } from "@/components/ui/button";
import { CountChip, EvidenceChip, EvidenceRow } from "@/components/ui/chip";
import type { Evidence, Suggestion } from "@/lib/api/types";

/**
 * 디자인 시스템 §7 `card-personalized` — 05 제안 후보의 개인화 추천 1건.
 *
 * 🚨 **일반 추천(`card-general`)과 같은 컴포넌트로 만들지 않는다** (문서 §7 · CLAUDE.md §2).
 *    한 컴포넌트에 `isGeneral` 같은 플래그를 넣으면 언젠가 근거 0건인 추천이 개인화로 그려진다.
 *    두 개가 한 필드에 섞이면 "개인화 추천의 근거 0건은 버그" 라는 하드 기준이 무의미해진다.
 *
 * 🚨 **근거가 없는 카드를 그릴 수 있는 경로를 만들지 않는다.** `evidence` 가 빈 suggestion 은
 *    서버가 생성 단계에서 버리고 `scarcity` 로 내린다 — 그 상태를 화면에 그리면 버그를 UI 로 덮는 것이다.
 *    그래서 빈 배열이면 **아무것도 렌더하지 않고 개발 콘솔에 경고**한다 (조용히 넘기지 않는다).
 *
 * 🚨 health 는 이 카드가 아니다. 진단하지 않는다는 규칙 때문에 문구도 행동도 다르다
 *    (아래 `HealthSuggestionCard`).
 */
export function SuggestionCard({
  suggestion,
  onApprove,
  onReject,
  approveLabel = "이걸로",
  busy = false,
}: {
  suggestion: Suggestion;
  /** 승인 시트를 연다. 🚨 여기서 확정하지 않는다 — 시트가 승인 게이트다. */
  onApprove: () => void;
  onReject: () => void;
  approveLabel?: string;
  busy?: boolean;
}) {
  if (suggestion.evidence.length === 0) {
    if (process.env.NODE_ENV !== "production") {
      // 🚨 원문이 아니라 id 만 남긴다 (CLAUDE.md §2 개인정보).
      console.error(
        `[suggestion] evidence 0건인 개인화 추천이 내려왔다 (id: ${suggestion.id}). 서버가 scarcity 로 내렸어야 한다.`,
      );
    }
    return null;
  }

  return (
    <article className="bg-brand-soft rounded-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <DomainChip agent={suggestion.agent} />
      </div>

      <h3 className="text-section text-ink mt-3">{suggestion.content}</h3>

      <dl className="mt-3 flex flex-col gap-1">
        <Reason term="왜 이걸" value={suggestion.reason.why_this} />
        <Reason term="왜 지금" value={suggestion.reason.why_now} />
      </dl>

      <EvidenceList evidence={suggestion.evidence} className="mt-3" />

      {/* 🚨 카드 안에서도 primary 를 쓰지 않는다. 제안은 화면에 여러 장 뜨는데(Agent 최대 2개),
          카드마다 primary 를 두면 한 화면에 초록 버튼이 두 개가 되어 "다음 행동이 무엇인가" 가
          사라진다 (문서 §7 "한 화면에 primary 는 하나"). 카드 안의 서열은 secondary 와
          tertiary 로 낸다. 진짜 primary 는 승인 시트의 `btn-approve` 하나뿐이다. */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="secondary" onClick={onApprove} disabled={busy}>
          {approveLabel}
        </Button>
        <Button variant="tertiary" onClick={onReject} disabled={busy}>
          안 할래요
        </Button>
      </div>

      <p className="text-caption text-brand-ink mt-3">
        누르면 저장될 내용을 먼저 보여드려요. 승인 전에는 아무것도 넣지 않아요.
      </p>
    </article>
  );
}

/**
 * health 전용 카드.
 *
 * 🚨 **Health Agent 는 진단하지 않는다** (CLAUDE.md §2). 그래서 이 카드에는
 *    "이걸로" 가 없다 — 식단이나 놀이를 정하지 않고, 모아 놓은 기록을 보여주기만 한다.
 *    같은 이유로 이유 라벨도 "왜 이걸 / 왜 지금" 이 아니라 "무엇을 봤나 / 참고할 점" 이다.
 */
export function HealthSuggestionCard({ suggestion }: { suggestion: Suggestion }) {
  if (suggestion.evidence.length === 0) return null;

  return (
    <article className="border-line rounded-card bg-surface border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <DomainChip agent="health" />
        <span className="text-caption text-ink-subtle">참고용이에요. 진단이 아니에요</span>
      </div>

      <h3 className="text-section text-ink mt-3">{suggestion.content}</h3>

      <dl className="mt-3 flex flex-col gap-1">
        <Reason term="무엇을 봤나" value={suggestion.reason.why_this} />
        <Reason term="참고할 점" value={suggestion.reason.why_now} />
      </dl>

      <EvidenceList evidence={suggestion.evidence} className="mt-3" />

      <p className="text-body-sm text-ink-muted mt-4">
        기록을 모아 보여드릴 뿐이에요. 진단하지 않고, 식단이나 놀이를 정하지 않아요. 걱정되면
        의료진에게 물어보세요.
      </p>
    </article>
  );
}

/**
 * 이유 한 줄. 가운뎃점은 **줄당 하나**까지다 (문서 §4) — 라벨과 값을 가르는 이 자리에서 다 쓴다.
 * 그래서 값 안에서 또 나누고 싶어도 여기서는 더 쓰지 않는다.
 */
function Reason({ term, value }: { term: string; value: string }) {
  return (
    <div className="text-body-sm text-ink-muted flex gap-1.5">
      <dt className="text-ink-subtle shrink-0">{term} ·</dt>
      <dd>{value}</dd>
    </div>
  );
}

/**
 * 근거 칩 줄. 🚨 **4개를 넘으면 "+N" 으로 접는다** (문서 §7) — 근거를 숨기는 게 아니라
 * 카드에서 목록으로 옮기는 것이다. 목록(07 기억 화면)은 다음 이슈라, 지금은 건수만 남긴다.
 */
const EVIDENCE_VISIBLE = 4;

function EvidenceList({ evidence, className }: { evidence: Evidence[]; className?: string }) {
  const visible = evidence.slice(0, EVIDENCE_VISIBLE);
  const hidden = evidence.length - visible.length;

  return (
    <div className={className}>
      <p className="text-caption text-ink-subtle">사용한 기록 {evidence.length}건</p>
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
