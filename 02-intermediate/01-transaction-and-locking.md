# 트랜잭션과 락 (동시성 제어)

## 핵심 개념 설명

여러 사용자가 동시에 같은 데이터를 읽고 쓰는 환경에서, DBMS는 **각 트랜잭션이 마치 혼자 실행되는 것처럼** 보이도록 만들어야 한다. 이것을 격리성(Isolation)이라 하며, 트랜잭션의 ACID 성질 중 하나다. 하지만 완벽한 격리는 성능을 크게 떨어뜨리기 때문에, DBMS는 여러 단계의 **격리 수준(Isolation Level)** 을 제공해 성능과 정합성을 절충하게 한다.

격리 수준이 낮으면 동시 처리량은 높지만 아래와 같은 이상 현상(anomaly)이 발생할 수 있다.

- **Dirty Read**: 아직 커밋되지 않은 다른 트랜잭션의 변경을 읽는다.
- **Non-repeatable Read**: 같은 행을 두 번 읽었는데 값이 달라진다(중간에 다른 트랜잭션이 수정·커밋).
- **Phantom Read**: 같은 조건으로 조회했는데 행의 개수가 달라진다(중간에 다른 트랜잭션이 삽입·삭제).

표준 SQL의 4가지 격리 수준과 이상 현상 허용 여부는 다음과 같다.

| 격리 수준 | Dirty Read | Non-repeatable Read | Phantom Read |
|---|---|---|---|
| Read Uncommitted | 가능 | 가능 | 가능 |
| Read Committed | 방지 | 가능 | 가능 |
| Repeatable Read | 방지 | 방지 | 가능(표준) |
| Serializable | 방지 | 방지 | 방지 |

실무에서는 대부분 **Read Committed**(Oracle/PostgreSQL 기본)나 **Repeatable Read**(MySQL InnoDB 기본)를 쓴다. 은행 이체나 재고 차감처럼 정합성이 결정적으로 중요한 로직에서는 더 높은 수준이나 명시적 잠금을 선택한다.

### MVCC (다중 버전 동시성 제어)

현대 DBMS(PostgreSQL, MySQL InnoDB, Oracle)는 **MVCC**로 "읽기는 쓰기를 막지 않고, 쓰기는 읽기를 막지 않는다"를 구현한다. 데이터를 수정할 때 기존 행을 덮어쓰지 않고 새 버전을 만들며, 각 트랜잭션은 자신이 시작한 시점의 스냅샷을 본다. 덕분에 조회 트랜잭션이 오래 걸려도 갱신 트랜잭션을 차단하지 않는다.

MVCC의 대가로 **오래된 행 버전(dead tuple)** 이 쌓이므로 주기적으로 정리·관리해야 한다.



오래 열려 있는 트랜잭션은 정리를 방해하므로 주의해야 한다.

### 락의 종류

- **공유 락(Shared, S)**: 읽기용. 여러 트랜잭션이 동시에 보유 가능.
- **배타 락(Exclusive, X)**: 쓰기용. 하나만 보유 가능하며 다른 락과 공존 불가.
- **행 락(Row Lock)**: 특정 행만 잠금. 동시성이 높다.
- **테이블 락(Table Lock)**: 테이블 전체를 잠금. DDL 등에서 사용되며 동시성이 낮다.

## 주요 명령어/문법

### 격리 수준 확인 및 설정


**MySQL**
```sql
SELECT @@transaction_isolation;

SET TRANSACTION ISOLATION LEVEL READ COMMITTED;  -- 다음 트랜잭션에만 적용
START TRANSACTION;
-- ... 작업 ...
COMMIT;

-- 세션/전역 기본값
SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;
```


### 명시적 잠금

```sql
-- PostgreSQL / MySQL / Oracle 공통: 조회한 행에 배타 락
SELECT * FROM accounts WHERE id = 100 FOR UPDATE;
```


```sql
-- MySQL: 공유 락으로 읽기
SELECT * FROM accounts WHERE id = 100 LOCK IN SHARE MODE; -- MySQL 구버전
SELECT * FROM accounts WHERE id = 100 FOR SHARE;        -- MySQL 8.0+
```

```sql
-- 락 대기 대신 즉시 실패 / 건너뛰기 (PostgreSQL / MySQL 8.0+ / Oracle 공통)
SELECT * FROM jobs WHERE status = 'ready' FOR UPDATE SKIP LOCKED;  -- 큐 처리에 유용
SELECT * FROM accounts WHERE id = 100 FOR UPDATE NOWAIT;
```

## 실습 예제

시나리오: 계좌 `A(잔액 1000)` 에서 `B` 로 100원을 이체하되, 동시성 문제를 방지한다. 터미널 2개로 각각 트랜잭션을 열어 실습한다.

```sql
-- 세션 1
BEGIN;
SELECT balance FROM accounts WHERE id = 'A' FOR UPDATE;  -- 행에 배타 락 획득
UPDATE accounts SET balance = balance - 100 WHERE id = 'A';
-- 아직 COMMIT하지 않음

-- 세션 2 (동시에 실행)
BEGIN;
UPDATE accounts SET balance = balance - 100 WHERE id = 'A';  -- 세션 1의 락 때문에 대기(블로킹)

-- 세션 1
UPDATE accounts SET balance = balance + 100 WHERE id = 'B';
COMMIT;   -- 락 해제 → 세션 2의 UPDATE가 진행됨
```

### 데드락 재현과 진단

데드락은 두 트랜잭션이 서로가 가진 락을 기다릴 때 발생한다.

```sql
-- 세션 1                          -- 세션 2
BEGIN;                             BEGIN;
UPDATE t SET v=1 WHERE id=1;       UPDATE t SET v=1 WHERE id=2;
UPDATE t SET v=1 WHERE id=2;  -->  UPDATE t SET v=1 WHERE id=1;
-- 세션 2의 락 대기                -- 세션 1의 락 대기 → 데드락!
```

DBMS는 데드락을 자동 감지해 한쪽 트랜잭션을 강제 롤백한다.

**진단 방법**


```sql
-- MySQL: 가장 최근 데드락 상세 로그
SHOW ENGINE INNODB STATUS;   -- LATEST DETECTED DEADLOCK 섹션 확인
```


**데드락 예방 원칙**: 여러 트랜잭션이 여러 자원을 잠글 때 **항상 같은 순서로** 접근하도록 애플리케이션 로직을 통일한다. 트랜잭션은 짧게 유지하고, 사용자 입력을 기다리는 동안 트랜잭션을 열어두지 않는다.

## 체크리스트

- [ ] 4가지 격리 수준과 각 수준에서 허용되는 이상 현상을 표로 설명할 수 있다.
- [ ] 각 DBMS의 기본 격리 수준(PostgreSQL/Oracle: Read Committed, MySQL: Repeatable Read)을 안다.
- [ ] MVCC가 읽기-쓰기 충돌을 어떻게 줄이는지, 그 부작용(dead tuple, VACUUM/UNDO)을 설명할 수 있다.
- [ ] 공유 락과 배타 락, 행 락과 테이블 락의 차이를 안다.
- [ ] `SELECT ... FOR UPDATE`로 명시적 잠금을 걸고 동시성 시나리오를 제어할 수 있다.
- [ ] `FOR UPDATE SKIP LOCKED`가 큐 처리에서 왜 유용한지 설명할 수 있다.
- [ ] 데드락을 재현하고, `SHOW ENGINE INNODB STATUS`나 `pg_locks`/`v$lock`로 원인을 진단할 수 있다.
- [ ] 데드락 예방을 위한 자원 접근 순서 통일 원칙을 안다.
