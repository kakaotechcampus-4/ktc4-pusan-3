/**
 * 일반 추천이 섞여 나온 화면의 안내 카드 문구 (`ScarcityCard`).
 *
 * 🚨 **카드의 "기록 N건" 은 Agent별 누적 건수이고, 개인화 여부는 이번 추천이 아이 기록을 직접 썼는가다.**
 *    서버의 `general[].record_count` 는 Agent별 남은 관찰 수라, 22건이 있어도 이번 추천에 인용할 수
 *    있는 기록은 0건일 수 있다 (#662). 그래서 건수는 숨기지 않고 그대로 말하되 "N건뿐이에요" ·
 *    "더 쌓이면" 처럼 **기록이 적어서 그렇다고 원인을 단정하지 않는다** — 이미 22건이 있는 화면에서
 *    틀린 말이 된다. 말하는 것은 사실 둘이다: 기록이 몇 건 있다 · 이번 추천에는 직접 쓰지 않았다.
 * 🚨 **개인화도 같이 선 화면에서는 "위 추천" 이라고 하지 않는다** (#512 리뷰). 위에 선 개인화 추천까지
 *    일반 기준이라고 부르는 것도 §2 가 막는 혼동이다 — 카드에 붙은 라벨 "일반 추천" 을 가리킨다.
 * 🚨 **"더 남기면 개인화된다" 를 약속하지 않는다.** 질문이 있을 때만 질문 하나에 이어 붙인다 —
 *    답해 주신 것이 다음 추천의 근거가 되는 것은 질문을 한 경우뿐이다.
 */

import { josa } from "es-hangul";
export interface ScarcityCopyInput {
  /** `general[].record_count` — Agent별 누적 건수. 응답의 Agent 순서를 유지한다. */
  records: readonly AgentRecordCount[];
  /** 일반 추천이 없는 예전 응답을 위한 방어값. 정상 일반 추천 문구에는 쓰지 않는다. */
  fallbackCount: number;
  /** 같은 화면에 개인화 추천도 섰다. */
  hasPersonal: boolean;
  /** Agent 가 되물었다 — 질문이 카드 아래에 선다. */
  asked?: boolean;
}

export interface AgentRecordCount {
  label: string;
  count: number;
}

export interface ScarcityCopy {
  title: string;
  body: string;
}

export function scarcityCopy({
  records,
  fallbackCount,
  hasPersonal,
  asked = false,
}: ScarcityCopyInput): ScarcityCopy {
  if (records.length === 0) {
    return {
      title: "이번에는 제안할 근거를 찾지 못했어요",
      body: `${fallbackRecorded(fallbackCount)} 근거 없이 제안을 만들지는 않을게요.`,
    };
  }

  const followup = asked ? " 하나만 알려 주시면 다음 제안에 참고할게요." : "";

  if (hasPersonal) {
    const only = records[0]!;
    return {
      title: "일부는 일반 추천으로 골랐어요",
      body:
        (records.length === 1 && only.count > 0
          ? `${only.label} 기록은 ${only.count}건 있지만, 일반 추천이라고 적힌 것은 아이 기록 없이 골랐어요.`
          : `${recorded(records)} 일반 추천이라고 적힌 것은 아이 기록 없이 골랐어요.`) + followup,
    };
  }

  return {
    title: "이번에는 일반 추천으로 골랐어요",
    body: `${recorded(records)} 이번에는 아이 기록을 쓰지 않고, 제안마다 아래 적힌 기준으로 골랐어요.${followup}`,
  };
}

/** Agent별 누적 건수 한 문장. 0건도 숨기지 않는다 (CLAUDE.md §2). */
function recorded(records: readonly AgentRecordCount[]): string {
  const first = records[0]!;
  const second = records[1];
  if (!second) {
    return first.count === 0
      ? `${first.label} 기록은 아직 없어요.`
      : `${first.label} 기록은 ${first.count}건 있어요.`;
  }
  if (first.count === 0 && second.count === 0) {
    return `${josa(first.label, "와/과")} ${second.label} 기록은 아직 없어요.`;
  }
  if (first.count > 0 && second.count > 0) {
    return `영역별 기록은 ${first.label} ${first.count}건, ${second.label} ${second.count}건이에요.`;
  }
  const present = first.count > 0 ? first : second;
  const absent = first.count === 0 ? first : second;
  return `${present.label} 기록은 ${present.count}건 있고, ${absent.label} 기록은 아직 없어요.`;
}

/** 일반 추천이 없는 예전 응답에서만 쓰는 방어 문구. */
function fallbackRecorded(count: number): string {
  return count === 0 ? "쌓인 기록이 아직 없어요." : `쌓인 기록은 ${count}건 있어요.`;
}
