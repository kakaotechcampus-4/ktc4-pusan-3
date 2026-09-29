# Curator ↔ Profile 상태 전이 통합 계획

PR #154 (프로필 상태 전이 규칙) + PR #163 (Curator 임베딩·연결 단계) 통합.
작업 브랜치: `feat/be-163-curator-integration` (base: `feat/be-149-up-down`)

작성일: 2026-09-30
담당: 김명성

---

## 0. 현재 상태

| PR | 한 일 | 안 한 일 |
| --- | --- | --- |
| **#154** (profile) | 순수 함수 `compute_profile_status`, `recompute_profile` 서비스, correction 핸들러, `last_observed_on` 재계산 | recompute 호출 시점 (TODO) |
| **#163** (curator) | 임베딩 단계, 연결 단계 (link_step), Jev 판정기, 인메모리 저장소, 보류 테이블 | DB `CuratorStore` 구현, 실행 흐름 연결, `last_observed_on` 갱신 |

**접점**: Curator 가 관찰을 Profile 에 연결한 뒤 → 해당 Profile 에 `recompute_profile` 을 돌려야 상태 전이가 일어난다.

---

## 1. 작업 목록

### Phase A — DB CuratorStore 구현

Curator 의 `CuratorStore` Protocol 을 SQLAlchemy 로 구현한다. 인메모리 저장소와 1:1 대응.

**파일**: `app/domains/memory/curator/db_store.py` (신규)

| Protocol 메서드 | DB 동작 | 비고 |
| --- | --- | --- |
| `list_unembedded(child_id)` | 3개 도메인 테이블에서 `status=active AND embedding IS NULL` 조회 | UNION 또는 도메인별 순차 쿼리. `created_at` 오름차순 |
| `save_embeddings(vectors)` | 도메인별 `UPDATE SET embedding = :vec WHERE id = :id` | 배치 UPDATE |
| `list_unlinked(child_id)` | `status=active AND embedding IS NOT NULL AND affinity_id IS NULL` | `created_at` 오름차순 |
| `list_profiles(child_id, domain, polarity)` | `SELECT FROM profile_affinity WHERE child_id AND domain AND polarity` | archived 포함. `ORDER BY created_at, id` |
| `link(domain, observation_id, affinity_id)` | `UPDATE SET affinity_id = :aid WHERE id = :oid` | |
| `create_profile(...)` | `INSERT INTO profile_affinity` | state=candidate, strength=기본값 |
| `record_uncertain(domain, observation_id, subject_hash)` | `INSERT ON CONFLICT UPDATE` on `observation_link_hold` | subject_hash 불일치 시 count=1 리셋 |
| `clear_hold(domain, observation_id)` | `DELETE FROM observation_link_hold WHERE {domain}_id = :oid` | 없으면 no-op |

**주의사항**:
- `list_unembedded`, `list_unlinked` 은 3개 도메인 테이블을 조회해야 한다. 도메인별 순차 쿼리 후 `created_at` 로 병합 정렬이 가장 단순
- `link` 와 `clear_hold` 는 같은 트랜잭션 (ports.py 주석 참고)
- `create_profile` 시 `strength` 는 `STRENGTH_DEFAULT` (0.5) — #154 스펙

### Phase B — 연결 후 recompute_profile 호출

Curator 가 관찰을 Profile 에 연결하거나 새 candidate 를 만든 뒤, 영향 받은 Profile 의 상태를 재계산한다.

**위치**: `link_observations` 를 호출하는 쪽 (실행 흐름)

```python
result = await link_observations(store, embedder, judge, child_id=child_id)

# 연결로 관찰이 늘어난 Profile → last_observed_on 갱신 + 상태 재계산
for profile_id in result.affected_profile_ids:
    latest = await get_latest_active_observed_on(session, affinity_id=profile_id, domain=...)
    if latest is not None:
        profile = await session.get(ProfileAffinity, profile_id)
        if profile is not None:
            profile.last_observed_on = latest
            await session.flush()
    await recompute_profile(session, profile_id=profile_id, today=today)
```

**설계 결정**:
- `link_observations` 안에서 recompute 를 부르지 않는다 — Curator 는 저장소 계약(CuratorStore)만 알고, profile 서비스 의존을 갖지 않는다
- 호출하는 쪽(실행 흐름)이 `affected_profile_ids` 를 순회하며 recompute 한다
- `last_observed_on` 갱신은 #154 에서 만든 `get_latest_active_observed_on` 을 재사용한다

### Phase C — 실행 흐름 연결

관찰이 저장된 뒤 Curator 를 언제, 어떻게 돌릴 것인지.

**선택지**:

| 방식 | 장점 | 단점 |
| --- | --- | --- |
| 관찰 저장 직후 같은 요청에서 | 즉시 반영. 검색·추천에 바로 잡힘 | 요청 latency 증가 (임베딩 API + 판정기 호출) |
| 백그라운드 태스크 (같은 프로세스) | latency 분리 | 실패 시 재시도 필요. --workers 1 전제 (#134) |
| 별도 배치 (cron / worker) | 완전 분리 | 인프라 추가. 반영 지연 |

**선택**: 백그라운드 태스크 (같은 프로세스).
- 관찰 저장 트랜잭션을 먼저 커밋한 뒤, 백그라운드에서 Curator 를 돌린다
- 관찰 저장이 임베딩/판정 실패에 묶이지 않는다 — `link_observations` docstring 의 경고("같은 트랜잭션에서 부르면 실패가 관찰 저장까지 되돌린다")를 구조적으로 회피
- Curator 는 별도 세션·트랜잭션으로 동작한다. 실패해도 관찰은 이미 저장돼 있고, 벡터·연결이 안 된 관찰은 다음 실행이 자동으로 집는다 (list_unembedded / list_unlinked)
- --workers 1 전제(#134)이므로 같은 프로세스의 `BackgroundTasks` 또는 `asyncio.create_task` 로 충분. 인프라를 늘리지 않는다
- 동시 실행: 같은 아이에 대해 두 요청이 동시에 돌 가능성은 낮다 (한 부모가 연속 입력). 우선 잠금 없이 가되, 동시성 문제 발생 시 `SELECT ... FOR UPDATE` 추가

### Phase D — migration 정리

1. `observation_link_hold` 마이그레이션(`b901cf42a99f`)의 `down_revision` 을 이 브랜치의 마지막 마이그레이션(`a2b3c4d5e6f7`)으로 변경
2. DB CuratorStore 가 추가 인덱스를 필요로 하면 같은 마이그레이션 또는 별도 마이그레이션으로

### Phase E — 테스트

| 대상 | 종류 | 검증 |
| --- | --- | --- |
| DB CuratorStore | integration | 인메모리와 동일한 동작. 특히 `record_uncertain` UPSERT, `clear_hold` |
| 연결 → recompute 체인 | integration | 관찰 3건 연결 → Profile 이 candidate → confirmed 로 전이 |
| `last_observed_on` 갱신 | integration | 연결 후 Profile 의 `last_observed_on` 이 관찰 날짜를 반영 |
| 실행 흐름 | integration | 관찰 저장 → Curator → recompute 가 한 흐름으로 동작 |

---

## 2. 보류 테이블 FK ↔ soft delete 충돌 (후속 — PR #178 머지 후)

**문제**: PR #178 이 observation 삭제를 hard delete → soft delete (`status = 'deleted'`) 로 바꾼다.
`observation_link_hold` 의 FK 는 `ON DELETE CASCADE` 를 전제로 설계됐는데, soft delete 에서는 CASCADE 가 실행되지 않는다.

**관련 PR**: [#178 — observation soft delete](https://github.com/kakaotechcampus-4/ktc4-pusan-3/pull/178)

**김명성 코멘트 요약**:
> 멘토님 리뷰를 통해 observation 삭제를 hard delete 가 아닌 `status = deleted` 로 변경했다.
> suggestion 에 들어간 observation 이 삭제되어도 "이 observation 을 기반으로 추천되었음"을 나타내기 위함이다.
> CASCADE 가 실행되지 않으므로 FK 방식 대신 다른 방식을 고려해야 하고,
> observation 이 삭제되는 시점에 보류 카운트도 동시에 수정되어야 한다.

**해결 방향** (PR #178 머지 후 이 브랜치에서 진행):

| 안 | 변경 | 장단점 |
| --- | --- | --- |
| **A. FK 제거 + 삭제 시 코드에서 정리** | `observation_link_hold` 의 FK 3개를 일반 UUID 칼럼으로 변경. `delete_observation()` 에서 hold 행 삭제 추가 | 단순. DB 보장 → 코드 보장으로 바뀜 |
| **B. FK 유지 + trigger** | `BEFORE UPDATE` trigger 로 `status='deleted'` 전환 시 hold 행 삭제 | DB 레벨 보장 유지. trigger 관리 비용 |
| **C. hold 조회 시 deleted 필터** | hold 를 읽을 때 관찰이 deleted 인지 확인. hold 행은 남겨둠 | 변경 최소. 고아 행이 쌓임 |

**추천**: **안 A**. 6인 10주 프로젝트에서 trigger 는 과하고, 고아 행은 나중에 문제가 된다.
`delete_observation()` 이 이미 status 변경을 하는 한 곳이므로 거기에 hold 정리를 추가하면 된다.

구체적 변경:
1. 마이그레이션: FK 3개 DROP → 일반 UUID 칼럼으로 (UNIQUE 유지)
2. `delete_observation()` 에서 `DELETE FROM observation_link_hold WHERE {domain}_id = :oid` 추가
3. `CuratorStore.list_unembedded` / `list_unlinked` 는 이미 `status=active` 필터가 있어 deleted 관찰은 자연스럽게 제외됨
4. 보류 중인 관찰이 삭제되면 hold 행도 사라짐 → uncertain 카운트 리셋 (의도한 동작: 삭제 후 같은 내용을 다시 입력하면 새 관찰이므로 처음부터)

**시점**: PR #178 이 develop 에 머지된 후. 이 통합 브랜치에서 후속 커밋으로 진행한다.

---

## 3. 작업 순서

```
Phase A  DB CuratorStore 구현
  ↓
Phase D  migration 정리 (down_revision 체인)
  ↓
Phase B  연결 후 recompute 호출 배선
  ↓
Phase C  실행 흐름 연결 (관찰 저장 → Curator → recompute)
  ↓
Phase E  통합 테스트
  ↓
(PR #178 머지 후)  §2 보류 테이블 FK → soft delete 대응
```

---

## 4. 파일 변경 예상

| 파일 | 변경 |
| --- | --- |
| `app/domains/memory/curator/__init__.py` | 신규. 패키지 |
| `app/domains/memory/curator/db_store.py` | 신규. DB CuratorStore (Phase A) |
| `app/domains/memory/curator/recompute.py` | 신규. 연결 후 last_observed_on + recompute (Phase B) |
| `app/domains/memory/curator/trigger.py` | 신규. 백그라운드 트리거 (Phase C) |
| `app/agents/pipeline.py` | 호출 지점 TODO 추가 (Phase C) |
| `alembic/versions/b901cf42a99f_*.py` | `down_revision` 변경 (Phase D) |
| `tests/integration/test_curator_db_store.py` | 신규. DB Store 스펙 18건 |
| `tests/integration/test_curator_recompute_chain.py` | 신규. 연결→승격 체인 9건 |
| `tests/integration/test_curator_stages.py` | 신규. 단계별 독립 검증 + E2E 15건 |

---

## 5. 안 하는 것

- Curator 연결 로직 변경 (link_step, judge 등) — 이미 #163 에서 검증됨
- 순수 함수·규칙 변경 — 이미 #154 에서 검증됨
- 동시 실행 제어 — 현실적으로 낮은 확률. 문제 발생 시 후속
- `merge_key` 변경 (보호자가 Profile 이름을 고치는 것) — 별도 이슈
- 배치 recompute (시간 경과에 의한 archived 전이) — #154 TODO 로 기록됨

## 6. PR 리뷰 포인트

### 이 PR 에서 다루지 않았지만 확인이 필요한 것

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| `ports.py:100` docstring "strength 기본값 0.3" → 0.5 | 이시하님 코드 | #163 리뷰에서 언급 필요 |
| CLAUDE.md §2 "6개월 이상 지난 관심 기록은 단독 근거로 쓰지 않는다" | **미구현** | 절대 규칙. Agent 가 evidence 를 가져올 때 6개월 필터가 없으면 §2 위반. 현재 `ARCHIVED_WINDOW_DAYS=21` 만 있고 180일 감쇠 규칙은 코드에 없다. Agent evidence 조회 경로에서 후속 이슈로 처리해야 한다 |
| education 도메인 DB Store 테스트 | 미작성 | `ObservationEducation.topic` 필수 필드 때문에 별도 헬퍼 필요 |
