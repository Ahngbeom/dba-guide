# 복제 기초

## 핵심 개념 설명

복제(Replication)는 하나의 DB(primary/master)의 데이터를 하나 이상의 다른 DB(replica/standby/slave)로 실시간에 가깝게 복사하는 기술이다. 복제를 도입하는 이유는 크게 세 가지다.

1. **고가용성(HA)**: primary가 죽으면 replica를 승격(failover)해 서비스 중단을 최소화한다.
2. **읽기 분산(Read Scaling)**: 조회 트래픽을 여러 replica로 분산해 primary 부하를 낮춘다.
3. **재해 복구(DR)**: 원격지에 replica를 두어 데이터센터 단위 장애에 대비한다.

### 동기 vs 비동기 복제

- **비동기 복제(Asynchronous)**: primary가 커밋을 반환한 뒤 변경 로그를 replica로 보낸다. 성능 영향이 적지만, primary가 죽는 순간 아직 전송되지 않은 트랜잭션은 **유실**될 수 있다(RPO > 0). 대부분의 읽기 분산·DR 구성이 여기에 해당한다.
- **동기 복제(Synchronous)**: primary가 replica의 수신(또는 기록) 확인을 받은 후에야 커밋을 완료한다. 데이터 유실이 없지만(RPO=0), replica 응답을 기다리므로 쓰기 지연이 늘고, replica 장애 시 primary 쓰기가 멈출 수 있다.

실무에서는 "가까운 replica 1대는 동기, 나머지는 비동기"처럼 혼합해 정합성과 성능을 절충하기도 한다.

### 복제 지연(Replication Lag)

비동기 복제에서는 replica가 primary보다 뒤처지는 지연이 생긴다. 지연이 큰 상태에서 replica로 조회하면 **오래된 데이터**를 읽을 수 있다(read-after-write 불일치). 지연은 반드시 모니터링해야 하는 지표다.

### 각 DBMS의 복제 방식

<!-- dbms:postgresql -->
- **PostgreSQL — Streaming Replication**: primary의 WAL을 replica로 스트리밍해 재생한다. 물리 복제이며, 동기/비동기 모두 지원. 논리 복제(Logical Replication)는 특정 테이블만 복제하거나 버전 간 복제에 쓴다.
<!-- /dbms:postgresql -->
<!-- dbms:mysql -->
- **MySQL — Binlog 기반 복제**: primary의 바이너리 로그(binlog)를 replica가 받아 relay log에 저장하고 재생한다. GTID 기반 복제가 현대적 표준이며, failover와 복제 재구성이 쉽다.
<!-- /dbms:mysql -->
<!-- dbms:oracle -->
- **Oracle — Data Guard**: primary(운영 DB)의 redo를 standby로 전송·적용한다. Physical Standby(블록 단위 복제)와 Logical Standby(SQL 재실행)가 있고, 보호 모드(Maximum Protection/Availability/Performance)로 동기성 수준을 선택한다.
<!-- /dbms:oracle -->

## 주요 명령어/문법

<!-- dbms:postgresql -->
### PostgreSQL — Streaming Replication

```ini
# primary: postgresql.conf
wal_level = replica
max_wal_senders = 10
# 동기 복제를 원하면:
# synchronous_standby_names = 'standby1'
```
```bash
# primary: 복제 전용 롤과 pg_hba.conf 허용 후, replica에서 base backup으로 초기화
pg_basebackup -h primary_host -U replicator -D $PGDATA -Fp -Xs -R -P
# -R: standby.signal 및 primary_conninfo 자동 생성
# replica 기동 후 복제 상태 확인 (primary에서)
```
```sql
SELECT client_addr, state, sync_state,
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes
FROM pg_stat_replication;
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
### MySQL — Binlog(GTID) 복제

```ini
# primary: my.cnf
server-id = 1
log_bin = mysql-bin
gtid_mode = ON
enforce_gtid_consistency = ON
```
```sql
-- primary: 복제 계정 생성
CREATE USER 'repl'@'%' IDENTIFIED BY 'pw';
GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%';

-- replica: primary 지정 후 시작
CHANGE REPLICATION SOURCE TO
  SOURCE_HOST='primary_host', SOURCE_USER='repl', SOURCE_PASSWORD='pw',
  SOURCE_AUTO_POSITION=1;   -- GTID 자동 위치
START REPLICA;

-- 복제 상태/지연 확인
SHOW REPLICA STATUS\G   -- Seconds_Behind_Source, Replica_IO/SQL_Running 확인
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
### Oracle — Data Guard (개요)

```sql
-- standby 구성 후 로그 적용 시작(간략화)
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT FROM SESSION;
-- 복제/적용 지연 확인
SELECT name, value FROM v$dataguard_stats WHERE name LIKE '%lag%';
```
<!-- /dbms:oracle -->

<!-- dbms:mysql -->
## 실습 예제 — MySQL 비동기 복제 구성 흐름

시나리오: primary 1대 + replica 1대로 읽기 분산 구성.

```bash
# 1) primary: my.cnf에 server-id, log_bin, gtid_mode 설정 후 재시작
# 2) primary: 복제 계정 생성 (위 GRANT 참고)

# 3) primary 데이터를 replica로 초기 이관 (GTID 정보 포함)
mysqldump --single-transaction --set-gtid-purged=ON --all-databases > dump.sql
# replica에서 적재
mysql < dump.sql
```
```sql
-- 4) replica: 복제 시작
CHANGE REPLICATION SOURCE TO
  SOURCE_HOST='10.0.0.1', SOURCE_USER='repl',
  SOURCE_PASSWORD='pw', SOURCE_AUTO_POSITION=1;
START REPLICA;

-- 5) 상태 점검: 두 스레드 모두 Yes, 지연 0에 수렴하는지 확인
SHOW REPLICA STATUS\G
--   Replica_IO_Running: Yes
--   Replica_SQL_Running: Yes
--   Seconds_Behind_Source: 0

-- 6) primary에서 쓰기 → replica에서 조회로 반영 확인
```

**failover 시 주의**: 비동기 구성에서 primary를 강제 승격하면 미전송 트랜잭션이 유실될 수 있다. 승격 후에는 애플리케이션의 쓰기 대상(엔드포인트)을 새 primary로 바꾸고, 기존 primary는 복구 후 새 replica로 재편입한다.
<!-- /dbms:mysql -->

<!-- dbms:postgresql -->
## 실습 예제 — PostgreSQL 스트리밍 복제 구성 흐름

시나리오: primary 1대 + replica 1대로 읽기 분산 구성.

```ini
# 1) primary: postgresql.conf 설정 후 재시작
wal_level = replica
max_wal_senders = 10
```
```bash
# 2) primary: 복제 전용 롤 생성, pg_hba.conf에 replica 접속 허용

# 3) replica: base backup으로 초기 데이터 이관 (standby.signal, primary_conninfo 자동 생성)
pg_basebackup -h 10.0.0.1 -U replicator -D $PGDATA -Fp -Xs -R -P
```
```bash
# 4) replica: PostgreSQL 기동 → 자동으로 primary에 접속해 WAL 스트리밍 시작
pg_ctl start -D $PGDATA
```
```sql
-- 5) 상태 점검(primary에서): state가 streaming인지, 지연이 0에 수렴하는지 확인
SELECT client_addr, state, sync_state,
       pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes
FROM pg_stat_replication;
--  state      | streaming
--  sync_state | async
--  lag_bytes  | 0

-- 6) primary에서 쓰기 → replica에서 조회로 반영 확인
```

**failover 시 주의**: 비동기 구성에서 primary가 죽으면 아직 전송되지 않은 WAL은 유실될 수 있다. `pg_ctl promote` 등으로 replica를 승격한 후에는 애플리케이션의 쓰기 대상(엔드포인트)을 새 primary로 바꾸고, 기존 primary는 복구 후 `pg_basebackup`으로 새 replica로 재편입한다.
<!-- /dbms:postgresql -->

<!-- dbms:oracle -->
## 실습 예제 — Oracle Data Guard 구성 개요

시나리오: primary(운영 DB) 1대 + physical standby 1대로 DR 구성. Data Guard는 리스너·TNS·redo transport 설정이 얽혀 있어 전체 절차를 다 다루기보다, 핵심 단계와 확인 방법 위주로 개요를 잡는다.

```text
1) primary: FORCE LOGGING, ARCHIVELOG 모드 활성화
2) primary: standby redo log 그룹 추가
3) primary: standby로 백업(RMAN duplicate 또는 수동 복사)으로 초기 데이터 이관
4) standby: 별도 인스턴스로 기동 후 MOUNT 상태 유지
```
```sql
-- 5) standby: 관리형 복구 시작 (redo 수신·적용)
ALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT FROM SESSION;
```
```sql
-- 6) 상태 점검(primary 또는 standby에서): apply lag이 0에 수렴하는지 확인
SELECT name, value FROM v$dataguard_stats WHERE name LIKE '%lag%';
--  name        VALUE
--  apply lag   +00 00:00:00

-- 아카이브 전송 상태도 함께 확인
SELECT dest_id, status, error FROM v$archive_dest_status WHERE status != 'INACTIVE';
```
```sql
-- 7) primary에서 쓰기(commit) 후 standby에서 조회로 반영 확인 (물리 standby는 읽기 전용 조회 시 Active Data Guard 라이선스 필요)
```

**failover 시 주의**: Maximum Performance 모드(비동기)에서는 primary 장애 시 아직 전송되지 않은 redo가 유실될 수 있다. `ALTER DATABASE ACTIVATE STANDBY DATABASE` 등으로 standby를 승격(스위치오버/페일오버)한 후에는 애플리케이션 접속 정보(TNS)를 새 primary로 전환하고, 기존 primary는 복구 후 새 standby로 재편입한다.
<!-- /dbms:oracle -->

## 체크리스트

- [ ] 복제를 도입하는 세 가지 목적(HA, 읽기 분산, DR)을 설명할 수 있다.
- [ ] 동기 복제와 비동기 복제의 RPO/성능 트레이드오프를 설명할 수 있다.
- [ ] 복제 지연(lag)의 의미와 read-after-write 불일치 위험을 이해한다.
- [ ] PostgreSQL Streaming Replication, MySQL binlog(GTID) 복제, Oracle Data Guard의 개념을 구분한다.
<!-- dbms:postgresql -->
- [ ] PostgreSQL에서 `pg_basebackup -R`로 standby를 초기화하고 `pg_stat_replication`으로 상태를 확인할 수 있다.
<!-- /dbms:postgresql -->
<!-- dbms:mysql -->
- [ ] MySQL에서 GTID 기반 복제를 구성하고 `SHOW REPLICA STATUS`로 지연을 확인할 수 있다.
<!-- /dbms:mysql -->
- [ ] failover 시 데이터 유실 가능성과 애플리케이션 엔드포인트 전환의 필요성을 안다.
