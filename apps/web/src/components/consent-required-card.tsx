import Link from "next/link";

import { Card } from "@/components/ui/card";
import type { ApiError } from "@/lib/api";
import { consentItem } from "@/lib/consent";

/**
 * `403 consent_required` 를 만났을 때. **저장이 아예 안 된 상태**다 (계약서 §04 "동의는 저장보다 먼저다").
 *
 * 🚨 **일반 실패로 뭉뚱그리지 않는다.** "불러오지 못했어요" 로 그리면 부모는 다시 시도만 누르는데,
 *    다시 시도해도 같은 403 이다. 무엇이 막혔는지와 어디로 가야 하는지를 말해야 한다
 *    (apps/web/CLAUDE.md §3 에러).
 *
 * 🚨 **`CardFailed` 가 아니다.** 동의가 없는 것은 고장이 아니라 아직 안 한 일이다.
 *
 * ⚠️ 계약서의 `deeplink` 는 `settings/consent` 처럼 **아이를 안 담은 상대 경로**다.
 *    지금 보고 있는 아이 경로 아래에 붙여서 쓴다 — 이 조립을 화면마다 하면 어긋난다.
 *
 * 🚨 **딥링크를 그대로 주소로 쓰지 않는다.** 서버가 준 문자열이라 `settings/consents` 처럼
 *    지금 없는 경로가 오기도 하고(목이 그렇다), 외부 주소가 오면 화면이 앱 밖으로 나간다.
 *    가는 곳은 10 설정 화면 하나이고, 딥링크는 **어느 구역인지 힌트**로만 쓴다.
 */
export function ConsentRequiredCard({
  childId,
  error,
  /**
   * 이 화면에서 무엇이 막혔는지. **문장을 통째로** 넘긴다 ("제안을 준비할 수 없어요").
   * 어간만 받아서 "…할 수 없어요" 를 붙이면 화면마다 말이 어긋난다 (실제로 그렇게 났다).
   */
  what,
}: {
  childId: string;
  error: ApiError;
  what: string;
}) {
  // 🚨 스코프 코드를 화면에 그대로 내지 않는다 — `child_health` 는 부모가 읽을 말이 아니다.
  const scopes = error.detail?.scopes;
  const missing = (Array.isArray(scopes) ? scopes : [])
    .map((s) => (typeof s === "string" ? consentItem(s)?.label : undefined))
    .filter((label): label is string => Boolean(label));

  return (
    <Card>
      <p className="text-body text-ink">먼저 동의가 필요해요</p>
      <p className="text-body-sm text-ink-muted mt-2">
        동의를 받기 전이라 {what} 저장된 것은 하나도 없어요.
      </p>
      {missing.length > 0 ? (
        <p className="text-caption text-ink-subtle mt-2">
          설정 화면의 동의에서 켤 수 있어요 ({missing.join(", ")}).
        </p>
      ) : null}
      <div className="mt-3">
        <Link
          href={`/child/${childId}/settings`}
          className="text-button text-ink border-line rounded-field min-h-touch bg-surface ease-standard hover:bg-surface-muted active:bg-surface-muted inline-flex items-center px-4 py-2 transition-colors duration-120"
        >
          동의 관리로 가기
        </Link>
      </div>
    </Card>
  );
}
