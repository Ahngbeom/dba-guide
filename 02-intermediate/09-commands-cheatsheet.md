# 중급 단계 명령어 대조표

이 단계에서 다룬 개념·명령어를 PostgreSQL / MySQL / Oracle 기준으로 한눈에 비교한다. 실무 중 빠르게 참조하는 용도로 사용한다.

## 트랜잭션과 격리 수준

| 작업 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 기본 격리 수준 | Read Committed | Repeatable Read | Read Committed |
| 지원 격리 수준 | RC, RR, Serializable | 4단계 모두 | RC, Serializable |
| 격리 수준 확인 | `SHOW transaction_isolation;` | `SELECT @@transaction_isolation;` | `-` (세션 설정으로) |
| 격리 수준 변경 | `SET TRANSACTION ISOLATION LEVEL ...` | `SET TRANSACTION ISOLATION LEVEL ...` | `SET TRANSACTION ISOLATION LEVEL ...` |
| 트랜잭션 시작 | `BEGIN;` | `START TRANSACTION;` | (암시적 시작) |
| 배타 잠금 조회 | `SELECT ... FOR UPDATE;` | `SELECT ... FOR UPDATE;` | `SELECT ... FOR UPDATE;` |
| 잠긴 행 건너뛰기 | `FOR UPDATE SKIP LOCKED` | `FOR UPDATE SKIP LOCKED` | `FOR UPDATE SKIP LOCKED` |
| 동시성 제어 방식 | MVCC (+ VACUUM) | MVCC (InnoDB, UNDO) | MVCC (UNDO 세그먼트) |

## 락/데드락 진단

| 작업 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 락 현황 조회 | `SELECT * FROM pg_locks;` | `SELECT * FROM performance_schema.data_locks;` | `SELECT * FROM v$lock;` |
| 블로킹 세션 | `pg_stat_activity` 조인 | `sys.innodb_lock_waits` | `v$session.blocking_session` |
| 데드락 로그 | 서버 로그(`deadlock detected`) | `SHOW ENGINE INNODB STATUS` | alert log / trace |

## 인덱스

| 작업 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 인덱스 생성 | `CREATE INDEX i ON t(c);` | `CREATE INDEX i ON t(c);` | `CREATE INDEX i ON t(c);` |
| 유니크 인덱스 | `CREATE UNIQUE INDEX ...` | `CREATE UNIQUE INDEX ...` | `CREATE UNIQUE INDEX ...` |
| 무중단 생성 | `CREATE INDEX CONCURRENTLY ...` | `... ALGORITHM=INPLACE, LOCK=NONE` | `CREATE INDEX ... ONLINE` |
| 부분 인덱스 | `... WHERE cond` | (미지원, 생성 컬럼 우회) | (함수 기반 인덱스 우회) |
| 전문/특수 인덱스 | GIN / GiST | FULLTEXT | Domain/Text Index |
| 인덱스 삭제 | `DROP INDEX i;` | `DROP INDEX i ON t;` | `DROP INDEX i;` |

## 실행계획 / 쿼리 분석

| 작업 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 계획만 보기 | `EXPLAIN <sql>` | `EXPLAIN <sql>` | `EXPLAIN PLAN FOR <sql>` |
| 실제 실행 측정 | `EXPLAIN (ANALYZE, BUFFERS) <sql>` | `EXPLAIN ANALYZE <sql>` (8.0.18+) | `DBMS_XPLAN.DISPLAY_CURSOR(...,'ALLSTATS LAST')` |
| 계획 출력 | (즉시 출력) | (즉시 출력) | `SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY);` |
| 통계 갱신 | `ANALYZE t;` | `ANALYZE TABLE t;` | `DBMS_STATS.GATHER_TABLE_STATS(...)` |
| 전체 스캔 표기 | `Seq Scan` | `type: ALL` | `TABLE ACCESS FULL` |

## 성능 모니터링

| 작업 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 쿼리 통계 도구 | `pg_stat_statements` | Performance Schema / `sys` | AWR / ASH / Statspack |
| 슬로우 쿼리 로그 | `log_min_duration_statement` | `slow_query_log`, `long_query_time` | SQL Trace / `DBMS_MONITOR` |
| 상위 부하 쿼리 | `SELECT ... FROM pg_stat_statements ORDER BY total_exec_time DESC` | `sys.statement_analysis` | AWR 리포트 Top SQL |
| 활성 세션 | `pg_stat_activity` | `SHOW PROCESSLIST` / `performance_schema.threads` | `v$session` |
| 커넥션 수 | `SELECT count(*) FROM pg_stat_activity;` | `SHOW STATUS LIKE 'Threads_connected';` | `SELECT count(*) FROM v$session;` |
| 캐시 히트율 | `pg_stat_database (blks_hit/read)` | `Innodb_buffer_pool_read%` | `v$sysstat` / `v$librarycache` |

## 백업 / 복구

| 작업 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 논리 백업 | `pg_dump` / `pg_dumpall` | `mysqldump` / `mysqlpump` | `expdp` (Data Pump) |
| 논리 복원 | `pg_restore` / `psql` | `mysql <` | `impdp` |
| 물리 백업 | `pg_basebackup` | XtraBackup | RMAN `BACKUP DATABASE` |
| 연속 로그 | WAL 아카이빙 | 바이너리 로그(binlog) | 아카이브 로그 |
| 연속 로그 목록 확인 | `SELECT * FROM pg_ls_waldir();` | `SHOW BINARY LOGS;` | `SELECT * FROM v$archived_log;` |
| PITR 목표 지정 | `recovery_target_time` | `mysqlbinlog --stop-datetime` | `RECOVER ... UNTIL TIME` |

## 복제

| 항목 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 복제 방식 | Streaming Replication (WAL) | Binlog + GTID | Data Guard (redo) |
| standby 초기화 | `pg_basebackup -R` | `mysqldump --set-gtid-purged` + `CHANGE REPLICATION SOURCE` | RMAN duplicate |
| 복제 시작 | (standby.signal 기동) | `START REPLICA;` | `RECOVER MANAGED STANDBY DATABASE` |
| 상태/지연 확인 | `pg_stat_replication` | `SHOW REPLICA STATUS` (Seconds_Behind_Source) | `v$dataguard_stats` |
| 동기 복제 설정 | `synchronous_standby_names` | `rpl_semi_sync_*` (semi-sync) | Protection Mode |

## 스키마 변경

| 작업 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 컬럼 추가(즉시) | `ADD COLUMN ... DEFAULT` (11+) | `ADD COLUMN ..., ALGORITHM=INSTANT` | `ADD (col ... DEFAULT ...)` |
| 무중단 인덱스 | `CREATE INDEX CONCURRENTLY` | `ALGORITHM=INPLACE, LOCK=NONE` | `... ONLINE` |
| 제약 2단계 추가 | `... NOT VALID` → `VALIDATE CONSTRAINT` | (도구 활용) | `... ENABLE NOVALIDATE` |
| 무중단 변경 도구 | (논리 복제/CONCURRENTLY) | `pt-online-schema-change`, `gh-ost` | `DBMS_REDEFINITION` |
| 마이그레이션 도구 | Flyway / Liquibase (공통) | Flyway / Liquibase (공통) | Flyway / Liquibase (공통) |

## 클라우드 DB 인프라/접속

| 작업 | AWS RDS | GCP Cloud SQL |
|---|---|---|
| 서브넷 그룹/네트워크 준비 | `aws rds create-db-subnet-group` | (기본 VPC 또는 프라이빗 서비스 액세스) |
| 보안 그룹/방화벽 규칙 | `aws ec2 create-security-group` + `authorize-security-group-ingress` | `gcloud compute firewall-rules create` |
| 프라이빗 인스턴스 생성 | `create-db-instance --no-publicly-accessible` | `gcloud sql instances create --no-assign-ip` |
| SSL 검증 접속 | `psql "... sslmode=verify-full sslrootcert=..."` | `psql "... sslmode=verify-full"` |
| IAM 인증 접속 | `aws rds generate-db-auth-token` → 토큰을 비밀번호로 사용 | `gcloud sql users create --type=cloud_iam_user` |
| 배스천 없는 프라이빗 접속 | `aws ssm start-session --document-name AWS-StartPortForwardingSessionToRemoteHost` | Cloud SQL Auth Proxy (`--auto-iam-authn`) |

## 클라우드 관리형 DB

| 개념 | AWS RDS | GCP Cloud SQL |
|---|---|---|
| 설정 변경 | 파라미터 그룹 | 데이터베이스 플래그 |
| 설정 명령 | `aws rds modify-db-parameter-group` | `gcloud sql instances patch --database-flags` |
| 스냅샷 생성 | `aws rds create-db-snapshot` | `gcloud sql backups create` |
| PITR | `restore-db-instance-to-point-in-time` | `gcloud sql instances clone --point-in-time` |
| 마이너 업그레이드 | `modify-db-instance --engine-version` | `patch --maintenance-version` |
| 고가용성 | Multi-AZ | HA configuration |
| 읽기 확장 | Read Replica | Read Replica |
| 모니터링 | CloudWatch / Performance Insights | Cloud Monitoring / Query Insights |

---

> 참고: 표의 명령어는 핵심 형태만 요약한 것이다. 실제 옵션과 최신 문법은 각 DBMS 버전 문서를 확인한다. 벤더별 차이가 큰 항목(특히 복제·PITR)은 앞선 각 챕터의 상세 설명을 함께 참고한다.
