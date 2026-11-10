import { cn } from "@/lib/cn";

/** 출처를 그릴 때 필요한 칸만 — `DocEvidence` (일반 추천 근거)와 `DocSource` (일반 정보 답변 근거)가 모두 맞는다. */
interface DocSourceLike {
  ref: { id: string };
  title: string;
  source_org: string;
  source_title: string;
}

/**
 * 문서 행 근거의 "참고 · 제목 (기관, 출처)" 줄 (일반 추천 카드 · 일반 정보 답변이 같이 쓴다).
 *
 * 🚨 출처(`source_org` · `source_title`)를 빼지 않는다 — CC BY 문서는 출처 표시가 이용 조건이다.
 * 🚨 문서 제목도 텍스트 노드로만 그린다 (raw HTML 금지). 두 곳이 따로 그리면 표시 방식이 어긋날 수
 *    있어서 한 컴포넌트로 둔다. 비어 있으면 아무것도 그리지 않는다.
 */
export function DocSourceList({
  docs,
  className,
}: {
  docs: readonly DocSourceLike[];
  className?: string;
}) {
  if (docs.length === 0) return null;
  return (
    <ul className={cn("flex flex-col gap-1", className)}>
      {docs.map((doc) => (
        <li key={doc.ref.id} className="text-caption text-ink-subtle">
          참고 · {doc.title} ({doc.source_org}, {doc.source_title})
        </li>
      ))}
    </ul>
  );
}
