# 02. 고가용성과 페일오버 (HA & Failover)

## 1. 핵심 개념 설명

**고가용성(HA, High Availability)**은 "구성 요소 하나가 죽어도 서비스가 계속되는" 성질이다. DB는 상태(state)를 가진 컴포넌트이기 때문에, 웹 서버처럼 그냥 인스턴스를 여러 개 띄운다고 HA가 되지 않는다. **데이터를 잃지 않으면서 리더(primary)를 교체**하는 문제가 핵심이며, 여기서 복제·합의·펜싱(fencing) 같은 어려운 주제가 등장한다.

HA를 설계할 때 반드시 구분해야 할 것은 **HA와 DR의 차이**다. HA는 보통 같은 리전/데이터센터 안에서 초~분 단위로 자동 복구하는 것을 목표로 하고(가용영역 장애까지 대응), DR(다음 챕터)은 리전 전체가 사라지는 재해를 사람이 개입해 더 긴 시간에 복구하는 것을 목표로 한다. 둘은 목적도 비용도 다르며, HA가 있다고 DR이 되는 것은 아니다.

### 대표 HA 아키텍처 패턴

- **Active-Standby (Primary-Replica)**: 쓰기를 받는 Primary 1대 + 대기 중인 Standby 1대 이상. Primary 장애 시 Standby를 승격(promote). 가장 널리 쓰이는 기본형.
- **Multi-AZ (클라우드 매니지드)**: RDS/Cloud SQL 등에서 서로 다른 가용영역(AZ)에 동기 복제된 Standby를 두고, 벤더가 자동 페일오버를 관리. 운영 부담이 가장 낮다.
- **Patroni + etcd/Consul (PostgreSQL)**: 분산 합의 저장소로 리더 선출을 관리하는 오픈소스 표준 조합. 셀프 매니지드 PostgreSQL HA의 사실상 표준.
- **Pacemaker + Corosync**: 범용 클러스터 리소스 관리자. VIP 이동, 펜싱(STONITH) 등 세밀한 제어가 가능하지만 복잡하다.
- **MySQL InnoDB Cluster (Group Replication) / Galera**: 다중 노드 합의 기반 복제. 준동기~동기 특성으로 데이터 손실을 줄인다.

### 자동 페일오버의 핵심 난제
1. **장애 오탐(false positive)**: 네트워크 순단을 장애로 오인해 불필요한 페일오버를 하면 오히려 서비스가 흔들린다. → 헬스체크 임계값·재시도 설계가 중요.
2. **스플릿 브레인(split-brain)**: 옛 Primary가 죽지 않았는데 새 Primary가 뜨면 **쓰기가 두 곳에서 발생**해 데이터가 갈린다. 이를 막기 위해 **쿼럼(quorum)**과 **펜싱/STONITH**(옛 리더를 강제로 격리·차단)가 필수다.
3. **데이터 손실 vs 가용성**: 비동기 복제에서 페일오버하면 미전송 트랜잭션이 유실될 수 있다(RPO > 0). 동기 복제는 손실을 없애지만 지연·가용성 비용이 든다.

---

## 2. 주요 명령어/문법

### PostgreSQL — 스트리밍 복제 & 승격
```bash
# Standby 구성 (베이스백업)
pg_basebackup -h primary-host -D /var/lib/pgsql/data -U replicator -R -X stream

# 수동 페일오버: Standby를 Primary로 승격
pg_ctl promote -D /var/lib/pgsql/data
# 또는
SELECT pg_promote();
```
```ini
# 동기 복제로 데이터 손실 방지 (postgresql.conf on primary)
synchronous_standby_names = 'FIRST 1 (standby1, standby2)'
synchronous_commit = on
```

**Patroni 상태 확인 (셀프 매니지드 표준)**
```bash
patronictl -c /etc/patroni.yml list          # 클러스터 멤버/리더/lag 확인
patronictl -c /etc/patroni.yml switchover     # 계획된 무중단 전환
patronictl -c /etc/patroni.yml failover       # 비상 페일오버
```

### MySQL — 복제 & 페일오버
```sql
-- 복제 상태/지연 확인
SHOW REPLICA STATUS\G   -- Seconds_Behind_Source 주목

-- 준동기 복제로 손실 최소화
INSTALL PLUGIN rpl_semi_sync_source SONAME 'semisync_source.so';
SET GLOBAL rpl_semi_sync_source_enabled = 1;
```
```bash
# MHA / Orchestrator로 토폴로지 관리·자동 페일오버 (운영 도구)
orchestrator-client -c graceful-master-takeover -i old-primary:3306
```

### Oracle — Data Guard
```sql
-- 관측/전환 (Broker 사용 시 dgmgrl)
DGMGRL> SHOW CONFIGURATION;
DGMGRL> SWITCHOVER TO 'standby_db';   -- 계획된 무손실 전환
DGMGRL> FAILOVER TO 'standby_db';     -- 비상 페일오버
-- Maximum Availability/Protection 모드로 RPO=0 보장 가능
```

### 클라우드 매니지드 (AWS RDS)
```bash
# Multi-AZ 강제 페일오버 테스트 (재부팅 시 AZ 전환)
aws rds reboot-db-instance --db-instance-identifier proddb --force-failover
```

---

## 3. 실습 예제

**시나리오: "3-AZ Multi-AZ PostgreSQL 클러스터에서 Primary가 위치한 AZ 장애 → 페일오버 시뮬레이션"**

구성: `AZ-a`(Primary), `AZ-b`(동기 Standby), `AZ-c`(비동기 Standby, 리드용). Patroni + etcd(각 AZ에 1개씩, 쿼럼 3) 기반.

1. **정상 상태 확인**
   ```bash
   patronictl list
   # + Cluster: pg (leader: node-a) | node-a Leader | node-b Sync Standby lag 0 | node-c Replica lag 12KB
   ```

2. **장애 주입**: `AZ-a` 네트워크를 차단해 Primary를 격리한다(실제 훈련에서는 방화벽 규칙 또는 인스턴스 중지).

3. **자동 감지·선출**: etcd 리더 리스가 만료되면 Patroni가 리더 부재를 감지한다. 쿼럼(3개 중 2개 생존)이 유지되므로 스플릿 브레인 없이 선출 가능. **동기 Standby였던 `node-b`**를 승격(무손실).

4. **펜싱 확인**: 격리된 옛 `node-a`는 리더 리스를 못 얻어 자동으로 read-only demote → 스플릿 브레인 방지. 애플리케이션은 VIP/서비스 디스커버리(예: HAProxy가 Patroni REST `/primary` 헬스체크로 라우팅)를 통해 새 Primary로 자동 재연결.

5. **검증 지표 기록**: 감지~승격까지의 **실제 다운타임(예: 18초)**, 유실 트랜잭션 수(동기 구성이므로 0), 애플리케이션 재연결 완료 시각을 기록한다. 이 숫자가 곧 SLA 근거가 된다.

6. **복구**: 옛 `node-a`를 새 Primary의 Standby로 재편입(`pg_rewind`로 분기 지점 정합).
   ```bash
   pg_rewind --target-pgdata=/data --source-server="host=node-b ..."
   ```

### 페일오버 Runbook 작성법과 예시

Runbook은 **새벽 3시에 당황한 온콜 담당자가 그대로 따라 할 수 있어야** 한다. 다음 골격을 권한다.

```markdown
# Runbook: PostgreSQL Primary 페일오버

## 1. 발동 조건 (언제 이 문서를 여는가)
- Primary 헬스체크 5회 연속 실패 (약 30초) AND 애플리케이션 5xx 급증
- 자동 페일오버가 60초 내 완료되지 않은 경우 → 수동 개입

## 2. 사전 확인 (섣부른 승격 금지)
- [ ] 옛 Primary가 정말 죽었는가? (스플릿 브레인 방지 위해 반드시 확인)
- [ ] 승격 대상 Standby의 복제 지연(lag)은? 0에 가까운 노드 선택
- [ ] etcd/쿼럼 상태 정상인가?

## 3. 실행 절차
1. `patronictl list` 로 후보 확인
2. (자동 실패 시) `patronictl failover --candidate node-b`
3. 옛 Primary 강제 격리(펜싱): 인스턴스 중지 또는 네트워크 차단
4. 애플리케이션 커넥션 풀 재시작/헬스체크 통과 확인

## 4. 검증
- [ ] 새 Primary에 쓰기 성공 (`SELECT pg_is_in_recovery()` = false)
- [ ] 애플리케이션 에러율 정상화
- [ ] 데이터 정합성 스팟 체크

## 5. 사후
- 옛 노드 Standby 재편입 (pg_rewind)
- 인시던트 채널 공유 + 포스트모템 티켓 생성 (08장 참조)

## 6. 롤백 / 에스컬레이션
- 승격 후에도 장애 지속 → DBA 리드 호출, DR 절차(03장) 검토
```

> **트레이드오프 메모**: 자동 페일오버는 빠르지만 오탐 위험이 있고, 수동은 안전하지만 느리다. 성숙한 조직은 "자동으로 하되, 애매하면 사람에게 넘기는" 임계값(타임아웃·재시도·쿼럼)을 정교하게 튜닝한다. 처음에는 **자동 감지 + 수동 승격**으로 시작해 신뢰가 쌓이면 자동화 범위를 넓히는 것이 안전하다.

---

## 4. 체크리스트

- [ ] HA와 DR의 목적·시간척도·비용 차이를 설명할 수 있다.
- [ ] Active-Standby, Multi-AZ, Patroni, Pacemaker 등 패턴의 장단점을 비교할 수 있다.
- [ ] 스플릿 브레인이 발생하는 원리와 쿼럼/펜싱(STONITH)으로 막는 방법을 설명할 수 있다.
- [ ] 동기 vs 비동기 복제가 페일오버 시 RPO에 미치는 영향을 판단할 수 있다.
- [ ] 자동 페일오버의 오탐을 줄이는 헬스체크·타임아웃 설계를 할 수 있다.
- [ ] 계획된 switchover와 비상 failover의 차이와 각 절차를 수행할 수 있다.
- [ ] 온콜 담당자가 그대로 따라 할 수 있는 페일오버 Runbook을 작성할 수 있다.
- [ ] 페일오버 후 옛 Primary를 Standby로 안전하게 재편입(pg_rewind 등)할 수 있다.
