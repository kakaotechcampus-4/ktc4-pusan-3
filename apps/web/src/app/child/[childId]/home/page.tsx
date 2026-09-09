import { Card } from "@/components/ui/card";
import { Screen } from "@/components/ui/screen";

/**
 * 03 홈 — **플레이스홀더다.** 온보딩이 끝나면 여기로 오는데 라우트가 없으면 404 가 나서 둔 것이고,
 * 화면 자체는 다음 이슈에서 만든다 (GET /children/{cid}/home · highlight · agent_prompts).
 */
export default async function HomePage({ params }: PageProps<"/child/[childId]/home">) {
  const { childId } = await params;

  return (
    <Screen className="justify-center gap-4">
      <h1 className="text-title text-ink">홈 화면은 다음 이슈예요</h1>
      <Card>
        <p className="text-body-sm text-ink-muted">
          온보딩은 여기까지 왔어요. 03 홈은 아직 비어 있습니다.
        </p>
        <p className="text-caption text-ink-subtle mt-2">child_id — {childId}</p>
      </Card>
    </Screen>
  );
}
