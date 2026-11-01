/**
 * 일반 추천이 섞여 나온 화면의 안내 카드 문구 (`ScarcityCard`).
 *
 * 🚨 **카드의 "기록 N건" 은 누적 건수이고, 개인화 여부는 이번 추천이 아이 기록을 직접 썼는가다.**
 *    서버의 `scarcity.count` 는 기간 제한 없이 남은 관찰 전체라, 22건이 있어도 이번 추천에 인용할 수
 *    있는 기록은 0건일 수 있다 (#662). 그래서 건수는 숨기지 않고 그대로 말하되 "N건뿐이에요" ·
 *    "더 쌓이면" 처럼 **기록이 적어서 그렇다고 원인을 단정하지 않는다** — 이미 22건이 있는 화면에서
 *    틀린 말이 된다. 말하는 것은 사실 둘이다: 기록이 몇 건 있다 · 이번 추천에는 직접 쓰지 않았다.
 * 🚨 **개인화도 같이 선 화면에서는 "위 추천" 이라고 하지 않는다** (#512 리뷰). 위에 선 개인화 추천까지
 *    일반 기준이라고 부르는 것도 §2 가 막는 혼동이다 — 카드에 붙은 라벨 "또래 기준" 을 가리킨다.
 * 🚨 **"더 남기면 개인화된다" 를 약속하지 않는다.** 질문이 있을 때만 질문 하나에 이어 붙인다 —
 *    답해 주신 것이 다음 추천의 근거가 되는 것은 질문을 한 경우뿐이다.
 */
export interface ScarcityCopyInput {
  /** `Scarcity.count` — 쌓인 기록 건수(누적). 0 도 그대로 말한다. */
  count: number;
  /** 같은 화면에 또래 기준 일반 추천이 섰다. */
  hasGeneral: boolean;
  /** 같은 화면에 개인화 추천도 섰다. */
  hasPersonal: boolean;
  /** Agent 가 되물었다 — 질문이 카드 아래에 선다. */
  asked?: boolean;
}

export interface ScarcityCopy {
  title: string;
  body: string;
}

export function scarcityCopy({
  count,
  hasGeneral,
  hasPersonal,
  asked = false,
}: ScarcityCopyInput): ScarcityCopy {
  if (!hasGeneral) {
    return {
      title: "이번에는 추천할 근거를 찾지 못했어요",
      body: `${recorded(count)} 근거 없이 추천을 만들지는 않을게요.`,
    };
  }

  const followup = asked ? " 하나만 알려주시면 다음부터 달라져요." : "";

  if (hasPersonal) {
    return {
      title: "일부는 일반 기준으로 골랐어요",
      body:
        (count === 0
          ? `${recorded(count)} 또래 기준이라고 적힌 추천에는 직접 연결한 아이 기록이 없어요.`
          : `이 주제로 기록은 ${count}건 있지만, 또래 기준이라고 적힌 추천에는 직접 연결한 아이 기록이 없어요.`) +
        followup,
    };
  }

  return {
    title: "이번에는 일반 기준으로 골랐어요",
    body:
      (count === 0
        ? `${recorded(count)} 그래서 이번에는 일반 기준으로 골랐어요.`
        : `${recorded(count)} 이번 추천에 직접 사용한 아이 기록이 없어 일반 기준으로 골랐어요.`) +
      followup,
  };
}

/** 누적 건수 한 문장. 0건도 숨기지 않는다 (CLAUDE.md §2). */
function recorded(count: number): string {
  return count === 0 ? "이 주제로 쌓인 기록이 아직 없어요." : `이 주제로 기록은 ${count}건 있어요.`;
}
