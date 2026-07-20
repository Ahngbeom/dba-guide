# 01. 고급 성능 튜닝 — 옵티마이저·파티셔닝·캐싱·커넥션 풀링

## 1. 핵심 개념 설명

중급 단계의 튜닝이 "느린 쿼리 하나를 인덱스로 고치는 것"이었다면, 고급 단계의 튜닝은 **시스템 전체의 처리량과 지연시간을 아키텍처 관점에서 설계**하는 일이다. 이를 위해서는 옵티마이저가 왜 그런 실행 계획을 골랐는지 내부 원리를 이해하고, 데이터가 커질 때 구조적으로 대응(파티셔닝)하며, 메모리 계층(캐싱)과 연결 계층(커넥션 풀링)을 함께 조율해야 한다.

### 옵티마이저 내부 동작 원리
현대 RDBMS는 대부분 **비용 기반 옵티마이저(CBO, Cost-Based Optimizer)**를 쓴다. 옵티마이저는 통계 정보(테이블 행 수, 컬럼별 카디널리티/히스토그램, 인덱스 클러스터링 팩터 등)를 바탕으로 여러 실행 계획 후보의 예상 비용을 계산하고 가장 싼 것을 고른다. 실무에서 "인덱스가 있는데 왜 안 타지?"의 90%는 **통계가 부정확하거나(오래됨), 카디널리티 추정이 틀려서** 옵티마이저가 풀 스캔이 더 싸다고 오판한 경우다. 따라서 시니어 DBA는 실행 계획을 읽을 때 항상 **추정 행 수(estimated rows)와 실제 행 수(actual rows)의 괴리**를 먼저 본다.

### 파티셔닝
테이블이 수억 건을 넘어가면 단일 물리 구조로는 관리(백업, 인덱스 재구성, 오래된 데이터 삭제)와 성능이 모두 어려워진다. 파티셔닝은 하나의 논리 테이블을 여러 물리 조각으로 나눠, **파티션 프루닝(pruning)**으로 필요한 조각만 스캔하게 한다. 시계열 로그는 Range(날짜), 지역/카테고리는 List, 균등 분산이 필요하면 Hash를 쓰는 것이 전형적이다. 파티셔닝은 성능 기법이자 **라이프사이클 관리 기법**(오래된 파티션을 `DROP`으로 즉시 삭제)이라는 점이 중요하다.

### 캐싱 전략
디스크 I/O는 메모리보다 수천 배 느리다. RDBMS는 **버퍼 풀(buffer pool / shared buffers)**에 데이터·인덱스 페이지를 캐싱해 반복 접근을 메모리에서 처리한다. 버퍼 풀 히트율, 워킹셋이 메모리에 들어가는지가 성능의 핵심이다. 반면 **쿼리 결과 캐시**(MySQL의 과거 query cache 등)는 동시성 환경에서 무효화 비용이 커서 현대에는 애플리케이션 레벨 캐시(Redis 등)로 대체되는 추세다.

### 커넥션 풀링 심화
DB 커넥션은 비싸다(프로세스/스레드 생성, 인증, 세션 메모리). 특히 PostgreSQL은 연결당 프로세스를 쓰기 때문에 수천 개의 유휴 연결이 메모리를 잠식한다. 커넥션 풀은 **연결을 재사용**해 이 비용을 줄이고, 동시에 DB로 향하는 부하의 상한을 통제하는 **방어벽** 역할도 한다.

---

## 2. 주요 명령어/문법

### 실행 계획과 통계

<!-- dbms:postgresql -->
**PostgreSQL**
```sql
-- 실제 실행하며 계획 + 버퍼 사용량까지 확인 (추정 vs 실제 비교의 핵심 도구)
EXPLAIN (ANALYZE, BUFFERS, VERBOSE) SELECT ...;

-- 통계 갱신
ANALYZE orders;
-- 특정 컬럼 통계 목표(히스토그램 정밀도) 상향
ALTER TABLE orders ALTER COLUMN customer_id SET STATISTICS 500;
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**MySQL**
```sql
EXPLAIN FORMAT=JSON SELECT ...;      -- 상세 비용/행 추정
EXPLAIN ANALYZE SELECT ...;          -- 8.0.18+ 실제 실행 계측
ANALYZE TABLE orders;                -- 통계 갱신
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**Oracle**
```sql
EXPLAIN PLAN FOR SELECT ...;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY);
-- 실제 실행 후 실측 계획 (A-Rows vs E-Rows 비교)
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));

BEGIN DBMS_STATS.GATHER_TABLE_STATS('APP','ORDERS'); END;
```
<!-- /dbms:oracle -->

### 파티셔닝 (Range 예시)

<!-- dbms:postgresql -->
**PostgreSQL (선언적 파티셔닝, 11+)**
```sql
CREATE TABLE events (id bigint, created_at date, payload jsonb)
  PARTITION BY RANGE (created_at);

CREATE TABLE events_2026_07 PARTITION OF events
  FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
-- 오래된 파티션 즉시 제거 (초고속, 락 최소)
DROP TABLE events_2025_01;
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**MySQL**
```sql
CREATE TABLE events (id BIGINT, created_at DATE, payload JSON)
PARTITION BY RANGE (TO_DAYS(created_at)) (
  PARTITION p202607 VALUES LESS THAN (TO_DAYS('2026-08-01')),
  PARTITION pmax    VALUES LESS THAN MAXVALUE
);
ALTER TABLE events DROP PARTITION p202601;
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**Oracle** (List / Hash도 유사, 인터벌 파티셔닝으로 자동 생성 가능)
```sql
CREATE TABLE events (id NUMBER, created_at DATE, payload CLOB)
PARTITION BY RANGE (created_at)
INTERVAL (NUMTOYMINTERVAL(1,'MONTH'))
( PARTITION p0 VALUES LESS THAN (DATE '2026-01-01') );
```
<!-- /dbms:oracle -->

### 캐싱 관련 주요 파라미터

<!-- dbms:postgresql -->
**PostgreSQL** (postgresql.conf) — 물리 RAM의 25% 정도가 통상 출발점
```ini
shared_buffers = 8GB
effective_cache_size = 24GB   # OS 캐시 포함 추정치, 옵티마이저 판단에 사용
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**MySQL** (InnoDB) — 전용 서버라면 RAM의 60~75%
```ini
innodb_buffer_pool_size = 24G
innodb_buffer_pool_instances = 8
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**Oracle** — SGA/PGA 자동 관리
```sql
ALTER SYSTEM SET MEMORY_TARGET = 32G;
```
<!-- /dbms:oracle -->

<!-- dbms:postgresql -->
### 커넥션 풀 (PgBouncer 예시)
```ini
[databases]
appdb = host=127.0.0.1 port=5432 dbname=appdb

[pgbouncer]
pool_mode = transaction      ; 트랜잭션 단위 재사용 (가장 흔한 선택)
max_client_conn = 5000       ; 앱에서 들어올 수 있는 연결 수
default_pool_size = 40       ; 실제 DB로 나가는 연결 수 (방어벽)
```
<!-- /dbms:postgresql -->

---

## 3. 실습 예제

<!-- dbms:postgresql -->
**시나리오: "특정 고객의 최근 주문 조회가 갑자기 느려졌다." (PostgreSQL 기준)**

1. **재현·계측**
   ```sql
   EXPLAIN (ANALYZE, BUFFERS)
   SELECT * FROM orders
   WHERE customer_id = 42 AND created_at >= '2026-07-01';
   ```
   실행 계획에서 `Seq Scan on orders (rows=1 ... actual rows=180000)`처럼 **추정 1행 vs 실제 18만 행**의 괴리를 발견한다. 옵티마이저가 데이터를 오해하고 있다는 신호다.

2. **원인 분석**: 최근 대량 적재 후 `ANALYZE`가 돌지 않아 통계가 낡음 → 옵티마이저가 인덱스 대신 풀 스캔 선택.

3. **1차 조치**: 통계 갱신.
   ```sql
   ANALYZE orders;
   ```
   재실행하면 `Index Scan using idx_orders_customer_created`로 바뀌고 실측 행 수가 정확해진다.

4. **구조적 개선 판단**: `orders`가 이미 3억 건이고 매월 수천만 건씩 증가 → 단순 인덱스로는 한계. `created_at` 기준 **Range 파티셔닝** 도입을 설계한다. 이후 쿼리는 해당 월 파티션만 스캔(프루닝)하고, 오래된 데이터는 `DROP PARTITION`으로 즉시 아카이빙 가능.

5. **연결 계층 점검**: 부하 급증 시 애플리케이션이 커넥션을 폭증시키지 않도록 PgBouncer `default_pool_size`로 DB 유입 상한을 고정. DB가 받는 최대 동시성이 통제되면 예측 가능한 성능이 유지된다.

> **트레이드오프 메모**: 파티셔닝은 공짜가 아니다. 파티션 키를 포함하지 않는 조회는 모든 파티션을 뒤져 오히려 느려질 수 있고, 글로벌 유니크 제약·외래키에 제약이 생긴다. "쿼리 패턴의 대부분이 파티션 키를 포함하는가"를 먼저 확인하고 도입하라.
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**시나리오: "특정 고객의 최근 주문 조회가 갑자기 느려졌다." (MySQL 기준)**

1. **재현·계측**
   ```sql
   EXPLAIN ANALYZE
   SELECT * FROM orders
   WHERE customer_id = 42 AND created_at >= '2026-07-01';
   ```
   실행 계획 트리에서 `-> Table scan on orders (cost=... rows=1) ... (actual time=... rows=180000 loops=1)`처럼 **추정 1행 vs 실제 18만 행**의 괴리를 발견한다. 인덱스가 있는데도 옵티마이저가 풀 테이블 스캔을 고른 신호다.

2. **원인 분석**: 최근 대량 적재(`LOAD DATA`) 후 InnoDB 테이블 통계가 갱신되지 않아 옵티마이저가 카디널리티를 오판 → 인덱스 대신 테이블 스캔 선택.

3. **1차 조치**: 통계 갱신.
   ```sql
   ANALYZE TABLE orders;
   ```
   재실행하면 계획이 `Index range scan on orders using idx_orders_customer_created`로 바뀌고 `actual rows`가 정확해진다.

4. **구조적 개선 판단**: `orders`가 이미 3억 건이고 매월 수천만 건씩 증가 → 단순 인덱스로는 한계. `created_at` 기준 **Range 파티셔닝**을 도입한다.
   ```sql
   ALTER TABLE orders
   PARTITION BY RANGE (TO_DAYS(created_at)) (
     PARTITION p202607 VALUES LESS THAN (TO_DAYS('2026-08-01')),
     PARTITION pmax    VALUES LESS THAN MAXVALUE
   );
   ```
   이후 쿼리는 해당 월 파티션만 스캔(프루닝)하고, 오래된 데이터는 `ALTER TABLE orders DROP PARTITION p202601;`으로 즉시 아카이빙 가능.

5. **캐시 계층 점검**: 워킹셋이 InnoDB 버퍼 풀에 다 들어가는지 확인한다.
   ```ini
   innodb_buffer_pool_size = 24G
   innodb_buffer_pool_instances = 8
   ```
   `SHOW ENGINE INNODB STATUS`나 `Innodb_buffer_pool_read_requests` 대비 `Innodb_buffer_pool_reads` 비율로 히트율을 모니터링하고, 디스크 읽기가 잦으면 버퍼 풀 크기부터 재산정한다.

> **트레이드오프 메모**: MySQL 파티셔닝은 모든 유니크 키(기본 키 포함)가 파티션 키를 포함해야 한다는 제약이 있다. 기존 스키마의 유니크 제약과 충돌하지 않는지 먼저 확인하고, 파티션 키를 포함하지 않는 조회는 프루닝이 되지 않아 오히려 느려질 수 있음을 감안하라.
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**시나리오: "특정 고객의 최근 주문 조회가 갑자기 느려졌다." (Oracle 기준)**

1. **재현·계측**
   ```sql
   SELECT * FROM orders
   WHERE customer_id = 42 AND created_at >= DATE '2026-07-01';

   SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));
   ```
   실측 계획에서 `FULL TABLE SCAN` 라인의 `E-Rows=1` vs `A-Rows=180000`처럼 **추정치와 실측치의 괴리**를 발견한다. 인덱스가 있음에도 CBO가 풀 스캔을 골랐다는 신호다.

2. **원인 분석**: 최근 대량 적재 후 옵티마이저 통계가 갱신되지 않아 CBO가 카디널리티를 오판 → 인덱스 스캔 대신 풀 테이블 스캔 선택.

3. **1차 조치**: 통계 갱신.
   ```sql
   BEGIN DBMS_STATS.GATHER_TABLE_STATS('APP','ORDERS'); END;
   ```
   재실행하면 `INDEX RANGE SCAN`으로 바뀌고 `A-Rows`가 `E-Rows`에 근접해진다.

4. **구조적 개선 판단**: `ORDERS`가 이미 3억 건이고 매월 수천만 건씩 증가 → 단순 인덱스로는 한계. `created_at` 기준 **인터벌 파티셔닝**을 설계한다.
   ```sql
   CREATE TABLE orders (...)
   PARTITION BY RANGE (created_at)
   INTERVAL (NUMTOYMINTERVAL(1,'MONTH'))
   ( PARTITION p0 VALUES LESS THAN (DATE '2026-01-01') );
   ```
   신규 월이 되면 파티션이 자동 생성되어 쿼리는 해당 월 파티션만 스캔(프루닝)하고, 오래된 파티션은 `ALTER TABLE orders DROP PARTITION p_2025_01;`으로 즉시 아카이빙할 수 있다. 기존 무파티션 테이블은 `DBMS_REDEFINITION`으로 온라인 전환한다.

5. **캐시 계층 점검**: SGA(버퍼 캐시)가 워킹셋을 담기에 충분한지 확인한다.
   ```sql
   ALTER SYSTEM SET MEMORY_TARGET = 32G;
   ```
   `MEMORY_TARGET`으로 SGA/PGA를 자동 관리하게 하고, `V$SGA`·버퍼 캐시 히트율을 모니터링해 물리 디스크 읽기가 잦으면 메모리 할당부터 재산정한다.

> **트레이드오프 메모**: Oracle 파티셔닝은 Enterprise Edition의 유료 옵션이라 라이선스 비용을 고려해야 한다. 인터벌 파티셔닝은 데이터가 불규칙하게 들어오면 파티션이 과도하게 잘게 생성될 수 있고, 글로벌 인덱스를 쓰면 파티션 `DROP` 시 인덱스 재구성 비용이 발생하므로 가능하면 로컬 인덱스를 우선 검토하라.
<!-- /dbms:oracle -->

---

## 4. 체크리스트

- [ ] `EXPLAIN ANALYZE` 결과에서 추정 행 수와 실제 행 수의 괴리를 찾아 통계 문제를 진단할 수 있다.
- [ ] 통계가 최적화에 미치는 영향을 이해하고, 자동/수동 통계 갱신 정책을 설정할 수 있다.
- [ ] 데이터 특성(시계열/카테고리/균등분산)에 따라 Range/List/Hash 파티셔닝을 선택하고 근거를 댈 수 있다.
- [ ] 파티션 프루닝이 실제로 동작하는지 실행 계획으로 검증할 수 있다.
- [ ] 버퍼 풀 크기를 워크로드에 맞게 산정하고 히트율을 모니터링할 수 있다.
- [ ] 쿼리 결과 캐시 대신 애플리케이션 캐시를 써야 하는 이유를 설명할 수 있다.
- [ ] 커넥션 풀 모드(session/transaction/statement)의 차이와 적용 상황을 구분할 수 있다.
- [ ] 커넥션 풀을 부하 방어벽으로 활용해 DB 유입 동시성을 통제할 수 있다.
