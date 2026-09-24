# Agent 문서 — 먼저 읽을 것

도메인 Agent 넷(Food · Activity · Growth · Health)과 Memory, Supervisor의 설계 문서가 여기 있다.
Activity를 맡았다면 이 문서부터 읽고 [shared/](shared/)로 넘어가면 된다.

```
shared/     네 Agent가 공유하는 규약. Activity도 여기를 따른다
food/       Food — 이시하
growth/     Growth — 이시하
health/     Health — 이시하
activity/   Activity — 이도헌
data_model.md   DB 스키마 정본
memory-agent-v1.md
```

---

## 1. 읽는 순서

한 시간 정도 걸린다. 순서를 지키는 게 낫다. 뒤 문서가 앞 문서를 전제한다.

| # | 문서 | 무엇 |
| --- | --- | --- |
| 1 | [shared/Agent_공통규약.md](shared/Agent_공통규약.md) | 쓰기 권한 · 출력 채널 · 근거 · 게이팅 · 호출 예산 · 금지 사항 |
| 2 | [shared/Tool_공통.md](shared/Tool_공통.md) | `LifeStage` · `Gate` · `rank_evidence` 정본 |
| 3 | [shared/연령별_Tool_전략.md](shared/연령별_Tool_전략.md) | 월령 경계표 · 교정연령 · 경계 처리 |
| 4 | [supervisor-agent-v1.md](supervisor-agent-v1.md) §2, §3-1 | 2차 라벨, Activity ↔ Growth 경계. Activity 역할은 아래 §2 에 있다 |
| 5 | [data_model.md](data_model.md) | `observation_activity` · `profile_affinity` · `suggestion` |
| 6 | [shared/RAG_plan.md](shared/RAG_plan.md) · [shared/외부연결_계획.md](shared/외부연결_계획.md) §3 | 문서 행 제작 절차 · 날씨·장소 API |

다른 Agent가 어떻게 생겼는지 보고 싶으면 [growth/](growth/)가 가장 가깝다. Activity와 구조가 비슷하고 쓰기가 없다.

---

## 2. Activity 몫으로 정해진 것 [ACTIVITY 담당자가 수정 가능]

공통 결정이라 그대로 가져가면 된다. 이의가 있으면 고칠 수 있다.

**라벨이 없다.** 놀이 추천 하나다. 실내/야외는 날씨·시간 조회 결과로 갈리므로 런타임 판단이지 라벨이 아니다.

**추천은 정확히 3개다.** 2개 이하나 4개 이상이면 출력 tool이 거절한다. 안전 필터 뒤에 3개 미만이 되면 그 Agent만 모델을 한 번 더 부르고, 걸러진 항목을 제외 목록으로 넣는다. 재호출은 1회로 끝나고, 그래도 못 채우면 남은 만큼만 낸다.

**기피(polarity −1)는 제외 필터가 아니라 근거다.** "미끄럼틀을 싫어해서 이걸 골랐어요"가 되어야 한다. 후보에서 지워버리면 무엇을 피해 골랐는지 화면에 말할 수 없다. 기피 근거를 인용했으면 `reason`에 무엇을 피했는지 쓴다. 후보 풀에서 지우는 것은 안전뿐이다 — `hazard_term`에 걸리는 것.

**다만 기피 대상 자체가 후보로 나오면 출력에서 거절한다.** 근거로 쓰는 것과 그 활동을 다시 내지 않는 것은 별개다. Food 는 영양 때문에 기피 식품을 내야 할 때가 있지만 Activity 에는 그런 대항 요인이 없고, 모델에게 기피 근거를 주고 안 내기를 기대하는 것은 프롬프트에 맡기는 것이라 보장이 아니다. 기피 근거와 `merge_key` 가 같은 후보는 출력 tool 이 거절한다 — 풀에서 지우는 제거 필터가 아니라 출력 검증이라 위 문장과 부딪히지 않는다. 자리는 `Activity_Tool_명세.md` 의 출력 검증이고, 공통 규약에는 [shared/Tool_공통.md](shared/Tool_공통.md) §5-5 가 원칙("후보를 지우는 공통 규칙은 안전뿐")과 Food·Activity 대비를 적어 두었다.

**승인된 추천은 `observation_activity`로 간다.** 보호자가 승인하면 Memory Agent가 `suggestion`을 관찰로 재구조화해 저장하고, feedback(`liked`/`disliked`/`not_acted`)이 그 관찰의 `polarity`(+1/−1/0)를 갱신한다. `not_acted`는 "안 했다"가 아니라 "반응이 딱히 없음"이다.

**근거는 `suggestion_evidence` 테이블에 행으로 쌓는다.** `PK(suggestion_id, source_kind, source_id)`. `source_kind`가 아이 기록(`observation_*`·`profile_affinity` 등)이면 개인화 근거로 세고, 문서 행(`activity_doc`)이면 참고로만 센다. `kind='personalized'`인데 아이 기록 행이 0이면 버그다.

**`activity_doc`은 테이블이다.** Food·Growth와 맞춘다. 시드는 YAML로 버전 관리하고 Alembic이 `doc_key` 기준 upsert한다. lint·교차 검수 절차는 [shared/RAG_plan.md](shared/RAG_plan.md) §2에 있다. few-shot을 코드 안 YAML로 두기로 했던 R-1은 이 결정으로 닫힌다.

**`hazard_term`은 Activity 소유다.** Growth도 읽는다 — `growth_doc` 적재 시점에 한 번, 활동 후보 출력 사후에 한 번. 구현을 두 벌 두지 않는다. Growth가 필요로 하는 것은 아래 §5에 적었다.

### Activity Agent의 역할

아이의 현재 관심과 실제 놀이 반응을 바탕으로, **지금 실행하기 좋은** 놀이·외출 활동을 추천한다.

하는 일

- 집/실내/야외 놀이 추천
- `candidate` 관심을 확인해볼 가벼운 탐색 활동
- `confirmed` 관심을 더 깊게 즐길 활동
- 최근 했던 놀이와 중복되는 추천 제거 (코드)
- 날씨·시간·거리·가족 일정 반영

연결할 것 — `observation_activity` · `profile_affinity(domain=activity)` · Child Profile · Calendar · Weather · 위치 기반 장소 정보

하지 않는 일

- 한 번 즐긴 놀이를 장기 취향으로 확정
- 외부 리뷰만 보고 최적 장소라고 단정
- 보호자 승인 없이 예약·결제 — 경로 자체를 만들지 않는다
- `observation_education` · `observation_routine` 읽기

Growth 와의 경계는 [supervisor-agent-v1.md](supervisor-agent-v1.md) §3-1 에 있다. 읽기 포트가 비대칭이다 — Growth 는 놀이 기록을 읽지만 Activity 는 학습·루틴 기록을 읽지 않는다.

---

## 3. 채워야 할 빈칸

Activity 것이라 비워 뒀다.

**근거 소비 규칙 두 칸.** [shared/Agent_공통규약.md](shared/Agent_공통규약.md) §4 표에 Activity 칸이 있다. 지금은 `candidate` affinity를 안 쓰고 최근 14일 관찰도 안 쓰는 것으로 되어 있다. 그대로 갈지 정해 달라. 관찰을 안 쓰면 id도 반환하지 않는다.

**`hazard_term` 스키마.** 지금 어느 문서에도 필드가 없다. Growth가 이미 읽고 있어서 이게 먼저 필요하다.

**`filter_activity_safety`.** 이름만 코드 tool 목록에 있고 규칙이 없다. Food의 `filter_food_safety`([food/Food_Tool_명세.md](food/Food_Tool_명세.md) §3)가 같은 자리의 선례다.

**쓰기 테이블이 있는지.** Food는 `daycare_meal`을 고치고 Health는 `medication_*`을 쓴다. Growth는 쓰기가 없다. Activity가 쓰기를 갖는다면 공통규약 §2 표와 테스트 규약(§13 "쓰기 tool 없음")을 같이 고쳐야 하니 정해지면 알려 달라.

**날씨·장소 포트.** [shared/외부연결_계획.md](shared/외부연결_계획.md) §3에 소스와 실패 처리가 적혀 있다. 어댑터는 `app/integrations/`에 BE가 만들고 Agent는 포트만 받는다.

---

## 4. 나이 계산 — 여기서 제일 많이 틀린다

공통이 강제하는 것은 **월령 계산까지**다. 구현은 `app/rules/age.py` 하나이고 네 Agent가 같은 함수를 쓴다.

```python
def life_stage(birth_date: date, today: date, gestational_weeks: int | None = None) -> LifeStage
# LifeStage(months, corrected_months, stage, big)
```

| `stage` | 월령 |
| --- | --- |
| `infant_milk` | 0–3 |
| `infant_weaning` | 4–11 |
| `toddler` | 12–35 |
| `preschool` | 36+ |

**`stage`는 참고값이지 공통 게이팅 축이 아니다.** 그 값으로 tool을 어떻게 가를지는 도메인마다 다르다.

| | 가르는 방식 |
| --- | --- |
| Food | `stage`를 배타적 범주로. 수유기와 유아식기는 먹을 수 있는 것이 질적으로 달라 tool이 겹치지 않는다 |
| Growth | tool별 `min_month` 눈금. 열리는 시점만 다르고 배타적인 tool이 없다 |
| Health | 가르지 않는다. 연령이 여는 tool이 없고 검진 차수·접종 시기라는 계산 방식만 바뀐다 |
| Activity | 미정. 날씨·위치가 더 큰 축일 수도 있다 |

아래는 방식과 무관하게 지켜야 한다. 안 지키면 게이트가 어긋난다.

- **민법 기준 달력 계산이다.** `(today - birth).days // 30`을 쓰면 6년이면 두 달 앞서 열린다. 말일 경계는 민법 §160③을 따른다 — 1월 31일생은 평년 2월 28일에 1개월이 된다.
- **`date` 자리에 `datetime`이 들어가지 못하게 타입으로 막는다.** UTC 서버에서 `date.today()`를 부르면 KST 00:00–09:00 사이에 하루가 어긋나 그 시간대에만 게이트가 안 열린다. 기준일은 `Gate`가 들고 있는 `today`를 쓰고 tool 안에서 직접 부르지 않는다.
- **생일 당일 전환이다.** 12개월이 되는 날 유아식 tool이 열린다.
- **경계값은 `config/age_gates.yaml` 한 곳에 둔다.** 코드·프롬프트·문서에 숫자를 복제하지 않는다.

조산(`gestational_weeks < 37`)이면 24개월까지 `corrected_months`를 계산한다. 월 단위 반올림이다 — `months - round((40 - gestational_weeks) / 4.35)`. 34주면 1개월 보정. Activity가 활동 난이도에 교정연령을 쓸지는 정해진 게 없다. **안전 필터만은 `min(age_months, corrected_months)`를 쓴다** — 더 어린 쪽이 보수적이다.

테스트는 경계마다 양쪽을 본다. 필수 월령은 3/4 · 11/12 · 17/18 · 23/24 · 35/36 · 47/48 · 71/72다. Activity에 걸리는 건 17/18(affinity 생성 시작)과 35/36(안전 기준 전환)이다.

---

## 5. Growth가 `hazard_term`에 요구하는 것

Growth는 스키마를 정하지 않는다. 필요한 것만 적는다.

- **용어 하나에 최소 월령.** 활동 행의 `min_month`와 충돌하는지 판정한다. "구슬"이 36개월 미만 행에 있으면 적재를 거부한다.
- **`guards`** — 오탐 취소 목록. Food의 `allergen_term.guards`가 같은 패턴이다("밀크티"는 밀이 아니다).
- **출처.** 행마다 필요하다. 어린이제품 공통안전기준 · 행안부 놀이시설 고시 같은 근거가 붙어야 한다.
- **조회 실패는 예외로 올린다.** 빈 목록으로 폴백하면 위험한 활동이 그대로 나간다. Growth는 예외를 받으면 활동 추천을 닫는다.

쓰는 시점은 두 곳이다. `growth_doc` 적재(배치)와 `propose_learning_activity` 출력 사후(런타임).

매칭은 공통 매처 `app/rules/term_match.py`를 쓴다 — 공백 제거 부분 일치 + guards + 가장 엄한 판정 우선. Food의 알레르기 필터와 같은 구현이다.

---

## 6. 디렉토리 구성

```
app/agents/activity/
├── agent.py       run(task, context, *, client=None) -> DomainAgentResult
├── context.py     ActivityContext — 아이 id · now · timezone · Gate · 읽기 포트
├── prompt.py      구획별 system prompt
├── registry.py    TOOL_HANDLERS · CODE_TOOLS · tools_for(task_type, gate) · execute_tool(allowed=)
├── result.py      ToolResult · ErrorCode
├── schemas/       common.py · task.py · <기능별>.py · tool_defs.py
├── store/ports.py 읽기 포트 Protocol (구현체는 app/api가 주입)
└── tools/         tool 하나당 함수 하나
```

밖으로 열린 함수는 `run` 하나다. Agent는 DB를 모르고 읽기 포트와 writer를 주입받는다. 다른 Agent 패키지를 import하지 않는다 — 남의 테이블이 필요하면 포트 주입이다.

의존 방향은 `activity → common`만이다. `supervisor`는 어휘만 import하고 `pipeline`이 전부를 부른다.

공유 코드는 세 곳에 있다.

| 경로 | 무엇 |
| --- | --- |
| `app/rules/age.py` | `life_stage()` · 달력 계산 |
| `app/rules/term_match.py` | 위험 용어·알레르기 매처 |
| `common/tool_runtime.py` | `ToolResult` · `ErrorCode`. `execute_tool` 은 각 Agent 의 `registry.py` 가 갖는다 |
| `common/evidence.py` | `rank_evidence()` |
| `common/suggestion.py` · `common/readout.py` | `SuggestionDraft` · `Readout` |

`ToolResult`와 `ErrorCode`는 [shared/Tool_공통.md](shared/Tool_공통.md) §1의 어휘 하나로 통일돼 있다. Agent 전용 코드가 필요하면 `common` 것을 상속해서 얹는다 — `memory/result.py`가 선례다.

---

## 7. 문서는 네 장

다른 Agent가 이 구성이다. 같은 이름을 쓰면 서로 찾기 쉽다.

| 파일 | 무엇 | 읽는 사람 |
| --- | --- | --- |
| `Activity_Agent_명세.md` | 받는 요청 · 하는 일/안 하는 일 · 읽는 것/쓰는 것 · Tool 목록 · 화면에 나가는 것 | 기획·디자인·FE·BE·QA |
| `Activity_Tool_명세.md` | 게이팅 표 · 모델 tool · 코드 tool · 출력 상수 | AI·BE |
| `activity_agent_own_table.md` | 소유 테이블 필드 · DDL · 권한 | BE |
| `activity_plan.md` | 단계별 구현 순서 · 선행 조건 · 테스트 · 롤아웃 | 본인 |

테스트는 `test_input_activity.txt`(입력 40건 남짓)와 `test_expected_activity.md`(입력별 기대·실패 조건)로 둔다. [growth/test_expected_growth.md](growth/test_expected_growth.md)가 형식 예시다.

---

## 8. 넘기기 전에 알아둘 것

문구를 코드 상수로 관리한다. 판정·안내·경고·미지원 문구와 일반 추천 이유는 전부 상수이고, 테스트가 글자 단위로 비교한다. 모델이 쓰는 것은 개인화 이유와 서술뿐이다.

로그에는 `{kind, id}`만 남긴다. 발화 원문·장소명·활동명을 남기지 않는다. 월령 대신 밴드를 남긴다. 좌표는 요청 바디로만 받고 즉시 격자로 뭉갠다 — DB·로그·모델 입력·예외 메시지 어디에도 원좌표가 없어야 한다.

모델 호출은 run당 최대 5회, 도메인 Agent당 1회 이내다. 재호출 예외만 2회다. 게이트가 닫혔으면 모델을 부르지 않고 코드 readout으로 끝낸다.

실패를 기본값으로 메우지 않는다. 날씨 조회 실패는 "맑음"이 아니다. 날씨가 실패하면 실내만, 미세먼지만 실패하면 야외를 허용하되 고지하고, 장소가 실패하면 장소가 필요 없는 활동만 낸다. 위치 권한이 없으면 야외 tool이 닫힌다.

[shared/Agent_공통규약.md](shared/Agent_공통규약.md) §8에 전 Agent 금지 사항 열 가지가 있다. Activity에 특히 걸리는 것은 예약·결제 경로를 만들지 않는 것과, 조회 결과에 없는 장소명을 생성하지 않는 것이다.
