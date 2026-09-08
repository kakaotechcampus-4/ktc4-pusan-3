export default function Page() {
  return (
    <main className="pt-safe pb-safe mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 px-6">
      <div>
        <p className="text-ink-muted text-sm">부산대 3팀</p>
        <h1 className="mt-1 text-2xl font-bold tracking-tight">육아기억 AI</h1>
        <p className="text-ink-muted mt-3 text-[15px] leading-relaxed">
          육아를 가장 많이 아는 AI가 아니라,
          <br />
          우리 아이를 가장 오래 알아온 AI.
        </p>
      </div>

      <div className="border-line bg-surface-muted text-ink-muted rounded-card border p-4 text-sm">
        기본 세팅만 올라간 상태다. 화면 01~10 은{" "}
        <code className="text-ink">docs/api/api-interface-v1.html</code> 의 화면 → 호출 표를
        기준으로 붙인다.
      </div>
    </main>
  );
}
