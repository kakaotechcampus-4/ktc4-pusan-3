import { CORRECTED_OBSERVATION } from "@/components/correction-buttons";
import { ObservationIcon, observationLabel } from "@/components/domain-chip";
import { CorrectedMark } from "@/components/observation-list";
import { CONFIDENCE_LABEL } from "@/lib/confidence";
import { isHealthObservation, type Observation } from "@/lib/api/types";
import { formatTimeOfDay } from "@/lib/format";
import { healthMoment, healthTitle, temperatureLabel } from "@/lib/health-observation";

/**
 * 저장된 기록 한 줄. 🚨 **지난 대화에서는 고친 기록도 온다** (#228 리뷰) — 교정은 기록을 지우지 않고 상태만
 * 바꿔서, 서버가 지금 모습(`status`)으로 싣는다. 방금 저장한 run 에서는 늘 `active` 라 예전엔 볼 필요가 없었다.
 * 07 기록 탭과 같은 고친 표시를 단다. 색만으로 가르지 않는다 — 무엇으로 고쳤는지가 글자로 선다.
 * ⚠️ 잘못된 기록(`inactive`)은 없다 (#736) — 서버가 지운 기록으로 옮겨서 지난 대화에도 실리지 않는다.
 */
export function ObservationRow({ observation }: { observation: Observation }) {
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
          // 🚨 기록 줄에는 가운뎃점을 쓰지 않는다 (디자인 시스템 §7 기록 줄 · #815) — 간격 8 로 가른다 (gap 6 + 2).
          <span className="text-caption text-ink-subtle ml-0.5">{observation.observed_label}</span>
        ) : null}
      </div>

      <p className="text-body text-ink mt-2">{observation.raw_text}</p>

      <div className="text-caption text-ink-subtle mt-1.5 flex flex-wrap items-center gap-x-2">
        {detail ? <span>{detail}</span> : null}
        {/* 🚨 **보호자가 직접 한 말이면 출처를 적지 않는다** (#868). 대화의 기록은 거의 다 그 보호자가 적은 것이라
            줄마다 "보호자 직접" 이 붙으면 아무것도 알려 주지 않는다. 다를 때(전해 들음 · 추측 · 기관 공지)만 적는다.
            07 상세 시트의 "출처" 칸은 칸 이름이 있어서 그대로 적는다. */}
        {observation.confidence_source === "parent_direct" ? null : (
          <span>{CONFIDENCE_LABEL[observation.confidence_source]}</span>
        )}
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
