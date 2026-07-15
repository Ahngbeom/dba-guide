# DBMS 비교표 (PostgreSQL / MySQL / Oracle / MSSQL)

이 문서는 학습서 전체에서 다룬 개념과 명령어를 DBMS별로 한눈에 비교할 수 있도록 정리한 참조표다. 특정 챕터를 학습하다가 "다른 DB에서는 이걸 뭐라고 부르지?"가 궁금할 때 이 문서를 찾아보면 된다.

## 1. 기본 용어와 아키텍처

| 개념 | PostgreSQL | MySQL | Oracle | MSSQL |
|---|---|---|---|---|
| 논리적 데이터 집합 단위 | Database > Schema | Database (= Schema) | Database > Schema (보통 사용자 = 스키마) | Database > Schema |
| 클라이언트 CLI | `psql` | `mysql` | `sqlplus` | `sqlcmd` |
| 기본 포트 | 5432 | 3306 | 1521 | 1433 |
| 프로세스 모델 | 프로세스 기반(Backend Process per Connection) | 스레드 기반 | 프로세스 기반(Background Process + Server Process) | 스레드 기반 |
| 설정 파일 | `postgresql.conf` | `my.cnf` / `my.ini` | `init.ora` / `spfile` | 레지스트리 + `sp_configure` |
| 인증 설정 | `pg_hba.conf` | `mysql.user` 테이블 + 플러그인 | `sqlnet.ora` + `LISTENER` | SQL Server Authentication / Windows Auth |

## 2. 계정 및 권한

| 개념 | PostgreSQL | MySQL | Oracle | MSSQL |
|---|---|---|---|---|
| 계정 생성 | `CREATE ROLE name LOGIN PASSWORD '...'` | `CREATE USER 'name'@'host' IDENTIFIED BY '...'` | `CREATE USER name IDENTIFIED BY ...` | `CREATE LOGIN name WITH PASSWORD='...'` |
| 권한 부여 | `GRANT SELECT ON t TO role` | `GRANT SELECT ON db.t TO 'user'@'host'` | `GRANT SELECT ON t TO user` | `GRANT SELECT ON t TO user` |
| 권한 회수 | `REVOKE ... FROM role` | `REVOKE ... FROM 'user'@'host'` | `REVOKE ... FROM user` | `REVOKE ... FROM user` |
| 권한 목록 조회 | `\du`, `information_schema.role_table_grants` | `SHOW GRANTS FOR 'user'@'host'` | `SELECT * FROM DBA_ROLE_PRIVS` | `sys.database_permissions` |
| 롤(Role) 개념 | 있음 (User = Role의 일종) | 8.0+부터 지원 | 있음 | 있음 |

## 3. 세션 / 프로세스 모니터링

| 개념 | PostgreSQL | MySQL | Oracle | MSSQL |
|---|---|---|---|---|
| 현재 세션 조회 | `SELECT * FROM pg_stat_activity` | `SHOW PROCESSLIST` / `SHOW FULL PROCESSLIST` | `SELECT * FROM v$session` | `sys.dm_exec_sessions` |
| 세션 강제 종료 | `SELECT pg_terminate_backend(pid)` | `KILL <id>` | `ALTER SYSTEM KILL SESSION 'sid,serial#'` | `KILL <spid>` |
| 실행계획 확인 | `EXPLAIN (ANALYZE, BUFFERS)` | `EXPLAIN` / `EXPLAIN ANALYZE`(8.0.18+) | `EXPLAIN PLAN FOR` + `SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY)` | `SET SHOWPLAN_XML ON` |
| 슬로우 쿼리 로그 | `log_min_duration_statement` | `slow_query_log` + `long_query_time` | AWR / ASH (Enterprise) | Query Store |
| 통계 뷰 | `pg_stat_statements` (확장) | `performance_schema` | AWR, `v$sql` | Query Store, DMV |

## 4. 백업 / 복구

| 개념 | PostgreSQL | MySQL | Oracle | MSSQL |
|---|---|---|---|---|
| 논리 백업 | `pg_dump` / `pg_dumpall` | `mysqldump` | `expdp` (Data Pump) | `BACKUP DATABASE` (또는 `bcp`) |
| 논리 복원 | `pg_restore` / `psql` | `mysql < dump.sql` | `impdp` | `RESTORE DATABASE` |
| 물리 백업 | `pg_basebackup`, WAL 아카이빙 | `Percona XtraBackup`, 파일시스템 스냅샷 | RMAN | `BACKUP DATABASE` (Full/Diff/Log) |
| PITR(시점 복구) | WAL + `recovery_target_time` | Binlog 기반 복구 | RMAN + Archive Log | 로그 백업 체인 + `STOPAT` |

## 5. 복제 / 고가용성

| 개념 | PostgreSQL | MySQL | Oracle | MSSQL |
|---|---|---|---|---|
| 복제 방식 | Streaming Replication (WAL 기반) | Binlog 기반 복제 (Row/Statement/Mixed) | Data Guard (Redo 기반) | Always On Availability Groups |
| 동기/비동기 | 둘 다 지원 (`synchronous_commit`) | 둘 다 지원 (`semi-sync` 플러그인) | 둘 다 지원 (Maximum Availability/Performance) | 둘 다 지원 |
| 자동 페일오버 도구 | Patroni, repmgr | MySQL InnoDB Cluster (Group Replication), Orchestrator | Data Guard Broker, RAC | Always On Failover Cluster Instance |
| 커넥션 풀러 | PgBouncer, Pgpool-II | ProxySQL | Oracle Connection Manager | SQL Server 자체 풀링 + ODBC |

## 6. 클라우드 매니지드 서비스 대응표

| 개념 | AWS | GCP | Azure |
|---|---|---|---|
| 관리형 RDBMS | RDS (PostgreSQL/MySQL/Oracle/SQL Server), Aurora | Cloud SQL, AlloyDB(PostgreSQL 호환) | Azure Database for PostgreSQL/MySQL, Azure SQL Database |
| 파라미터 설정 | 파라미터 그룹 (Parameter Group) | 플래그 (Database Flags) | 서버 매개변수 |
| 스냅샷/백업 | 자동 스냅샷 + 수동 스냅샷 | 자동 백업 + 온디맨드 백업 | 자동 백업 + 장기 보존 |
| 읽기 확장 | 읽기 전용 복제본 (Read Replica) | 읽기 전용 복제본 | 읽기 전용 복제본 |
| 글로벌/멀티리전 | Aurora Global Database | Cross-Region Replica | Azure SQL Auto-failover Group |
| 서버리스 | Aurora Serverless v2 | Cloud SQL(자동 확장 일부 지원) | Azure SQL Database Serverless |

## 이 표 사용법

- 특정 DBMS의 명령어가 기억나지 않을 때 개념(행) 기준으로 찾는다.
- 다른 DBMS로 이직하거나 멀티 벤더 환경을 운영해야 할 때, 같은 개념이 어떤 이름으로 불리는지 빠르게 대조한다.
- 각 챕터 본문에서 더 자세한 설명과 실습 예제를 확인한다.
