import { ChildScope } from "@/components/child-scope";

/**
 * 화면 03~09 는 전부 이 아래에 둔다 (`/child/[childId]/home` 처럼).
 * 쿼리 키가 이미 qk.child(cid) 스코프라 URL 파라미터와 1:1 로 맞는다.
 */
export default async function ChildLayout({ children, params }: LayoutProps<"/child/[childId]">) {
  const { childId } = await params;
  return <ChildScope childId={childId}>{children}</ChildScope>;
}
