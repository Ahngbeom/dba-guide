# 06. 기본 모니터링

## 1. 핵심 개념 설명

**모니터링(Monitoring)** 은 데이터베이스가 지금 정상인지, 어디가 아픈지를 살펴보는 활동입니다. 사람으로 치면 건강 검진과 같습니다. DBA는 문제가 커지기 전에 미리 이상 징후를 발견해야 하고, 장애가 났을 때는 원인을 빠르게 찾아야 합니다. 그 첫걸음이 바로 기본 상태 점검입니다.

초급 단계에서 익혀야 할 기본 점검은 세 가지입니다.

- **로그(Log) 확인**: DBMS는 무슨 일이 있었는지 로그 파일에 기록합니다. 오류, 느린 쿼리, 접속 실패 등이 모두 남습니다. 문제가 생기면 **가장 먼저 로그를 봐야** 합니다.
- **세션/프로세스 조회**: 지금 누가 접속해 있고, 어떤 쿼리를 실행 중인지 봅니다. 데이터베이스가 갑자기 느려졌을 때, 무거운 쿼리를 돌리는 세션을 찾아내는 기본 도구입니다.
- **디스크 사용량 확인**: 데이터가 쌓여 디스크가 꽉 차면 데이터베이스가 멈춥니다. 용량이 얼마나 남았는지, 어느 테이블이 큰지 주기적으로 확인합니다.

이 세 가지만 익혀도 "지금 DB가 정상인가?"라는 질문에 스스로 답할 수 있게 됩니다.

---

## 2. 주요 명령어/문법

### 로그 파일 위치와 확인

로그는 대개 서버에 파일로 쌓입니다. 위치는 설정에 따라 다르지만 대표적인 기본 위치는 다음과 같습니다.

<!-- dbms:postgresql -->
```bash
# PostgreSQL — 로그 디렉터리는 설정에 따라 다름. 실제 경로는 아래 SQL로 확인
#   Linux 예시 경로: /var/log/postgresql/  또는 데이터디렉터리의 log/ 폴더
tail -f /var/log/postgresql/postgresql-16-main.log      # 실시간으로 로그 보기
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
```bash
# MySQL — 에러 로그 (Linux 예시)
tail -f /var/log/mysql/error.log
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
```bash
# Oracle — 알럿 로그 (경로는 환경에 따라 다름)
tail -f $ORACLE_BASE/diag/rdbms/*/*/trace/alert_*.log
```
<!-- /dbms:oracle -->

로그 위치를 SQL로 직접 확인할 수도 있습니다.

<!-- dbms:postgresql -->
```sql
-- PostgreSQL: 로그 관련 설정 확인
SHOW log_directory;      -- 로그 디렉터리
SHOW logging_collector;  -- 로그 수집 활성화 여부
SHOW data_directory;     -- 데이터 디렉터리 (log 폴더가 그 아래일 수 있음)
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
```sql
-- MySQL: 에러 로그 파일 경로 확인
SHOW VARIABLES LIKE 'log_error';
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
```sql
-- Oracle: 알럿 로그가 있는 진단 경로 확인
SELECT value FROM v$diag_info WHERE name = 'Diag Trace';
```
<!-- /dbms:oracle -->

> `tail -f`는 로그가 실시간으로 쌓이는 것을 계속 보여 줍니다. 멈추려면 `Ctrl + C`를 누릅니다. 최근 몇 줄만 보려면 `tail -n 50 파일명`을 씁니다.

### 현재 세션 / 프로세스 조회

지금 누가 무엇을 하고 있는지 보는 명령입니다. DBMS마다 다릅니다.

<!-- dbms:postgresql -->
**PostgreSQL — pg_stat_activity 뷰**

```sql
-- 현재 접속된 세션과 실행 중인 쿼리 조회
SELECT pid, usename AS 사용자, state AS 상태, query AS 쿼리
FROM pg_stat_activity
WHERE state != 'idle';        -- 놀고 있지 않은(작업 중인) 세션만

-- 특정 세션 강제 종료가 필요할 때 (pid는 위에서 확인한 값)
SELECT pg_terminate_backend(12345);
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**MySQL — SHOW PROCESSLIST**

```sql
-- 현재 접속/작업 목록
SHOW PROCESSLIST;

-- 더 자세히 (information_schema 활용)
SELECT id, user, db, command, time, state, info
FROM information_schema.processlist;

-- 특정 세션 강제 종료
KILL 12345;                   -- 12345는 위 목록의 id
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**Oracle — v$session 뷰**

```sql
-- 현재 활성 세션 조회
SELECT sid, serial#, username, status, sql_id
FROM v$session
WHERE username IS NOT NULL AND status = 'ACTIVE';

-- 특정 세션 강제 종료 (sid, serial# 필요)
ALTER SYSTEM KILL SESSION '145,12345';
```
<!-- /dbms:oracle -->

### 디스크 / 용량 사용량 확인

<!-- dbms:postgresql -->
**PostgreSQL**

```sql
-- 데이터베이스별 크기
SELECT datname, pg_size_pretty(pg_database_size(datname)) AS 크기
FROM pg_database ORDER BY pg_database_size(datname) DESC;

-- 특정 테이블 크기
SELECT pg_size_pretty(pg_total_relation_size('employees')) AS 테이블크기;
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**MySQL**

```sql
-- 데이터베이스별 크기 (MB 단위)
SELECT table_schema AS 데이터베이스,
       ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 크기_MB
FROM information_schema.tables
GROUP BY table_schema;
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**Oracle**

```sql
-- 테이블스페이스별 사용량
SELECT tablespace_name,
       ROUND(SUM(bytes) / 1024 / 1024, 2) AS 사용_MB
FROM dba_segments
GROUP BY tablespace_name;
```
<!-- /dbms:oracle -->

**운영체제 레벨 디스크 확인 (공통)**

```bash
df -h        # 전체 디스크 파티션별 남은 용량 (사람이 읽기 쉬운 단위)
du -sh /var/lib/postgresql/    # 특정 데이터 디렉터리가 차지하는 용량
```

---

## 3. 실습 예제

<!-- dbms:postgresql -->
**시나리오: 지금 데이터베이스가 정상인지 3단계로 점검해 보자. (PostgreSQL 기준)**

```sql
-- 1) 지금 누가 접속해서 무엇을 하는지 확인
SELECT pid, usename, state, query
FROM pg_stat_activity
WHERE state != 'idle';
--   장시간 실행 중인 무거운 쿼리가 있는지 살펴본다.

-- 2) 데이터베이스 용량이 얼마나 되는지 확인
SELECT datname, pg_size_pretty(pg_database_size(datname))
FROM pg_database
ORDER BY pg_database_size(datname) DESC;
--   특정 DB가 비정상적으로 커지고 있지 않은지 확인한다.
```

```bash
# 3) 서버 디스크에 여유가 있는지 확인
df -h
#   데이터 디렉터리가 있는 파티션의 Use%가 90%를 넘으면 위험 신호!

# 4) 최근 오류가 있었는지 로그 확인
tail -n 50 /var/log/postgresql/postgresql-16-main.log
#   ERROR, FATAL 같은 단어가 보이면 원인을 추적한다.
```

이 네 가지를 순서대로 확인하는 습관을 들이면, "DB가 느려요"라는 요청을 받았을 때 어디부터 봐야 할지 막막하지 않게 됩니다. 세션 → 용량 → 디스크 → 로그 순으로 훑는 것이 좋은 시작점입니다.
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**시나리오: 지금 데이터베이스가 정상인지 3단계로 점검해 보자. (MySQL 기준)**

```sql
-- 1) 지금 누가 접속해서 무엇을 하는지 확인
SELECT id, user, db, command, time, state, info
FROM information_schema.processlist
WHERE command != 'Sleep';
--   Time(초)이 큰데도 계속 실행 중인 무거운 쿼리가 있는지 살펴본다.

-- 2) 데이터베이스 용량이 얼마나 되는지 확인
SELECT table_schema AS 데이터베이스,
       ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 크기_MB
FROM information_schema.tables
GROUP BY table_schema
ORDER BY 크기_MB DESC;
--   특정 DB가 비정상적으로 커지고 있지 않은지 확인한다.
```

```bash
# 3) 서버 디스크에 여유가 있는지 확인
df -h
#   데이터 디렉터리가 있는 파티션의 Use%가 90%를 넘으면 위험 신호!

# 4) 최근 오류가 있었는지 로그 확인
tail -n 50 /var/log/mysql/error.log
#   ERROR 같은 단어가 보이면 원인을 추적한다.
```

이 네 가지를 순서대로 확인하는 습관을 들이면, "DB가 느려요"라는 요청을 받았을 때 어디부터 봐야 할지 막막하지 않게 됩니다. 세션 → 용량 → 디스크 → 로그 순으로 훑는 것이 좋은 시작점입니다.
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**시나리오: 지금 데이터베이스가 정상인지 3단계로 점검해 보자. (Oracle 기준)**

```sql
-- 1) 지금 누가 접속해서 무엇을 하는지 확인
SELECT sid, serial#, username, status, sql_id
FROM v$session
WHERE username IS NOT NULL AND status = 'ACTIVE';
--   장시간 ACTIVE 상태로 남아 있는 무거운 세션이 있는지 살펴본다.

-- 2) 테이블스페이스별 할당 용량이 얼마나 되는지 확인
SELECT tablespace_name,
       ROUND(SUM(bytes) / 1024 / 1024, 2) AS 할당_MB
FROM dba_data_files
GROUP BY tablespace_name
ORDER BY 할당_MB DESC;
--   특정 테이블스페이스가 비정상적으로 커지고 있지 않은지 확인한다.
```

```bash
# 3) 서버 디스크에 여유가 있는지 확인
df -h
#   데이터 파일이 있는 파티션의 Use%가 90%를 넘으면 위험 신호!

# 4) 최근 오류가 있었는지 알럿 로그 확인
tail -n 50 $ORACLE_BASE/diag/rdbms/*/*/trace/alert_*.log
#   ORA-로 시작하는 에러 코드가 보이면 원인을 추적한다.
```

이 네 가지를 순서대로 확인하는 습관을 들이면, "DB가 느려요"라는 요청을 받았을 때 어디부터 봐야 할지 막막하지 않게 됩니다. 세션 → 용량 → 디스크 → 로그 순으로 훑는 것이 좋은 시작점입니다.
<!-- /dbms:oracle -->

---

## 4. 체크리스트

- [ ] 모니터링이 왜 필요한지(문제를 미리·빠르게 발견) 설명할 수 있다.
- [ ] PostgreSQL/MySQL/Oracle의 로그 파일 위치를 확인하는 방법을 안다.
- [ ] `tail -f`와 `tail -n`으로 로그를 실시간·부분 확인할 수 있다.
- [ ] pg_stat_activity / SHOW PROCESSLIST / v$session으로 현재 세션을 조회할 수 있다.
- [ ] 각 DBMS에서 문제 세션을 강제 종료하는 명령을 안다.
- [ ] 데이터베이스·테이블 용량을 조회할 수 있다.
- [ ] `df -h`로 서버 디스크 여유 공간을 확인할 수 있다.
- [ ] "DB가 느리다"는 요청에 세션→용량→디스크→로그 순으로 점검할 수 있다.
