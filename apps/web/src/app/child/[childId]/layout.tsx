import { ChildScope } from "@/components/child-scope";

/**
 * 화면 03~09 는 전부 이 아래에 둔다 (`/child/[childId]/home` 처럼).
 * 쿼리 키가 이미 qk.child(cid) 스코프라 URL 파라미터와 1:1 로 맞는다.
 *
 * `key={childId}` — Next 도 세그먼트 값이 바뀌면 이 아래를 새로 띄우지만(`layout-router` 의 캐시 키),
 * 볼 수 있는지의 판정(`ChildScope` · #802)이 "이 아이 화면에 들어온 뒤 받은 `/me`" 를 기준으로 해서
 * 그 동작에 기대지 않고 적어 둔다. 셸이 이어지면 앞 아이에서 받은 것으로 판정하게 된다.
 */
export default async function ChildLayout({ children, params }: LayoutProps<"/child/[childId]">) {
  const { childId } = await params;
  return (
    <ChildScope key={childId} childId={childId}>
      {children}
    </ChildScope>
  );
}
