# Curator 실험 데이터

모두 가상 데이터다. 실제 사용자 발화나 아이 정보를 넣지 않는다 (루트 CLAUDE.md §9).

## 이름 규칙

`역할_종류.txt`

- 역할
  - `tune` — 방식을 고를 때 쓴다
  - `check1` · `check2` — 결과를 보기 전에 정답을 확정해 둔 확인용
- 종류
  - `pairs` — 이름 쌍
  - `orders` — 관찰을 여러 순서로 넣는 시나리오
  - `scale` — Profile 이 여러 개 쌓인 상태에서 새 관찰 하나
- `disputed_pairs` — 판단이 갈리는 쌍. 점수에 넣지 않는다

**한 번 결과를 본 확인용 데이터는 그 뒤로 방식을 고를 때만 쓴다.** 새 확인이 필요하면 `check3` 을 만든다.

## 어느 실험에 썼나

| 파일 | 실험 1 · 2 | 실험 3 | 실험 4 | 실험 5a | 실험 5b |
|---|---|---|---|---|---|
| `tune_pairs.txt` | 고르기 | 고르기 | 고르기 | 고르기 | |
| `check1_pairs.txt` | 확인 | 확인 | | | |
| `check1_orders.txt` | | 확인 | | 고르기 | |
| `disputed_pairs.txt` | 참고 | 참고 | | | |
| `tune_scale.txt` | | | | 고르기 | |
| `check2_pairs.txt` | | | | | 확인 |
| `check2_orders.txt` | | | | | 확인 |

파일 규칙(정답 값, 확인용과 다른 파일의 이름 겹침 등)은 `../test_pairs_data.py` 가 확인한다.
