import { Card } from "@/components/ui/card";
import type { ApiError } from "@/lib/api";

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
  const deeplink = error.consentDeeplink;

  return (
    <Card>
      <p className="text-body text-ink">먼저 동의가 필요해요</p>
      <p className="text-body-sm text-ink-muted mt-2">
        동의를 받기 전이라 {what} 저장된 것은 하나도 없어요.
      </p>
      {/* 동의 화면(10 설정)은 아직 없다. 링크를 거는 대신 갈 곳을 적어 둔다 —
          아무 데도 안 가는 링크를 만들지 않는다. */}
      <p className="text-caption text-ink-subtle mt-2">
        동의 화면은 아직 없어요 {deeplink ? `(/child/${childId}/${deeplink})` : null}
      </p>
    </Card>
  );
}
