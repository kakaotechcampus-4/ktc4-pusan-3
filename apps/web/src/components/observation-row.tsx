import { CORRECTED_OBSERVATION } from "@/components/correction-buttons";
import { ObservationIcon, observationLabel } from "@/components/domain-chip";
import { CorrectedMark } from "@/components/observation-list";
import { CONFIDENCE_LABEL } from "@/lib/confidence";
import { cn } from "@/lib/cn";
import { isHealthObservation, type Observation } from "@/lib/api/types";
import { formatTimeOfDay } from "@/lib/format";
import { healthMoment, healthTitle, temperatureLabel } from "@/lib/health-observation";

/**
 * 저장된 기록 한 줄. 🚨 **지난 대화에서는 고친 기록도 온다** (#228 리뷰) — 교정은 기록을 지우지 않고 상태만
 * 바꿔서, 서버가 지금 모습(`status`)으로 싣는다. 방금 저장한 run 에서는 늘 `active` 라 예전엔 볼 필요가 없었다.
 * 07 기록 탭과 같은 고친 표시를 달고, 잘못된 기록(`inactive`)은 글자를 물린다 — 근거에서도 빠진 기록이다.
 * 색만으로 가르지 않는다 — 무엇으로 고쳤는지가 글자로 선다.
 */
export function ObservationRow({ observation }: { observation: Observation }) {
  const wrong = observation.status === "inactive";
  const detail = observationDetail(observation);

  return (
    <div className="py-3 first:pt-0 last:pb-0">
      {/* 🚨 여기서는 **도메인 색을 쓰지 않는다** (`DomainChip` 이 아니다). 한 화면에 도메인 색은
          2개까지인데(문서 §3), 한 줄이 관찰 3건으로 갈리면 4색이 다 뜰 수 있다. 그리고 도메인 색은
          "어느 Agent 의 결과인가" 신호라(05 제안) 저장된 관찰에 쓰면 그 뜻이 흐려진다.
          모양은 아이콘이, 뜻은 라벨이 진다. */}
      <div className="text-label text-ink-muted flex flex-wrap items-center gap-1.5">
        <ObservationIcon kind={observation.kind} className="text-ink-subtle" />
        {observationLabel(observation.kind)}
        {observation.observed_label ? (
          <span className="text-caption text-ink-subtle">· {observation.observed_label}</span>
        ) : null}
      </div>

      <p className={cn("text-body mt-2", wrong ? "text-ink-muted" : "text-ink")}>
        {observation.raw_text}
      </p>

      <div className="text-caption text-ink-subtle mt-1.5 flex flex-wrap items-center gap-x-1.5">
        {detail ? <span>{detail}</span> : null}
        {detail ? <span aria-hidden>·</span> : null}
        <span>{CONFIDENCE_LABEL[observation.confidence_source]}</span>
      </div>

      {observation.status !== "active" ? (
        <div className="mt-1.5">
          <CorrectedMark label={CORRECTED_OBSERVATION[observation.status].label} />
        </div>
      ) : null}
    </div>
  );
}

/**
 * 기록 한 건의 짧은 꼬리표 — 건강은 증상(먹인 약이면 약 이름) · 체온 · 시각, 나머지는 대상(`subject`). 🚨 health 는 `subject` 키가 없어서
 * `isHealthObservation()` 으로 가른다 (apps/web/CLAUDE.md §4).
 */
export function observationDetail(observation: Observation): string {
  if (!isHealthObservation(observation)) return observation.subject;
  // 먹인 약 행은 증상이 비어 온다 — 약 이름이 제목을 받는다 (#551). 체온은 숫자만 옮기고,
  // 시각은 본 시각이 없으면 잰 시각으로 받친다(`healthMoment`)
  const moment = healthMoment(observation.domain_fields);
  return [
    healthTitle(observation.domain_fields),
    temperatureLabel(observation.domain_fields),
    moment && formatTimeOfDay(moment),
  ]
    .filter(Boolean)
    .join(", ");
}
