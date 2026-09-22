# apps/web

아이캐치 프론트엔드. Next.js 16 App Router.

## 로컬 실행

```bash
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE_URL 확인
make web-install             # 저장소 루트에서. = pnpm install
make web-dev                 # http://localhost:3000
```

이 폴더에서 `pnpm dev` 로 직접 돌려도 같다 — `make` 쪽은 6명이 파트를 오갈 때
루트에서 한 줄로 끝내기 위한 것이다. 전체 목록은 루트에서 `make`.

| | |
| --- | --- |
| `make web-dev` | 개발 서버 |
| `make web-build` | 프로덕션 빌드. **dev 에서 안 보이는 에러가 여기서 난다** (`useSearchParams` Suspense 누락 등 — CLAUDE.md §3) |
| `make web-start` | 빌드 결과를 로컬에서 실행 |
| `make web-check` | typecheck + lint + test. PR 올리기 전 |

백엔드 없이 화면을 볼 때는 `.env.local` 에서 `NEXT_PUBLIC_API_MOCKING=enabled` (CLAUDE.md §7).

구현 규칙·버전 고정 이유는 [CLAUDE.md](CLAUDE.md) 참고.
API 계약은 [`docs/api/api-interface-v1.html`](../../docs/api/api-interface-v1.html) 이 정본이다.
