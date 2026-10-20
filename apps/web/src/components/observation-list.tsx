"use client";

import { josa } from "es-hangul";
import { ChevronRight, PencilLine } from "lucide-react";

import { CORRECTED_OBSERVATION } from "@/components/correction-buttons";
import {
  domainField,
  OBSERVATION_ICON,
  observationAgent,
  observationLabel,
} from "@/components/domain-chip";
import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconTile } from "@/components/ui/icon-tile";
import { isHealthObservation, type Observation } from "@/lib/api/types";
import { cn } from "@/lib/cn";

/**
 * 07 관찰 목록 — **줄**이다. 카드가 아니다.
 *
 * 🚨 이 화면의 핵심 구분이 여기 있다. 관찰은 **가벼운 줄**이고, 프로필은 무게가 있는
 *    **카드**다 (`AffinityList`). 프로토타입처럼 둘을 같은 모양으로 그리고 라벨로만 가르면
 *    "한 번 본 것" 과 "확정된 성향" 이 같은 무게로 읽히는데, 그게 이 제품의 1번 규칙(한 번의
 *    관찰을 성향으로 확정하지 않는다 · CLAUDE.md §2)이 화면에서 사라지는 지점이다.
 *
 * ⚠️ **줄은 한 덩어리가 아니라 하나씩 떨어져 선다** (#269 · 제품 결정). 처음에는 가는 선으로만
 *    나뉜 한 덩어리였는데, 기록마다 상태(`active` · `stand_alone` · `inactive`)가 갈리면서 줄마다
 *    면을 달리 줘야 했고, 붙어 있으면 어디서 한 건이 끝나는지 읽히지 않았다. 대신 카드와 갈리는
 *    지점을 셋 남긴다 — **작은 모서리(`field` 10)** · **좁은 간격(8)** · **본문 크기 글자**.
 *    기억 카드는 `card` 14 · 간격 12 · 이름이 `title` 20 이다.
 *
 * 🚨 **상태는 면 + 글자로 갈린다** (색만으로 가르지 않는다 · 문서 §3).
 *    - `active`: 기본 줄. 아래 자리에 묶인 기억 **칩**
 *    - `stand_alone`(이번만 그랬어요): 같은 면. 아래 자리에 **고친 표시**("이번만 그랬어요로
 *      고친 기록"). 실제로 있었던 일이라 물러서지 않는다
 *    - `inactive`(잘못된 기록): 고친 표시 + 낡은 기억 카드와 같은 말로 **물러선다**
 *      (`surface-muted` 면 · `ink-muted` 글자 · 뉴트럴 타일) — 근거에서도 빠진 기록이다
 * 🚨 **고친 표시는 칩이 아니다.** 아이콘 + 문장이고 면이 없다. 같은 자리에 서는 기억 칩과 같은
 *    알약 모양이면 "이번만 그랬어요" 가 기억 이름처럼 읽혔다 — 모양이 달라야 다른 것으로 읽힌다.
 *    라벨만 두지 않고 "…로 고친 기록" 까지 쓰는 것도 같은 이유다.
 *
 * 🚨 **줄을 끌고 가는 것은 그 기록이 무엇에 관한 것인지(`subject` · health 는 `symptom`)다**
 *    (#524). 부모가 적은 말(`raw_text`)은 그 아래 한 단계 약하게 선다. 한 발화는 관찰 여러 건으로
 *    나뉘어 저장되는데(최상위 CLAUDE.md §4 ②), 원문이 제목이던 때는 같은 문장이 줄마다 같은
 *    무게로 반복돼서 "같은 말이 세 번 저장됐다" 로 읽혔다.
 *    ⚠️ **`subject` 와 묶인 기억 이름(`merge_key`)은 같은 말인 경우가 많다.** Curator 가 새 기억의
 *    이름을 첫 기록의 `subject` 로 짓고, 이름이 같으면 그 기억에 붙인다 (`link_step.py`). 그래서
 *    "물놀이" 제목 아래 "관련된 기억 | 물놀이" 칩이 서는 것이 보통이다 — 받아들인 겹침이다.
 *    제목은 "이 기록이 무엇에 관한 것인가", 칩은 "이 기록이 어느 기억에 세어졌나" 라 같은
 *    단어여도 말하는 것이 다르고, 칩이 빠지면 기록이 기억으로 쌓인다는 것이 안 보인다.
 *    제목을 `merge_key` 로 쓰지 않는 이유는 늘 있는 값이 아니어서다 — routine 과 아직 안 묶인
 *    기록은 `affinity` 가 `null` 이다. `subject` 는 health 밖의 모든 기록에 있다.
 *    health 의 증상 칩은 뺐다 — 제목과 **같은 필드**라 늘 같은 말이고, 이어진 기억도 없다.
 *
 * 줄의 뼈대는 네 층이다 — 왼쪽 타일(어느 영역인지) 옆에 제목 · 원문 · 메타 · 칩(또는 고친 표시).
 * 🚨 **제목은 `body` 600 이다.** 굵기로 원문(`body-sm`)과 가르고, 크기는 `body` 에 둔다 —
 *    `section`(17) 이상으로 올리면 기억 카드 이름(`title` 20)과 갈리는 글자 크기가 좁아진다.
 * 🚨 **줄 안에 가운뎃점을 쓰지 않는다** (제품 결정 · #524). 메타는 간격으로, 칩은 세로 선과
 *    글자색으로 가른다.
 * 전부 같은 크기의 글자로 쌓으면 훑을 기준선이 없어서 목록이 통째로 회색 덩어리로 읽힌다.
 * 🚨 **타일은 그 영역의 도메인 색이다** (문서 §3 · §7 "07 기록 줄 · 기억 카드").
 *    도메인 색의 뜻을 "어느 Agent 결과인가" 에서 **"어느 영역인가"** 로 넓히면서 열린 자리다 —
 *    07 은 제안이 아니라 **쌓인 것을 훑는 화면**이고, 목록이 네 영역을 섞어 내려주므로
 *    "한 화면에 도메인 색 2개" 상한의 예외이기도 하다 (그 상한은 제안 화면의 규칙이다).
 *    🚨 **브랜드는 여전히 못 쓴다.** "어디서 왔나"(도메인) 와 "무엇을 하는가"(브랜드) 를
 *    같은 색으로 쓰지 않는다 — 여기 서 있는 것은 고르는 버튼이 아니다.
 *    🚨 **색이 단독 신호가 되지 않는다** — 타일 옆에 영역 이름이 항상 글자로 선다.
 *
 * 🚨 **이 줄이 무엇으로 이어졌는지는 칩이 진다.** "이 기록이 어느 기억에 묶였나" 는 이 제품의
 *    논지 자체(기록이 쌓여 기억이 된다)라, 날짜와 같은 무게의 회색 글자로 두면 목록에서
 *    제일 약한 것이 제일 중요한 사실이 된다.
 *
 * 🚨 **날짜를 만들지 않는다.** `observed_label`("오늘" · "3일 전")은 서버 문구다 (CLAUDE.md §3).
 *    없으면 그 자리를 비운다 — 프론트가 `observed_to` 로 계산해 채우지 않는다.
 *
 * 🚨 **도메인 색을 쓰지 않는다** (`DomainMeta` 주석). 07 에는 Agent 결과가 없고, 목록은
 *    4개 도메인이 섞여 내려온다.
 *
 * 🚨 **고친 기록도 이 목록에 선다** (#266 — 서버는 `deleted` 만 뺀다). 묶인 기억 칩을 그대로
 *    두면 고친 기록도 여전히 기억으로 센다고 읽혀서, 고친 줄은 그 칩을 달지 않는다.
 */
export function ObservationList({
  observations,
  onOpen,
}: {
  observations: Observation[];
  onOpen: (observation: Observation) => void;
}) {
  return (
    <ul className="flex flex-col gap-2">
      {observations.map((observation) => (
        <li key={`${observation.kind}:${observation.id}`}>
          <ObservationRow observation={observation} onOpen={() => onOpen(observation)} />
        </li>
      ))}
    </ul>
  );
}

function ObservationRow({ observation, onOpen }: { observation: Observation; onOpen: () => void }) {
  const agent = observationAgent(observation.kind);
  const wrong = observation.status === "inactive";
  const title = observationTitle(observation);

  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "min-h-touch rounded-field ease-standard flex w-full items-start gap-3 border px-4 py-3.5 text-left transition-colors duration-120",
        wrong
          ? "bg-surface-muted active:bg-line border-transparent"
          : "bg-surface border-line active:bg-surface-muted",
      )}
    >
      {/* 모양과 이름은 기록 종류, 색은 그 기록을 읽는 Agent 다 — 교육 · 생활습관이 같은 성장 색이다 (#501). */}
      <IconTile
        icon={OBSERVATION_ICON[observation.kind]}
        tone={wrong ? "neutral" : "plain"}
        className={wrong ? undefined : domainField(agent)}
      />

      <span className="flex min-w-0 flex-1 flex-col gap-1.5">
        <span
          className={cn(
            "text-body ease-standard font-semibold transition-colors duration-120",
            wrong ? "text-ink-muted" : "text-ink",
          )}
        >
          {title ?? observation.raw_text}
        </span>

        {/* 원문은 근거라 한 단계 약하게 선다. 자르지 않는다 (문서 §10). 제목이 원문이면 다시 쓰지 않는다. */}
        {title ? <span className="text-body-sm text-ink-muted">{observation.raw_text}</span> : null}

        {/* 기록 종류와 날짜는 가운뎃점 없이 간격으로 가른다 (#524). 칩은 아래 줄. */}
        <span
          className={cn(
            "text-caption ease-standard flex flex-wrap gap-x-2 transition-colors duration-120",
            wrong ? "text-ink-muted" : "text-ink-subtle",
          )}
        >
          <span>{observationLabel(observation.kind)}</span>
          {observation.observed_label ? <span>{observation.observed_label}</span> : null}
        </span>

        <ObservationLink observation={observation} />
      </span>

      <ChevronRight
        aria-hidden
        size={ICON_SIZE.md}
        strokeWidth={ICON_STROKE}
        className={cn(
          "ease-standard mt-2 shrink-0 transition-colors duration-120",
          wrong ? "text-ink-muted" : "text-ink-subtle",
        )}
      />
    </button>
  );
}

/**
 * 줄의 제목 — 이 기록이 무엇에 관한 것인가. 04 대화 결과의 기록 줄(`run-result.tsx`)과 같은 필드를
 * 쓴다 (04 는 health 에 `observed_time` 까지 붙이고, 07 은 시각을 상세에만 둔다).
 * health 는 `subject` 키가 없어 증상이 그 자리를 받는다. 증상이 없으면 `null` 이고, 줄은 원문을 제목으로 쓴다.
 */
function observationTitle(observation: Observation): string | null {
  if (isHealthObservation(observation)) {
    const symptoms = observation.domain_fields.symptom;
    return symptoms && symptoms.length > 0 ? symptoms.join(", ") : null;
  }
  return observation.subject || null;
}

/** 줄 아래에 하나만 서는 작은 표식. 글자 줄이 아니라 **모양**이라 메타와 안 섞인다. */
function LinkChip({ label, value }: { label: string; value: string }) {
  return (
    // 🚨 `truncate` 로 잘라 맞추지 않는다 (문서 §10). 글자를 키우면 칩이 두 줄이 될지언정
    //    묶인 기억 이름이 잘리지는 않는다 — 이름이 반만 보이면 무엇에 묶였는지 알 수 없다.
    <span className="bg-surface-muted text-ink-muted text-caption min-h-chip inline-flex w-fit max-w-full items-center gap-2 rounded-full px-2.5 py-1">
      {/* 라벨과 이름은 가운뎃점이 아니라 세로 선과 글자색으로 가른다 (#524). 선은 글자가 아니라 면이라
          기호 금지(apps/web/CLAUDE.md §5)에 걸리지 않고, 읽기 프로그램도 읽지 않는다. `line` 은
          `surface-muted` 위에서 안 보여서 테두리 쪽 `line-strong` 이다. 이름이 `ink` 인 것은
          `ink-muted` 가 라벨의 `ink-subtle` 과 가까워 둘이 한 구절로 읽혔기 때문이다. */}
      <span className="text-ink-subtle shrink-0">{label}</span>
      <span aria-hidden className="bg-line-strong h-3 w-px shrink-0" />
      <span className="text-ink">{value}</span>
    </span>
  );
}

/**
 * 고친 기록의 표시. 🚨 칩(면 + 알약)이 아니다 — 같은 자리의 기억 칩과 모양으로 갈린다.
 * 04 지난 대화의 저장 카드도 같은 표시를 쓴다 (`run-result.tsx` 의 `ObservationRow` · #228 리뷰).
 */
export function CorrectedMark({ label }: { label: string }) {
  return (
    <span className="text-caption text-ink-muted inline-flex items-center gap-1">
      <PencilLine aria-hidden size={ICON_SIZE.sm} strokeWidth={ICON_STROKE} className="shrink-0" />
      {josa(label, "으로/로")} 고친 기록
    </span>
  );
}

/**
 * 관찰이 무엇과 이어져 있는지 한 줄.
 *
 * 🚨 **health 는 모양이 다르다** — `subject` · `polarity` · `affinity` 키가 **아예 없다.**
 *    `null` 검사가 아니라 `kind === "observation_health"` 로 분기한다 (apps/web/CLAUDE.md §4).
 *    승격 파이프라인 밖이라 묶일 프로필이 없다. 예전엔 증상 칩이 그 자리를 받았는데, 증상이
 *    줄 제목이 되면서(#524) 같은 말이 두 번 서게 돼 뺐다.
 */
function ObservationLink({ observation }: { observation: Observation }) {
  // 고친 기록은 기억으로 세지 않는다 — 묶인 기억 칩 대신 무엇으로 고쳤는지가 그 자리에 선다.
  if (observation.status !== "active") {
    return <CorrectedMark label={CORRECTED_OBSERVATION[observation.status].label} />;
  }

  if (isHealthObservation(observation) || !observation.affinity) return null;
  // "기억" 한 단어로는 이 기록이 그 기억에 세어졌다는 관계가 안 읽힌다 (#524).
  return <LinkChip label="관련된 기억" value={observation.affinity.merge_key} />;
}
