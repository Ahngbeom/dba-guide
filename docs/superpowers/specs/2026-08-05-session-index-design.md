# 설계: `sessions` 단계의 세션별 치환자 `{{session_index}}`

- 날짜: 2026-08-05
- 대상: `scripts/shooting.py`, `shooting/stages/pg-1-1-idle-in-transaction.json`, `docs/shooting-game.md`
- 발단: 통합 검토 3번 — pg-1-1의 피해자 세션이 `random()`으로 행을 골라 서로 충돌한다

## 문제

`pg-1-1-idle-in-transaction`의 피해자 세션은 각자 행을 무작위로 고른다.

```sql
UPDATE orders SET status = 'PAID' WHERE id = 1 + floor(random() * {{rows}})::int
```

같은 파일의 `_comment`는 **세션마다 다른 행을 골라야 한다**고 못 박고, 겹치면
"도구가 지목한 세션을 끊었다가 `kill_precision`에 걸리는 부당한 함정"이 된다고
적어두었다. 그런데 `random()`은 그 조건을 보장하지 않는다. 선언된 파라미터 범위
(`rows` 30~80, `payments` 3~6)에서 충돌 확률은 다음과 같다.

| rows | payments | 충돌 확률 |
|---|---|---|
| 30 | 6 | **41.4%** |
| 50 | 6 | 26.8% |
| 80 | 3 | 3.7% |

최악 조합에서 열 판 중 네 판이 스테이지가 스스로 부당하다고 선언한 상태로 시작한다.

## 실측

로컬 랩(`./shoot up --with-postgresql`, postgres:16)에서 엔진의 실제 경로
(`db_spawn`/`db_query`)로 두 상황을 재현했다.

### 사례 A — 피해자 4개가 전부 같은 행(id=7)

```
    pid  state                 wait    blocked_by
    140  idle in transaction   Client  -              ← 범인
    153  active                Lock    140
    162  active                Lock    153
    169  active                Lock    153,162
    175  active                Lock    153,162,169
```

### 사례 B — 피해자 4개가 서로 다른 행(id=11..14)

```
    pid  state                 wait    blocked_by
    248  idle in transaction   Client  -              ← 범인
    255  active                Lock    248
    262  active                Lock    248
    269  active                Lock    248
    276  active                Lock    248
```

**사례 A에서는 막힌 네 줄 중 범인이 등장하는 것이 한 줄뿐이다.** pid 175의
blockers에는 범인이 아예 없다. 스테이지가 권장하는 진단 질의를 그대로 쳤을 때
범인 pid는 1회, 피해자 pid는 3회 나타나 신호 대 잡음이 뒤집힌다 — "사슬의 뿌리를
끊어라"라는 이 스테이지의 교훈이 정확히 반대를 가리킨다.

원인은 PostgreSQL이 같은 튜플을 노리는 대기자를 **tuple lock으로 직렬화**하기
때문이다. 두 번째 이후 대기자는 범인의 xid가 아니라 앞선 대기자가 쥔 tuple lock을
기다리고, `pg_blocking_pids`는 그것을 정확히 보고한다. MySQL 1-3이 피해자 전원을
같은 행(`WHERE id = {{victim}}`)에 몰아넣고도 멀쩡한 이유는 InnoDB가 대기자를 같은
레코드 락 큐에 붙이고 blocker로 **락 보유자**를 보고하기 때문이다. 벤더가 실제로
갈리는 지점이다.

## 진짜 원인

`sessions` 단계는 `count`개의 세션에 **동일한 SQL 문자열**을 던진다. 세션별로
다르게 만들 수단이 엔진에 없다. `sessions`를 쓰는 스테이지 다섯 개를 확인했다.

| 스테이지 | 세션별 차이가 필요한가 |
|---|---|
| 1-3 lock-contention | 아니오 — 같은 행에 몰아넣는 것이 의도 |
| 3-1 connection-exhaustion | 아니오 (`SELECT 1`) |
| 4-1 missing-index | 아니오 (같은 프로시저 호출) |
| 4-3 implicit-conversion | 아니오 (같은 프로시저 호출) |
| pg-1-1 idle-in-transaction | **예** — 그래서 SQL 안의 `random()`으로 때웠다 |

즉 스테이지의 실수라기보다 엔진에 빠진 원시 기능이 드러난 것이다. "각자 다른
대상을 잡는 세션 N개"는 잠금 경합을 재현하는 흔한 모양이다.

## 설계

### 치환자

```
{{session_index}}   0-기반. sessions 단계가 i번째 세션을 띄울 때 그 숫자로 치환된다.
```

이름을 길게 잡은 이유는 스테이지의 `vars` 이름과 충돌하지 않기 위해서다
(`{{i}}`는 위험하다). 0-기반인 것은 `range(count)`와 맞추기 위해서이고, 호출부가
`1 + {{session_index}}`처럼 적게 되므로 기반이 눈에 보인다.

### 렌더링 시점

`render_stage`는 로드 시점에 `vars`만 치환하고 모르는 자리는 그대로 통과시킨다
(`_render_value`의 `values.get(name, 원문)`). 따라서 `{{session_index}}`는 손대지
않은 채 `setup_stage`까지 살아남고, 세션을 띄우는 루프가 자기 번호로 바꾼다.

```python
def render_session_sql(sql, index):      # 순수 함수
    """`sessions` 단계 SQL의 {{session_index}}를 세션 번호로 바꾼다."""
    return _render_value(sql, {SESSION_INDEX_VAR: index})

# setup_stage 안
for i in range(count):
    db_spawn(target, user, password, render_session_sql(sql, i), idle)
```

### 검증

`_validate_vars`에 두 규칙을 더한다. 둘 다 이 저장소가 반복해서 경계해온
"조용히 썩는 자리"다.

1. `{{session_index}}`가 허용되는 자리는 **`type: "sessions"`인 setup 단계의 `sql`
   필드 하나뿐**이다. 그 밖의 어디든(다른 setup 타입, 같은 단계의 다른 필드,
   `objectives`, `brief`, `hints`, `debrief`) 등장하면 오류. 그런 자리에서는 영원히
   치환되지 않고 원문 `{{session_index}}`가 그대로 SQL이나 화면에 나간다.
2. `vars`에 `session_index`를 선언하면 오류(예약어 가림).

### 스테이지 변경

pg-1-1의 피해자 SQL:

```json
"sql": "UPDATE orders SET status = 'PAID' WHERE id = 1 + {{session_index}}"
```

`payments`가 3~6이므로 id는 1~6이고, `rows`가 30~80이므로 범인은 최소 1~30을
잠근다. 피해자는 항상 잠긴 구간 안에 들어간다. 이건 두 `vars` 선언 사이의 암묵적
관계라 조용히 깨질 수 있으므로 테스트로 고정한다.

`_comment`는 "중요하다"는 당부에서 구조적 보장으로 바꾸고 위 실측을 근거로 남긴다.

### 문서

`docs/shooting-game.md`의 `setup` 절에 `{{session_index}}`를 설명하고, 벤더가
갈리는 지점(PostgreSQL tuple lock 대 InnoDB 레코드 락 큐)을 실측과 함께 기록한다.

## 테스트

도커 없이 도는 단위 테스트로 다섯 가지를 고정한다.

| # | 무엇을 | 왜 |
|---|---|---|
| 1 | `render_session_sql`이 치환하고 **다른 자리는 남긴다** | 미치환 자리를 먹으면 SQL이 깨진다 |
| 2 | `setup_stage`가 세션 N개를 **서로 다른 SQL**로 띄운다 | 배선 테스트 — 검토 1번이 정확히 이 자리에서 났다 |
| 3 | `sessions` 밖에서 `{{session_index}}`를 쓰면 검증 실패 | 치환 안 된 원문이 SQL로 나가는 것을 로드 시점에 막는다 |
| 4 | `vars`가 `session_index`를 선언하면 검증 실패 | 예약어 가림 |
| 5 | pg-1-1: 모든 시드에서 피해자 id가 서로 다르고 범인 잠금 범위 안 | 두 `vars` 선언 사이의 암묵적 관계 고정 |

랩 실측(사례 A/B 재현)은 수정 후 한 번 더 돌려 확인하되, 도커가 필요하므로
자동화하지 않고 이 문서의 기록으로 남긴다.

## 범위 밖

- `kill_precision`이 `pg_cancel_backend`를 `pg_terminate_backend`와 동일 취급하는
  문제(검토 9번). 별개 사안이다.
- 다른 네 스테이지의 `sessions` 사용. 세션별 차이가 필요하지 않으므로 손대지 않는다.
