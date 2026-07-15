# 09. 고급 단계 명령어 치트시트

고급 단계(01~08)에서 다룬 개념과 명령을 DBMS·클라우드별로 한눈에 대조한다. 상세 맥락은 각 챕터를 참조.

> 표기: `—` 는 해당 엔진에 직접 대응이 없거나 방식이 크게 다름을 뜻한다.

---

## 1. 성능 튜닝 (01장)

| 작업 | PostgreSQL | MySQL | Oracle |
|------|-----------|-------|--------|
| 실행 계획(추정) | `EXPLAIN SELECT ...` | `EXPLAIN FORMAT=JSON SELECT ...` | `EXPLAIN PLAN FOR ...` + `DBMS_XPLAN.DISPLAY` |
| 실행 계획(실측) | `EXPLAIN (ANALYZE, BUFFERS) ...` | `EXPLAIN ANALYZE ...` | `DBMS_XPLAN.DISPLAY_CURSOR(...,'ALLSTATS LAST')` |
| 통계 갱신 | `ANALYZE tbl;` | `ANALYZE TABLE tbl;` | `DBMS_STATS.GATHER_TABLE_STATS(...)` |
| 통계 정밀도 상향 | `ALTER TABLE ... SET STATISTICS n` | (히스토그램 자동) | `METHOD_OPT => 'FOR COLUMNS ...'` |
| 버퍼 캐시 크기 | `shared_buffers` | `innodb_buffer_pool_size` | `SGA_TARGET`/`MEMORY_TARGET` |
| 캐시 힌트(옵티마이저) | `effective_cache_size` | — | — |
| Range 파티션 | `PARTITION BY RANGE (col)` + `PARTITION OF` | `PARTITION BY RANGE (...)` | `PARTITION BY RANGE (...) INTERVAL(...)` |
| 파티션 삭제 | `DROP TABLE part;` | `ALTER TABLE ... DROP PARTITION p;` | `ALTER TABLE ... DROP PARTITION p;` |

**커넥션 풀 (외부 도구)**: PostgreSQL → PgBouncer(`pool_mode=transaction`), MySQL → ProxySQL, Oracle → 내장 세션 풀/`DRCP`.

---

## 2. HA & 페일오버 (02장)

| 작업 | PostgreSQL | MySQL | Oracle |
|------|-----------|-------|--------|
| 복제 구성 | `pg_basebackup -R` (스트리밍) | `CHANGE REPLICATION SOURCE ...` | Data Guard (물리 Standby) |
| 복제 상태 | `SELECT * FROM pg_stat_replication;` | `SHOW REPLICA STATUS\G` | `DGMGRL> SHOW CONFIGURATION;` |
| 동기/무손실 복제 | `synchronous_standby_names` | 준동기 `rpl_semi_sync_source` | Max Availability/Protection 모드 |
| 수동 승격 | `pg_ctl promote` / `SELECT pg_promote();` | (도구) `orchestrator ... takeover` | `DGMGRL> SWITCHOVER/FAILOVER` |
| 클러스터 관리 도구 | Patroni(+etcd), Pacemaker | InnoDB Cluster, Orchestrator, MHA | Data Guard Broker(`dgmgrl`) |
| 페일오버 명령 | `patronictl failover` | `orchestrator ... failover` | `DGMGRL> FAILOVER TO ...` |
| 옛 Primary 재편입 | `pg_rewind` | `GTID` 기반 재구성 | Flashback / 재빌드 |
| 클라우드 강제 페일오버 | AWS: `reboot-db-instance --force-failover` | 동일 | 동일 |

---

## 3. DR / 백업·복구 (03장)

| 작업 | PostgreSQL | MySQL | Oracle |
|------|-----------|-------|--------|
| WAL/로그 아카이빙 | `archive_mode=on`, `archive_command` | binlog (`log_bin`) | Archived Redo Log |
| PITR 목표 시점 | `recovery_target_time` + `recovery.signal` | `mysqlbinlog --stop-datetime` | RMAN `SET UNTIL TIME` |
| 시점 되감기 | (PITR 복원) | (binlog 재적용) | `FLASHBACK DATABASE TO TIMESTAMP` |
| 물리 백업 도구 | `pg_basebackup`, pgBackRest | Percona XtraBackup | RMAN |
| 크로스리전 리플리카(AWS) | `create-db-instance-read-replica --region` | 동일 | 동일(또는 Data Guard 원격) |
| 리플리카 승격(AWS) | `promote-read-replica` | 동일 | `DGMGRL> FAILOVER` |
| 자동백업 크로스리전복제 | `start-db-instance-automated-backups-replication` | 동일 | — |

**개념**: RPO(허용 데이터 손실)=복제/백업 주기가 결정 · RTO(허용 복구 시간)=대기 인프라 상태가 결정 · Cold/Warm/Hot Standby 스펙트럼 · DR Drill 정기 수행.

---

## 4. 확장 & 샤딩 (04장)

| 작업 | PostgreSQL | MySQL | Oracle |
|------|-----------|-------|--------|
| 리드 리플리카 지연 | `pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)` | `Seconds_Behind_Source` | Data Guard `APPLY LAG` |
| 읽기/쓰기 분리 | 드라이버/PgBouncer/앱 라우팅 | ProxySQL query rules | 앱 라우팅 / ADG |
| 분산/샤딩 확장 | Citus (`create_distributed_table`) | Vitess (VSchema) | Sharding (`GDSCTL`) |
| 커넥션 풀 도구 | PgBouncer | ProxySQL | DRCP / 내장 풀 |

**샤드 키 원칙**: 고른 분산 + 대부분 쿼리가 단일 샤드 처리 + 낮은 리샤딩 빈도. 확장 사다리: 수직 확장 → 리드 리플리카 → 샤딩(최후).

---

## 5. 보안 & 컴플라이언스 (05장)

| 작업 | PostgreSQL | MySQL | Oracle |
|------|-----------|-------|--------|
| 전송 암호화 강제 | `hostssl` (pg_hba) + `sslmode=verify-full` | `ALTER USER ... REQUIRE SSL/X509` | TCPS + wallet |
| 저장 암호화(TDE) | 볼륨/FS 암호화, `pgcrypto`(컬럼) | `ALTER TABLE ... ENCRYPTION='Y'` | `... ENCRYPT` (테이블스페이스/컬럼) |
| 클라우드 저장 암호화 | AWS `--storage-encrypted --kms-key-id` / GCP CMEK | 동일 | 동일 |
| 감사 로그 | pgAudit (`pgaudit.log`) | Enterprise/MariaDB audit plugin | Unified Auditing (`CREATE AUDIT POLICY`) |
| 컬럼 암호화 | `pgp_sym_encrypt/decrypt` | 함수 `AES_ENCRYPT` / 앱단 | 네이티브 컬럼 `ENCRYPT` |
| 최소 권한 역할 | `CREATE ROLE ...; GRANT ...` | `CREATE ROLE; GRANT` | `CREATE ROLE; GRANT` |

**개념**: 방어 심화(저장/전송/접근 계층별) · 키 관리·회전(KMS/HSM) · 데이터 등급 분류 · 마스킹 · GDPR/개인정보보호법(잊힐 권리, 데이터 주권).

---

## 6. 자동화 & IaC (06장)

| 도구 | 역할 | 핵심 명령/개념 |
|------|------|--------------|
| Terraform | 프로비저닝(리소스 존재/형상) | `terraform plan`(드라이런) → `apply`, `prevent_destroy`, 원격 state+잠금 |
| Ansible | 구성(서버 내부 상태) | `ansible-playbook --check --diff`(드라이런), 멱등성, handler |
| GitOps | 변경 관리 흐름 | PR → 자동 `plan` → 리뷰 → 머지 → 자동 `apply`, main=진실의 원천 |
| 비밀 관리 | 코드 분리 | Vault / KMS / Secrets Manager 주입 |

**원칙**: 멱등성 · 드라이런 우선 · 비밀 분리 · 파괴적 작업 가드 · 드리프트 감지 · 운영/DR 동일 모듈 재현.

---

## 7. 클라우드 매니지드 심화 (07장)

| 작업 | AWS (Aurora) | GCP (AlloyDB / Cloud SQL) |
|------|-------------|--------------------------|
| 클러스터 생성 | `aws rds create-db-cluster --engine aurora-postgresql` | `gcloud alloydb clusters create` |
| Serverless | `--serverless-v2-scaling-configuration Min=..,Max=..` | AlloyDB 자동/Cloud SQL 스케일 |
| 리드 리플리카/풀 | `create-db-instance`(리더) / reader 엔드포인트 | `instances create --instance-type=READ_POOL` |
| 글로벌 DR | `create-global-cluster` / `failover-global-cluster` | Cloud SQL 크로스리전 리플리카 |
| I/O 비용 최적화 | `--storage-type aurora-iopt1` (I/O-Optimized) | 티어/디스크 선택 |
| 접속 | writer/reader 엔드포인트, `sslmode=require` | 표준 psql/mysql 엔드포인트 |

**개념**: 컴퓨트-스토리지 분리 → 빠른 페일오버·저지연 리플리카·빠른 복원 · FinOps(예약 vs Serverless, I/O 모델, 전송비) · 벤더 종속 인지.

---

## 8. 장애 대응 진단 (08장, 모두 읽기 전용)

| 작업 | PostgreSQL | MySQL | Oracle |
|------|-----------|-------|--------|
| 활성 세션/쿼리 | `SELECT ... FROM pg_stat_activity` | `performance_schema.processlist` | `v$session (status='ACTIVE')` |
| 블로킹 확인 | `pg_locks` 조인 | `sys.innodb_lock_waits` | `v$session_blockers` |
| 복제 지연 | `pg_stat_replication` | `SHOW REPLICA STATUS` | `v$dataguard_stats` |
| 세션 종료(완화) | `pg_terminate_backend(pid)` | `KILL id` | `ALTER SYSTEM KILL SESSION` |
| 장기쿼리 방어 | `statement_timeout` | `MAX_EXECUTION_TIME` | Resource Manager |

**프로세스**: 탐지→대응→복구→회고 · IC/Ops/Comms 역할 · 완화 우선 · 비난 없는 포스트모템(타임라인·5 Whys·담당자·기한).

---

## 부록: 핵심 개념 용어 대조

| 개념 | 한 줄 정의 |
|------|-----------|
| RPO | 허용 가능한 데이터 손실량(시간). 복제/백업 주기가 결정 |
| RTO | 허용 가능한 복구 소요 시간. 대기 인프라 상태가 결정 |
| HA | 같은 리전 내 자동·단시간 복구(가용영역 장애 대응) |
| DR | 리전급 재해에서 사람이 개입해 복구 |
| Split-brain | Primary가 둘이 되어 쓰기가 갈리는 상태(쿼럼·펜싱으로 방지) |
| Fencing/STONITH | 옛 리더를 강제 격리해 스플릿 브레인 차단 |
| Quorum | 과반 합의로 리더 선출·일관성 보장 |
| Replication Lag | 리플리카가 Primary를 따라잡지 못한 지연 |
| Partition Pruning | 파티션 중 필요한 것만 스캔하는 최적화 |
| TDE | 저장 데이터 투명 암호화(스토리지 계층) |
| Idempotency | 여러 번 실행해도 결과가 같은 성질(자동화 안전성) |
| Blameless Postmortem | 사람 대신 시스템을 개선하는 사후분석 |
