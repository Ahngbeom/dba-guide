# 02. 고가용성과 페일오버 (HA & Failover)

## 1. 핵심 개념 설명

**고가용성(HA, High Availability)**은 "구성 요소 하나가 죽어도 서비스가 계속되는" 성질이다. DB는 상태(state)를 가진 컴포넌트이기 때문에, 웹 서버처럼 그냥 인스턴스를 여러 개 띄운다고 HA가 되지 않는다. **데이터를 잃지 않으면서 리더(primary)를 교체**하는 문제가 핵심이며, 여기서 복제·합의·펜싱(fencing) 같은 어려운 주제가 등장한다.

HA를 설계할 때 반드시 구분해야 할 것은 **HA와 DR의 차이**다. HA는 보통 같은 리전/데이터센터 안에서 초~분 단위로 자동 복구하는 것을 목표로 하고(가용영역 장애까지 대응), DR(다음 챕터)은 리전 전체가 사라지는 재해를 사람이 개입해 더 긴 시간에 복구하는 것을 목표로 한다. 둘은 목적도 비용도 다르며, HA가 있다고 DR이 되는 것은 아니다.

### 대표 HA 아키텍처 패턴

- **Active-Standby (Primary-Replica)**: 쓰기를 받는 Primary 1대 + 대기 중인 Standby 1대 이상. Primary 장애 시 Standby를 승격(promote). 가장 널리 쓰이는 기본형.
- **Multi-AZ (클라우드 매니지드)**: RDS/Cloud SQL 등에서 서로 다른 가용영역(AZ)에 동기 복제된 Standby를 두고, 벤더가 자동 페일오버를 관리. 운영 부담이 가장 낮다.
- **Pacemaker + Corosync**: 범용 클러스터 리소스 관리자. VIP 이동, 펜싱(STONITH) 등 세밀한 제어가 가능하지만 복잡하다.
- **MySQL InnoDB Cluster (Group Replication) / Galera**: 다중 노드 합의 기반 복제. 준동기~동기 특성으로 데이터 손실을 줄인다.

### 자동 페일오버의 핵심 난제
1. **장애 오탐(false positive)**: 네트워크 순단을 장애로 오인해 불필요한 페일오버를 하면 오히려 서비스가 흔들린다. → 헬스체크 임계값·재시도 설계가 중요.
2. **스플릿 브레인(split-brain)**: 옛 Primary가 죽지 않았는데 새 Primary가 뜨면 **쓰기가 두 곳에서 발생**해 데이터가 갈린다. 이를 막기 위해 **쿼럼(quorum)**과 **펜싱/STONITH**(옛 리더를 강제로 격리·차단)가 필수다.
3. **데이터 손실 vs 가용성**: 비동기 복제에서 페일오버하면 미전송 트랜잭션이 유실될 수 있다(RPO > 0). 동기 복제는 손실을 없애지만 지연·가용성 비용이 든다.

---

## 2. 주요 명령어/문법


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


### 클라우드 매니지드 (AWS RDS)
```bash
# Multi-AZ 강제 페일오버 테스트 (재부팅 시 AZ 전환)
aws rds reboot-db-instance --db-instance-identifier proddb --force-failover
```

---

## 3. 실습 예제


**시나리오: "3-노드 MySQL Group Replication(InnoDB Cluster) 클러스터에서 Primary가 위치한 AZ 장애 → Orchestrator 기반 자동 페일오버 시뮬레이션"**

구성: `AZ-a`(Primary/Writer), `AZ-b`(준동기 Secondary), `AZ-c`(비동기 Secondary, 리드용). Orchestrator가 각 노드의 복제 토폴로지를 지속적으로 감시(polling)한다.

1. **정상 상태 확인**
   ```sql
   SHOW REPLICA STATUS\G
   -- (node-b 기준) Source_Host: node-a | Replica_IO_Running: Yes | Replica_SQL_Running: Yes | Seconds_Behind_Source: 0
   ```
   준동기 복제가 정상 동작 중인지 Primary의 `rpl_semi_sync_source_status`와 Secondary의 `rpl_semi_sync_replica_status`도 함께 확인한다.

2. **장애 주입**: `AZ-a` 네트워크를 차단해 Primary를 격리한다(실제 훈련에서는 방화벽 규칙 또는 인스턴스 중지).

3. **자동 감지·선출**: Orchestrator가 폴링 주기 내 `node-a`에 연속 접속 실패를 감지하면 DeadMaster 상태로 판단하고, 준동기 복제로 지연이 가장 적었던 `node-b`를 새 Primary로 승격한다(무손실에 가까움). 계획된 점검처럼 사람이 직접 트리거하는 경우에는 다음 명령을 쓴다.
   ```bash
   orchestrator-client -c graceful-master-takeover -i old-primary:3306
   ```

4. **펜싱 확인**: 격리된 옛 `node-a`는 Orchestrator가 `SET GLOBAL read_only = ON`으로 강제 격리해 실수로 다시 쓰기가 들어오는 것을 막는다(STONITH에 준하는 소프트 펜싱). 애플리케이션은 Orchestrator가 갱신한 VIP/서비스 디스커버리(ProxySQL 등)를 통해 새 Primary로 자동 재연결한다.

5. **검증 지표 기록**: 감지~승격까지의 **실제 다운타임**, 유실 트랜잭션 수(준동기 구성이므로 이상적으로는 0, `rpl_semi_sync_source_status`가 OFF로 떨어진 시점 이후 발생한 쓰기는 유실 가능), 애플리케이션 재연결 완료 시각을 기록한다. 이 숫자가 곧 SLA 근거가 된다.

6. **복구**: 옛 `node-a`를 새 Primary(`node-b`)의 Secondary로 재편입한다(GTID 자동 위치 지정).
   ```sql
   CHANGE REPLICATION SOURCE TO
     SOURCE_HOST = 'node-b', SOURCE_USER = 'replicator',
     SOURCE_AUTO_POSITION = 1;
   START REPLICA;
   ```

### 페일오버 Runbook 작성법과 예시

Runbook은 **새벽 3시에 당황한 온콜 담당자가 그대로 따라 할 수 있어야** 한다. 다음 골격을 권한다.

```markdown
# Runbook: MySQL Primary 페일오버 (Group Replication / Orchestrator)

## 1. 발동 조건 (언제 이 문서를 여는가)
- Orchestrator 대시보드에서 Primary가 DeadMaster로 표시 AND 애플리케이션 5xx 급증
- 자동 복구(recovery)가 완료되지 않거나 비활성화된 경우 → 수동 개입

## 2. 사전 확인 (섣부른 승격 금지)
- [ ] 옛 Primary가 정말 죽었는가? (스플릿 브레인 방지 위해 반드시 확인)
- [ ] 승격 대상 Secondary의 Seconds_Behind_Source는? 0에 가까운 노드 선택
- [ ] 준동기 복제(rpl_semi_sync)가 활성화된 노드인가?

## 3. 실행 절차
1. `SHOW REPLICA STATUS\G` 로 후보 확인
2. (수동 트리거 시) `orchestrator-client -c graceful-master-takeover -i old-primary:3306`
3. 옛 Primary 강제 격리(펜싱): `SET GLOBAL read_only = ON` 또는 인스턴스 중지
4. 애플리케이션 커넥션 풀(ProxySQL 등) 재시작/헬스체크 통과 확인

## 4. 검증
- [ ] 새 Primary에 쓰기 성공 (`SHOW GLOBAL VARIABLES LIKE 'read_only'` = OFF)
- [ ] 애플리케이션 에러율 정상화
- [ ] 데이터 정합성 스팟 체크

## 5. 사후
- 옛 노드 Secondary 재편입 (`CHANGE REPLICATION SOURCE TO ... SOURCE_AUTO_POSITION = 1`)
- 인시던트 채널 공유 + 포스트모템 티켓 생성 (09장 참조)

## 6. 롤백 / 에스컬레이션
- 승격 후에도 장애 지속 → DBA 리드 호출, DR 절차(03장) 검토
```

> **트레이드오프 메모**: 준동기 복제는 Primary가 최소 1개 Secondary의 ACK를 기다리므로 커밋 지연이 늘지만 데이터 손실 위험을 크게 줄인다. Group Replication의 일관성 수준을 더 높이면 손실을 더 줄일 수도 있지만 가용성 비용이 커진다. Orchestrator의 자동 복구 역시 처음에는 **탐지만 자동화하고 승격은 수동 승인**으로 시작해 신뢰가 쌓이면 전체 자동화로 넓히는 것이 안전하다.


---

## 4. 체크리스트

- [ ] HA와 DR의 목적·시간척도·비용 차이를 설명할 수 있다.
- [ ] Active-Standby, Multi-AZ, Patroni, Pacemaker 등 패턴의 장단점을 비교할 수 있다.
- [ ] 스플릿 브레인이 발생하는 원리와 쿼럼/펜싱(STONITH)으로 막는 방법을 설명할 수 있다.
- [ ] 동기 vs 비동기 복제가 페일오버 시 RPO에 미치는 영향을 판단할 수 있다.
- [ ] 자동 페일오버의 오탐을 줄이는 헬스체크·타임아웃 설계를 할 수 있다.
- [ ] 계획된 switchover와 비상 failover의 차이와 각 절차를 수행할 수 있다.
- [ ] 온콜 담당자가 그대로 따라 할 수 있는 페일오버 Runbook을 작성할 수 있다.
