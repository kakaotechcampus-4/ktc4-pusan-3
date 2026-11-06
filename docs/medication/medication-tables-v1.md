# 복약 테이블 v1

문서 목적: 복약 코스(`medication_schedule`) · 회차(`medication_dose`) · 알림 구독(`medication_notice_subscription`) 세 표의 모양과 그 이유를 고정한다.
제출 API · 회차 체크 API · 알림 켜기 · 끄기 API · DB 어댑터가 이 표 위에 올라간다. 그쪽이 지켜야 할 것(§5)도 여기 적는다.

작성일: 2026-10-21 · 담당: 김명성 · 갱신: 2026-11-06 체크한 보호자 `checked_by` · 알림 구독 표 ([#749](https://github.com/kakaotechcampus-4/ktc4-pusan-3/issues/749))
선행: 이슈 [#537](https://github.com/kakaotechcampus-4/ktc4-pusan-3/issues/537) 본문 DDL과 코멘트 결정 · [`event/event-draft-flow-v1.md`](../event/event-draft-flow-v1.md) · 루트 [`CLAUDE.md`](../../CLAUDE.md) §2 실행(게이트 ㉡)
근거: `apps/api/app/domains/medication/models.py` · `alembic/versions/c6e2a8d41f93_add_medication_tables.py` ·
`alembic/versions/361f26c96b2e_add_medication_dose_checked_by.py` ·
`alembic/versions/885b03bd5b61_add_medication_notice_subscription.py` ·
`tests/integration/test_medication_tables.py` · `tests/integration/test_medication_migration.py` ·
`tests/integration/medication/test_medication_subscription.py`

**이 문서에 없는 것** — 초안 제출 API, 회차 체크 API, SSE 번역, 되묻기 맥락, 알림 발송, `prescription_draft`.

---

## 1. 회차 1번 = 1행

`medication_dose` 는 시각 패턴이 아니라 **실제 먹일 회차 하나**가 한 행이다. 하루 세 번 × 3일이면 9행이다.
행마다 `checked_at` 이 있고, **이 체크 칸이 곧 복용 기록**이다. 누가 눌렀는지는 `checked_by` 에 같이 남는다. 따로 로그 테이블(`medication_dose_log`)을 두지 않는다.
날짜별 체크가 행에 그대로 남아서 며칠치 기록도 이 표 하나로 본다.

대가 — 행을 기간만큼 미리 만들어야 한다. 그래서 **`ends_on` 이 NOT NULL** 이고 무기한 코스는 없다.

## 2. 칸별 결정

### `medication_schedule`

| 칸 | 결정 | 이유 |
| --- | --- | --- |
| `status` | `active` / `stopped` 둘. **`completed` 없음** | 아래 §3 |
| `updated_at` | `stopped` 면 중단 시각 | 중단된 코스는 다시 고치지 않아서 이 값이 중단 시각으로 남는다 |
| `note` | 유지, nullable | 보호자 특이사항("물약이랑 알약을 같이" 등). 초안 카드에서 보호자가 적는 선택 칸이고 Agent 는 비워 둔다. 제출 본문에서 선택 칸으로 받는다 |
| `source_draft_id` | uuid, nullable, **FK · DEFAULT 없음** | 가리킬 `prescription_draft` 가 아직 없다. 처방전 경로를 만들 때 FK(`ON DELETE SET NULL`)를 단다 |
| `symptom_codes` | `text[] NOT NULL DEFAULT '{}'` | 비어 있음과 모름을 가를 일이 없다 |
| `storage` · `med_form` · `dosage` · `indication_text` | nullable | 초안 카드의 선택 칸 |

### `medication_dose`

| 칸 | 결정 | 이유 |
| --- | --- | --- |
| `dose_date` | date, NOT NULL | KST 날짜 |
| `scheduled_time` | `GENERATED ALWAYS ... STORED` | 기준 시각 + 오프셋, `fixed` 면 `fixed_time`. 쓰기 쪽이 계산하지 않는다 — ORM · raw SQL 모두 이 칸에 값을 넣으면 DB 가 거절한다(`GeneratedAlwaysError`, 확인함) |
| `checked_at` | timestamptz, nullable | NULL = 미체크. 체크 · 해제는 이 칸과 `checked_by` 만 바꾼다 |
| `checked_by` | uuid, nullable, `parent` FK `ON DELETE SET NULL` | 체크한 보호자(#749). 같은 아이의 다른 보호자도 누가 먹였는지 본다. 체크 API 가 로그인 보호자로 쓰고 해제하면 함께 비운다. **NULL 이어도 체크일 수 있다** — 이 칸이 생기기 전의 체크와 탈퇴한 보호자의 체크다. 탈퇴는 이 칸만 비우고 복용 기록은 남긴다(공동 기록의 "적은 사람" 과 같다). 연결 끊기는 지우지 않는다 |
| `created_at` · `updated_at` | **없음** | 제출 때 한꺼번에 만들어져 코스의 시각으로 알 수 있고, 바뀌는 건 체크뿐인데 그 시각은 `checked_at` 에, 누른 보호자는 `checked_by` 에 남는다 |

기준 시각: 기상 07:30 · 아침 08:00 · 점심 12:00 · 저녁 19:00 · 취침 20:30. 저녁은 #537 본문의 17:00 에서 19:00 으로 바꿨다 — 퇴근 시간대를 고려한 저녁 식사 시각(#546 코멘트, 이시하).

### `medication_notice_subscription` (#749)

보호자 한 명이 코스 하나의 복약 알림을 받는다는 행이다. **행이 있으면 받고, 끄면 지운다.** 일정 알림(`reminder`)과 같은 모양이다 —
코스와 회차는 공동 기록이지만 알림을 받을지는 보호자 개인 것이다.

| 칸 | 결정 | 이유 |
| --- | --- | --- |
| `schedule_id` | uuid, NOT NULL, 코스 FK `ON DELETE CASCADE` | 코스(아이 삭제 포함)가 지워지면 받을 알림도 없다 |
| `parent_id` | uuid, NOT NULL, `parent` FK `ON DELETE CASCADE` | 탈퇴하면 받을 사람이 없다 — 개인 설정이라 `checked_by` 와 달리 남기지 않는다 |
| `subscribed_at` | timestamptz, NOT NULL, **DEFAULT 없음** | 켠 시각. 이보다 예정 시각이 앞선 회차는 보내지 않는다 — 켜는 순간 이미 지난 회차가 울리지 않게. 앱이 서버 시계(`utc_now`)로 쓴다. 이미 켜져 있으면 처음 시각을 그대로 두고, 끄고 다시 켜면 새 행 · 새 시각이다 |
| 알림 시각 칸 | **없음** | 회차의 `scheduled_time` 에 울린다. 일정 알림의 `lead` 같은 선택지가 없다 |
| `on/off` 칸 | **없음** | 끄면 행을 지운다. 꺼진 행을 남기면 발송 쿼리와 정리 규칙이 둘이 된다 |

### enum

다른 표와 같이 `enum_col_py`(VARCHAR(32) + CHECK, `native_enum=False`)로 만든다. 이유는 `app/infra/db/types.py` docstring —
값을 지우는 명령이 없는 네이티브 ENUM 을 피하고, 값이 늘어도 CHECK 교체 한 번으로 끝낸다.
이슈 DDL 의 `varchar(16)` 대신 32 다(저장소 관례).

## 3. `completed` 를 저장하지 않는다 — 끝남은 조회 조건으로

`completed` 를 저장하면 매일 `ends_on` 이 지난 코스를 바꾸는 배치가 필요하다. 배치가 늦거나 실패하면 **끝난 약이 응급 카드에
'복용 중' 으로 찍힌다.** 그래서 끝남은 상태로 두지 않고, 읽을 때 계산한다.

- **진행 중 = `status = 'active' AND starts_on <= 오늘 <= ends_on`.** 이 조건은 `app/domains/medication/models.py` 의 `in_progress(today)` 한 곳에만 둔다.
  DB 어댑터(`app/domains/medication/agent_reader.py` — 응급 카드 · 투약의뢰서 · `get_next_dose`)와 BE 조회 API 가 같이 쓴다. 두 곳에 따로 쓰면 하나만 고쳐지는 날이 온다.
- `today` 는 인자로 받는다. DB 의 `CURRENT_DATE` 는 세션 타임존(UTC)이라 KST 자정 ~ 09시 사이에 하루 어긋난다.
- `ends_on == 오늘` 은 진행 중이다(그날 회차가 남아 있다).
- `starts_on` 이 내일 이후인 코스는 진행 중이 아니다 — 응급 카드 '복용 중인 약' 에 시작 전인 약이 실리지 않게. #544 의 in-memory `active_courses` 와 같은 정의다.
- `active` 는 "중단하지 않은 코스" 라는 뜻이라 `ends_on` 이 지나도 `active` 그대로다.
- 화면이 끝난 복약을 따로 보여 줘야 하면 응답에서 `ends_on < 오늘` 로 계산해 내려 준다(#537 코멘트, 이시하).

## 4. DB 가 막는 것

| 제약 | 막는 것 |
| --- | --- |
| `CHECK (ends_on >= starts_on)` | 끝이 시작보다 앞인 코스 |
| `CHECK ((timing_anchor = 'fixed') = (fixed_time IS NOT NULL))` | `fixed` 인데 시각이 없거나, 기준 시점인데 `fixed_time` 이 남은 행 — 생성 컬럼이 NULL 이 되거나 어느 시각이 맞는지 모호해지는 것 |
| `CHECK (timing_anchor <> 'fixed' OR offset_minutes = 0)` | `fixed` 에 붙은 오프셋 — 계산에 쓰이지 않는 값이 남아 화면과 어긋난다 |
| `CHECK (offset_minutes BETWEEN -120 AND 180)` | 알림 시각이 05:30 ~ 23:30 밖으로 나가는 것. **`time + interval` 은 자정을 넘으면 조용히 돌아간다**(20:30 + 240분 = 00:30). 이 범위면 넘지 않아서 `dose_date` 가 그 회차의 실제 날짜로 맞게 남는다 |
| `CHECK (checked_by IS NULL OR checked_at IS NOT NULL)` | 체크하지 않은 회차에 보호자만 남는 것. 반대(체크했는데 보호자 없음)는 위 `checked_by` 의 두 경우라 막지 않는다 |
| `UNIQUE (schedule_id, dose_date, scheduled_time)` | 같은 코스 같은 날 같은 시각 두 번. 기준이 달라도 시각이 같으면 막힌다(아침 +30 과 `fixed` 08:30) |
| enum CHECK | 목록 밖 값, 대소문자 변형('Active'), `completed` |
| FK CASCADE | 아이를 지우면 코스와 회차가, 코스를 지우면 회차가 함께 지워진다(보관 기준 [`safety/retention-v1.md`](../safety/retention-v1.md) "아이에 묶인 기록은 아이와 함께") |
| FK SET NULL (`checked_by`) | 체크한 보호자가 탈퇴해도 복용 기록이 지워지는 것 |
| `UNIQUE (schedule_id, parent_id)` (구독) | 보호자 한 명이 한 코스를 두 번 구독하는 것 — 켜기가 겹쳐도 한 행(`ON CONFLICT DO NOTHING`) |
| FK CASCADE (구독) | 코스 · 아이가 지워지거나 보호자가 탈퇴했는데 구독이 남는 것 |

`scheduled_time` 은 이슈 DDL 대로 NOT NULL 을 달지 않았다. NULL 이 되는 입력은 위 CHECK 와 NOT NULL(`timing_anchor` · `offset_minutes`)이 전부 막는다
— UPDATE 로 `fixed_time` 만 지우거나 `timing_anchor` 만 바꾸는 경우도 거절되는 것을 확인했다.

### 인덱스

코스 · 회차의 조회 패턴은 둘이고 둘 다 기존 인덱스가 받는다.

- 아이의 진행 중 코스 — `(child_id, status)`. `ends_on` 은 아이 한 명의 코스 몇 개 안에서 거른다.
- 코스의 회차(오늘 회차 n/m · 체크 · 수정 시 재생성) — UNIQUE 의 `(schedule_id, dose_date, scheduled_time)` 앞부분.

`medication_dose_checked_by_idx`(`checked_by`)는 조회용이 아니다. 탈퇴 때 SET NULL 이 그 보호자가 체크한 회차를 찾는다.

구독은 UNIQUE 의 `(schedule_id, parent_id)` 가 코스별 조회(응답의 `notice_enabled` · 발송)를 받고,
`ix_medication_notice_subscription_parent_id` 가 연결 끊기 · 탈퇴 때 그 보호자의 구독을 찾는다.

발송용 인덱스(`scheduled_time` 등)는 알림 발송이 범위 밖이라 두지 않는다. 발송을 만들 때 그 쿼리를 보고 단다.

## 5. DB 가 못 막는 것 — API · 서비스가 막는다

- **`title` 빈 문자열.** NOT NULL 은 `''` 를 통과시킨다.
- **`dose_date ∈ [starts_on, ends_on]`.** 다른 표의 칸이라 CHECK 로 못 건다.
- **아직 오지 않은 날의 회차 체크.** 체크 API 가 KST 오늘과 비교해 막는다.
- **체크한 보호자는 서버가 정한다** (#749). 로그인 보호자로 쓰고 요청 본문으로 받지 않는다. 이미 체크된 회차에 또 체크해도
  처음 시각과 처음 보호자가 남는다(`checked_at IS NULL` 일 때만 쓴다).
  - 🚨 **보호자 행을 회차보다 먼저 잡는다**(`FOR KEY SHARE`). 탈퇴는 보호자를 잠근 뒤 회차를 지우거나(등록한 아이) 비운다(SET NULL).
    회차를 먼저 잡고 `checked_by` 를 쓰면 FK 검사가 보호자를 기다려 순서가 뒤집히고, 같은 보호자의 탈퇴와 체크가 겹칠 때 교착이 난다.
    그사이 탈퇴했으면 401 이다.
  - 응답의 `checked_by` 는 `{parent_id, nickname}` 이고, 이름을 알 수 없으면(위 NULL 두 경우 · 이름 없는 계정) 통째로 null 이다.
- **수정 때 회차 재생성 규칙** (event-draft-flow §4-3 `is_prepared` 와 같은 선례 — 보호자가 남긴 표시는 초안이 덮지 않는다):
  - 체크된 회차(`checked_at IS NOT NULL`)는 **절대 지우지 않는다.**
  - 지우고 다시 만드는 건 **미체크 · 아직 오지 않은 회차만.**
  - 다시 만든 회차가 남겨 둔 행과 같은 날 같은 시각이면 **건너뛴다**(UNIQUE 위반으로 트랜잭션 전체가 죽지 않게).
  - `starts_on` 을 과거로 당겨도 **지난 날짜의 회차는 만들지 않는다.**
- **중단 UPDATE 는 `updated_at = now()` 를 직접 쓴다.** `Timestamps` 의 `onupdate` 는 ORM update 때만 채워진다(`EventItem` docstring 과 같은 함정).
- `stopped` 코스 수정 거절.
- **알림 구독** (#749):
  - **등록한 보호자는 등록할 때 함께 구독한다**(켠 시각 = 등록 시각). 함께 보는 다른 보호자는 꺼진 채로 시작해 각자 켠다.
    코스를 수정해도 구독은 그대로다.
  - **이 표가 생기기 전에 등록된 코스는 채워 넣지 않는다**(backfill 없음). `created_by` 는 `agent`/`caregiver` 구분뿐이라 누가
    등록했는지 몰라서, 그 코스는 모두 꺼진 채로 두고 필요한 보호자가 직접 켠다(#749 이슈 결정).
  - **시작 전 코스도 켤 수 있지만** 조회가 시작 전 코스를 싣지 않아서 화면에서는 첫날부터 켜고 끌 수 있다(알려진 한계,
    보호자 모두 같다). 그때 이미 지난 회차는 울리지 않는다.
  - **켤 수 있는 코스**는 멈추지 않았고 마지막 날이 지나지 않은 것이다(시작 전 포함). 끄기는 어느 코스든 된다.
    켜기만 건강정보 동의를 확인한다 — 그만 받는 것은 언제나 할 수 있어야 한다.
  - **연결 끊기는 DB 가 지우지 않아서 `unlink_parent` 가 지운다** — 그 보호자의 이 아이 코스 구독만.
  - 🚨 **등록 · 켜기는 보호자 → 아이 → 연결을 먼저 잡는다**(`lock_link`, 일정 알림과 같다). 연결 끊기가 겹치면 끊긴 보호자의
    구독이 남는다 — 잡아 두면 끊기가 이 커밋을 기다렸다가 구독까지 지운다. 등록에서는 아이별 등록 잠금보다 앞이다
    (탈퇴가 보호자 → 아이 순으로 잡는다). 그사이 끊겼으면 403 이다.
  - 🚨 **멈추기 · 끝나기는 구독을 지우지 않는다.** 멈춘 코스 · 마지막 날이 지난 코스에도 구독 행이 남을 수 있다.
    발송(아직 없음)이 `status = 'active'` · `ends_on` · `subscribed_at` 으로 걸러야 한다.

### 시간대 — 표를 쓰는 코드가 지킬 것

DB 세션은 UTC 다(로컬 · 배포 db 컨테이너 모두 TZ 없음). API 컨테이너만 `TZ=Asia/Seoul` 이라
같은 코드가 환경마다 다르게 도는 자리가 생긴다. 아래 셋은 실제 DB 에서 확인했다(`test_medication_tables.py` §6).

- **"오늘"은 `now.astimezone(KST).date()` 로 구해 `in_progress(today)` 에 넘긴다.** UTC 날짜를 넘기면
  KST 자정 ~ 09:00 사이에 어제 끝난 약이 '복용 중' 으로 잡힌다.
- **SQL 에서 체크한 날은 `(checked_at AT TIME ZONE 'Asia/Seoul')::date`.** `checked_at::date` 는 세션(UTC)
  날짜라 KST 오전 9시 전 체크가 하루 앞 날짜가 된다.
- **`checked_at` 에는 시간대가 붙은 값(`datetime.now(KST)`)이나 DB `now()` 만 쓴다.** 시간대 없는
  `datetime` 은 asyncpg 가 프로세스 TZ 로 해석해서, 같은 07:00 이 `TZ=Asia/Seoul` 에서는 KST 07:00,
  `TZ=UTC`(CI 등)에서는 KST 16:00 으로 저장된다(로컬에서 두 TZ 로 실행해 확인).

## 6. 기준 시각이 두 곳에 있다

생성 컬럼의 기준 시각과 초안 계약(`app/core/medication_draft.py`, #544)의 `ANCHOR_TIMES` · `scheduled_time_of` 는 같은 값이어야 한다.
어긋나면 초안 카드가 보여 준 시각과 실제 행의 시각이 달라진다.

- 이 PR 은 #544 브랜치 위에 있어 core 상수를 바로 import 한다. `test_생성_컬럼은_초안_계약의_시각_계산과_같다` 가
  `ANCHOR_TIMES` 의 기준 시점 다섯 개 × 오프셋 `OFFSET_MIN`~`OFFSET_MAX`(1,505 조합)를 실제 행으로 넣어 생성 컬럼과
  `scheduled_time_of` 를 대조한다. 기준 시점 이름과 오프셋 범위가 DB 와 같은지도 따로 본다.
  (저녁을 19:00 으로 바꾸기 전 DB 가 17:00 이던 상태에서 이 테스트가 깨지는 것을 확인했다.)
- 기준 시각을 바꾸려면 STORED 생성 컬럼이라 **표를 다시 쓰는 마이그레이션**이 필요하다. autogenerate 는 생성 식 변경을 잡지 못한다.

## 7. 정본 차이 — `docs/agents/health/health_agent_own_table.md`

명세는 AI 소유라 여기서 고치지 않는다. #544 의 §7 DDL 과 이 표가 다른 곳:

| 명세 §7 (#544) | 이 표 | 근거 |
| --- | --- | --- |
| `source_draft_id REFERENCES prescription_draft` | FK 없음 | §2 — 가리킬 표가 아직 없다 |
| enum `varchar(16)` | `varchar(32)` | §2 enum |
