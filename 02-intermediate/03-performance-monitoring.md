# 성능 모니터링

## 핵심 개념 설명

성능 모니터링은 "장애가 나기 전에 이상 징후를 발견하고, 장애가 났을 때 원인을 빠르게 찾는" DBA의 핵심 일상 업무다. 모니터링 없이 운영하는 것은 계기판 없이 운전하는 것과 같다. 사용자가 "느려요"라고 말하기 전에 지표로 먼저 알아채야 한다.

모니터링은 크게 두 축으로 본다. 첫째는 **쿼리 단위 분석**으로, 어떤 쿼리가 얼마나 자주 실행되고 얼마나 오래 걸리는지를 추적한다(슬로우 쿼리 로그, 통계 뷰). 둘째는 **인스턴스 단위 지표**로, 캐시 히트율·커넥션 수·I/O·락 대기 같은 시스템 전반의 건강 상태를 본다.

### 주요 성능 지표

- **캐시(버퍼) 히트율**: 요청한 데이터가 디스크가 아닌 메모리에서 처리된 비율. 보통 99% 이상을 목표로 하며, 낮으면 메모리 부족이나 비효율 쿼리를 의심한다.
- **커넥션 수**: 현재 연결/활성 세션 수. `max_connections`에 근접하면 연결 거부가 발생한다. 커넥션 풀 사용이 권장된다.
- **I/O / 디스크 읽기**: 물리 읽기가 많으면 인덱스 부재나 캐시 부족 신호다.
- **락 대기 / 블로킹**: 대기 세션이 많으면 경합이 있는 것이다.
- **복제 지연(Replication Lag)**: 복제 환경에서 replica가 primary를 얼마나 따라가는지(뒤에 별도 챕터).
- **슬로우 쿼리 수와 상위 쿼리**: 튜닝 우선순위를 정하는 근거.

### 쿼리 통계 수집 도구

<!-- dbms:postgresql -->
- **PostgreSQL — `pg_stat_statements`**: 정규화된 쿼리별 총 실행 시간, 호출 횟수, 평균 시간을 누적한다. 튜닝 대상 발굴의 1순위 도구.
<!-- /dbms:postgresql -->
<!-- dbms:mysql -->
- **MySQL — Performance Schema / sys 스키마**: 문장 다이제스트별 통계, 대기 이벤트, I/O 등을 제공한다. `sys` 스키마는 이를 읽기 쉽게 가공한 뷰 모음이다.
<!-- /dbms:mysql -->
<!-- dbms:oracle -->
- **Oracle — AWR / ASH / Statspack**: AWR(Automatic Workload Repository)는 스냅샷 간 성능 리포트를, ASH(Active Session History)는 세션 활동 샘플을 제공한다(진단 팩 라이선스 필요, 무료 대안은 Statspack).
<!-- /dbms:oracle -->

## 주요 명령어/문법

### 슬로우 쿼리 로그 설정

<!-- dbms:postgresql -->
**PostgreSQL** (`postgresql.conf` 또는 `ALTER SYSTEM`)
```sql
ALTER SYSTEM SET log_min_duration_statement = '500ms';  -- 500ms 넘는 쿼리 로깅
ALTER SYSTEM SET log_line_prefix = '%m [%p] %u@%d ';
SELECT pg_reload_conf();  -- 재적용(재시작 불필요)
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**MySQL** (`my.cnf` 또는 동적 설정)
```sql
SET GLOBAL slow_query_log = 'ON';
SET GLOBAL long_query_time = 0.5;          -- 0.5초 초과 쿼리
SET GLOBAL slow_query_log_file = '/var/log/mysql/slow.log';
SET GLOBAL log_queries_not_using_indexes = 'ON';  -- 인덱스 미사용 쿼리도 기록
-- 분석: mysqldumpslow -s t /var/log/mysql/slow.log  또는 pt-query-digest
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**Oracle** — 별도 슬로우 로그 대신 AWR/ASH 및 SQL Trace 사용
```sql
-- 특정 세션 추적
EXEC DBMS_MONITOR.SESSION_TRACE_ENABLE(session_id => 123, serial_num => 456);
-- 생성된 trace 파일을 tkprof로 정리
```
<!-- /dbms:oracle -->

### 쿼리 통계 조회

<!-- dbms:postgresql -->
**PostgreSQL**
```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;  -- shared_preload_libraries 등록 필요

-- 총 실행 시간 상위 10개
SELECT query, calls, total_exec_time, mean_exec_time, rows
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 10;

-- 캐시 히트율
SELECT sum(blks_hit) * 100.0 / nullif(sum(blks_hit + blks_read), 0) AS cache_hit_pct
FROM pg_stat_database;

-- 현재 활성 세션 / 커넥션 수
SELECT state, count(*) FROM pg_stat_activity GROUP BY state;
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**MySQL**
```sql
-- 가장 느린 문장 다이제스트 (sys 스키마)
SELECT query, exec_count, avg_latency, rows_examined_avg
FROM sys.statement_analysis
ORDER BY total_latency DESC LIMIT 10;

-- 버퍼 풀 히트율(대략)
SHOW ENGINE INNODB STATUS\G   -- BUFFER POOL AND MEMORY 섹션
SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read%';
-- hit% ≈ 1 - Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests

-- 커넥션 현황
SHOW STATUS LIKE 'Threads_connected';
SHOW STATUS LIKE 'Max_used_connections';
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**Oracle**
```sql
-- AWR 리포트 생성(대화형)
@?/rdbms/admin/awrrpt.sql

-- 라이브러리 캐시 히트율
SELECT namespace, gethitratio FROM v$librarycache;
-- 버퍼 캐시 히트율
SELECT 1 - (phy.value / (cur.value + con.value)) AS hit_ratio
FROM v$sysstat cur, v$sysstat con, v$sysstat phy
WHERE cur.name='db block gets' AND con.name='consistent gets'
  AND phy.name='physical reads';
```
<!-- /dbms:oracle -->

## 실습 예제

<!-- dbms:postgresql -->
시나리오: "오후 2시경 응답이 느려진다"는 제보를 받았다. 원인 쿼리를 찾는다. (PostgreSQL 기준)

```sql
-- 1) PostgreSQL: 통계 초기화 후 부하 시간대 동안 수집
SELECT pg_stat_statements_reset();
-- ... 문제 시간대 경과 대기 ...

-- 2) 총 소요 시간 기준 상위 쿼리 확인
SELECT substr(query,1,60) AS q, calls, round(mean_exec_time::numeric,2) AS avg_ms,
       round(total_exec_time::numeric,2) AS total_ms
FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 5;

-- 3) 의심 쿼리를 EXPLAIN ANALYZE로 분석 → 인덱스 튜닝(02장 참고)

-- 4) 동시에 커넥션/락 경합도 점검
SELECT count(*) FROM pg_stat_activity WHERE state = 'active';
SELECT pid, wait_event_type, wait_event, query
FROM pg_stat_activity WHERE wait_event_type = 'Lock';
```

핵심은 "느림"을 **호출 횟수 × 평균 시간 = 총 부하** 관점으로 보는 것이다. 한 번에 느린 쿼리보다, 빠르지만 수만 번 호출되는 쿼리가 전체 부하의 주범인 경우가 흔하다.
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
시나리오: "오후 2시경 응답이 느려진다"는 제보를 받았다. 원인 쿼리를 찾는다. (MySQL 기준)

```sql
-- 1) MySQL: 다이제스트 통계 초기화 후 부하 시간대 동안 수집
TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;
-- ... 문제 시간대 경과 대기 ...

-- 2) 총 소요 시간 기준 상위 쿼리 확인 (sys 스키마)
SELECT query, exec_count, avg_latency, total_latency, rows_examined_avg
FROM sys.statement_analysis
ORDER BY total_latency DESC LIMIT 5;

-- 3) 의심 쿼리를 EXPLAIN / EXPLAIN ANALYZE로 분석 → 인덱스 튜닝(02장 참고)
EXPLAIN ANALYZE
SELECT * FROM orders WHERE customer_id = 1001 AND status = 'PAID';

-- 4) 동시에 커넥션/락 경합도 점검
SHOW STATUS LIKE 'Threads_connected';
SELECT * FROM sys.innodb_lock_waits;   -- 블로킹/대기 트랜잭션 확인
```

PostgreSQL의 `pg_stat_statements`처럼, MySQL도 `sys.statement_analysis`의 `exec_count`(호출 횟수)와 `avg_latency`(평균 지연)를 곱한 `total_latency`가 곧 총 부하다. 이번에 `TRUNCATE`로 다이제스트 통계를 초기화한 이유는 관측 구간을 문제 시간대로 좁혀 잡음을 줄이기 위함이며, 운영 환경에서는 초기화 대신 `first_seen`/`last_seen` 컬럼으로 구간을 걸러내는 방법도 있다.
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
시나리오: "오후 2시경 응답이 느려진다"는 제보를 받았다. 원인 쿼리를 찾는다. (Oracle 기준)

```sql
-- 1) 문제 시간대 전후의 AWR 스냅샷 확인 (자동 스냅샷은 기본 1시간 간격)
SELECT snap_id, to_char(end_interval_time,'YYYY-MM-DD HH24:MI') AS snap_time
FROM dba_hist_snapshot
WHERE end_interval_time BETWEEN sysdate - 1 AND sysdate
ORDER BY snap_id;

-- 2) 해당 구간의 AWR 리포트 생성(대화형으로 begin/end snap 번호 입력)
@?/rdbms/admin/awrrpt.sql

-- 즉시 확인이 필요하면 v$sql에서 누적 elapsed_time 기준 상위 SQL 조회
SELECT sql_id, substr(sql_text,1,60) AS sql_text, executions,
       round(elapsed_time/1e6, 2) AS elapsed_sec,
       round(elapsed_time / nullif(executions,0) / 1e6, 2) AS avg_sec
FROM v$sql
ORDER BY elapsed_time DESC
FETCH FIRST 5 ROWS ONLY;

-- 3) 의심 SQL의 실제 실행 계획을 DBMS_XPLAN으로 확인
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR('&sql_id', NULL, 'ALLSTATS LAST'));

-- 4) 동시에 활성 세션 대기 이벤트 점검(ASH)
SELECT event, count(*) AS sample_cnt
FROM v$active_session_history
WHERE sample_time > sysdate - 1/24
GROUP BY event
ORDER BY sample_cnt DESC;
```

여기서도 핵심은 **`executions`(실행 횟수) × 평균 elapsed_time = 총 부하**다. 다만 `v$sql`은 PostgreSQL의 `pg_stat_statements`처럼 원하는 시점에 리셋할 수 있는 카운터가 아니라 인스턴스 시작(또는 커서 하드파스 이후) 누적치이므로, 특정 시간대만 콕 집어 보려면 그 구간의 AWR 스냅샷 두 개를 비교하는 것이 정확하다. `v$sql` 조회는 "지금 당장 의심 SQL을 빠르게 스크리닝"할 때, AWR 리포트는 "그 시간대만의 부하를 정밀 분석"할 때 쓴다고 구분하면 된다.
<!-- /dbms:oracle -->

## 체크리스트

- [ ] 각 DBMS에서 슬로우 쿼리 로그(또는 SQL Trace)를 활성화하고 임계값을 설정할 수 있다.
- [ ] 캐시(버퍼) 히트율을 조회하고 낮을 때의 의미를 해석할 수 있다.
- [ ] 현재 커넥션/활성 세션 수를 확인하고 `max_connections` 근접 위험을 판단할 수 있다.
<!-- dbms:postgresql -->
- [ ] `pg_stat_statements`를 설치·조회해 총 부하 상위 쿼리를 뽑을 수 있다.
<!-- /dbms:postgresql -->
<!-- dbms:mysql -->
- [ ] MySQL `sys` 스키마 / Performance Schema로 느린 문장을 찾을 수 있다.
<!-- /dbms:mysql -->
<!-- dbms:oracle -->
- [ ] Oracle AWR/ASH가 무엇을 제공하는지 개요 수준으로 설명할 수 있다.
<!-- /dbms:oracle -->
- [ ] "총 부하 = 호출 횟수 × 평균 시간" 관점으로 튜닝 우선순위를 정할 수 있다.
- [ ] 락 대기/블로킹 세션을 조회해 경합 여부를 진단할 수 있다.
