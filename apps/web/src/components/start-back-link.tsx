import { ArrowLeft } from "lucide-react";

import { ICON_SIZE, ICON_STROKE } from "@/components/ui/icon";
import { IconButtonLink } from "@/components/ui/icon-button";

/**
 * 00-1 경로 고르기(`/start`)로 돌아가는 화살표. 그 화면이 고르게 한 두 갈래(01 `/onboarding` ·
 * `/invite`)가 같이 쓴다 — 한쪽에만 있으면 잘못 고른 사람이 다른 쪽에서 갇힌다.
 *
 * 다른 돌아가기 화살표와 같이 **`PageTitle` 과 한 줄**에 세우고 `-ml-3` 는 그 줄에 건다
 * (`docs/web/components-v1.md` §1).
 *
 * 🚨 **화면 전환 방향(`BACK`)을 붙이지 않는다.** 00-1 에서 들어올 때 움직임이 없는데 나갈 때만
 *    움직이면 짝이 안 맞는다 — 움직임을 주는 쌍은 디자인 시스템 §8 표에만 있다.
 */
export function StartBackLink() {
  return (
    <IconButtonLink href="/start" label="시작 방법 고르기로 돌아가기">
      <ArrowLeft aria-hidden size={ICON_SIZE.md} strokeWidth={ICON_STROKE} />
    </IconButtonLink>
  );
}
