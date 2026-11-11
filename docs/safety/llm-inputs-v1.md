# 외부 LLM 으로 나가는 아이 정보 v1

문서 목적: Agent 가 외부 LLM 에 보내는 글에 아이의 알레르기 · 건강 정보가 **무엇이, 어디서** 들어가는지 고정한다.
루트 `CLAUDE.md` §10 의 "외부 LLM 전달 범위" 미정 항목을 코드로 확인해 닫은 기록이다.

확인일: 2026-11-10 (develop `97307162` 기준)
받는 회사 · 국외 이전은 개인정보 처리방침에 있다. 이 문서는 **코드가 실제로 무엇을 싣는지**만 적는다.

---

## 1. 결론

- 기획의 "공통 컨텍스트 강제 주입"(프로필 · 알레르기 · 급식 · 일정을 모든 Agent 에 미리 넣기)은 **만들지 않았다.**
  Agent 마다 자기 조회 통로(reader)를 따로 받는다 (`app/agents/entrypoint.py` 의 `_domain_contexts`).
- 알레르기 · 건강 정보(`health_safety`)의 **이름 · 반응 · 대처는 어느 Agent 의 프롬프트에도 싣지 않는다.**
  읽은 값은 코드 필터와, 코드가 만드는 화면 문구(readout)에만 쓴다.
- 그래서 "그 Agent 의 일과 무관한 건강정보가 모델로 나간다" 는 걱정은 지금 코드에 해당하지 않는다.

## 2. Agent 별로 모델에 나가는 것

| Agent | 읽는 안전 정보 | 모델로 나가는 건강 · 알레르기 항목 | 근거 |
| --- | --- | --- | --- |
| Food | active 전부 | 알레르기 이름은 없다. 급식 조회 결과에 메뉴마다 판정 `ok` / `blocked` / `unchecked` 가 붙는다 | `agents/food/tools/menu.py` 의 `meals[].menus[].allergy` |
| Activity | 알레르기 · 환경 요인만 | 없다. 월령(개월 수)만 | `agents/activity/prompt.py` 의 `[월령]` |
| Growth | 알레르기 · 환경 요인만 | 없다. 월령만. 키 · 몸무게 변화는 코드가 계산해 문장으로 만들고 모델을 부르지 않는다 | `agents/growth/prompt.py` 의 `[월령]` · `agents/growth/agent.py` 의 `compute_growth_delta` |
| Health | active 전부 | 복약을 고칠 때만 진행 중인 약의 이름 · 용량 · 알림 시각 · 기간 | `agents/health/tools/doses.py` 의 `search_medication_schedules` |

- Agent 마다 읽는 범위가 다른 것은 `domains/safety/agent_reader.py` 의 `ACTIVITY_KINDS` · `GROWTH_KINDS` 가 정한다.
- 이름 · 별명 · 생일 원문 · 성별은 도메인 프롬프트에 없다. `child_id` 도 싣지 않는다.
- Food 프롬프트의 "아이 기록 후보"(`memory_candidates`)는 음식 선호 기록의 정해진 칸(종류 · id · 라벨 · 좋고 싫음 · 날짜)이고,
  안전 필터를 거친 뒤의 것이다. 원문 · 양 표현은 싣지 않는다.

🚨 **Food 의 메뉴별 판정은 간접 신호다.** 이름은 보내지 않지만 "우유: blocked" 로 무엇에 알레르기가 있는지 짐작할 수 있다.
음식을 다루는 Agent 가 음식 판정을 받는 것이라 "무관한 Agent" 문제는 아니다. 판정은 코드가 이미 끝냈으므로,
이 칸을 모델에 보내지 않아도 되는지는 따로 볼 수 있다.

## 3. Supervisor · Memory

- 둘 다 알레르기 · 건강 정보를 미리 넣지 않는다. 보호자가 방금 적은 글은 간다.
- Memory 는 모델이 조회 tool 을 부르면 저장된 관찰을 돌려준다 (`agents/memory/tools/observation.py`). 건강 관찰도 포함된다.

## 4. 확인하지 못한 것

- 모델 호출을 감싸는 `LLMClient` 안쪽과 `pipeline.py` 전체에서 메시지를 더하는 다른 길이 없다는 것은 끝까지 따라가지 않았다.
- Memory 조회 결과(`to_summary`)에 관찰 원문(`raw_text`)이 그대로 실리는지는 칸 단위로 확인하지 않았다.
- 테스트로 고정한 것이 아니다. 프롬프트 · tool 결과를 바꾸면 이 표도 다시 본다.
