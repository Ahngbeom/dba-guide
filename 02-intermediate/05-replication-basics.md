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

- **PostgreSQL — Streaming Replication**: primary의 WAL을 replica로 스트리밍해 재생한다. 물리 복제이며, 동기/비동기 모두 지원. 논리 복제(Logical Replication)는 특정 테이블만 복제하거나 버전 간 복제에 쓴다.

## 주요 명령어/문법

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


## 체크리스트

- [ ] 복제를 도입하는 세 가지 목적(HA, 읽기 분산, DR)을 설명할 수 있다.
- [ ] 동기 복제와 비동기 복제의 RPO/성능 트레이드오프를 설명할 수 있다.
- [ ] 복제 지연(lag)의 의미와 read-after-write 불일치 위험을 이해한다.
- [ ] PostgreSQL Streaming Replication, MySQL binlog(GTID) 복제, Oracle Data Guard의 개념을 구분한다.
- [ ] PostgreSQL에서 `pg_basebackup -R`로 standby를 초기화하고 `pg_stat_replication`으로 상태를 확인할 수 있다.
- [ ] failover 시 데이터 유실 가능성과 애플리케이션 엔드포인트 전환의 필요성을 안다.
