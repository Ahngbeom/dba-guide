# 02. 고가용성과 페일오버 (HA & Failover)

## 1. 핵심 개념 설명

**고가용성(HA, High Availability)**은 "구성 요소 하나가 죽어도 서비스가 계속되는" 성질이다. DB는 상태(state)를 가진 컴포넌트이기 때문에, 웹 서버처럼 그냥 인스턴스를 여러 개 띄운다고 HA가 되지 않는다. **데이터를 잃지 않으면서 리더(primary)를 교체**하는 문제가 핵심이며, 여기서 복제·합의·펜싱(fencing) 같은 어려운 주제가 등장한다.

HA를 설계할 때 반드시 구분해야 할 것은 **HA와 DR의 차이**다. HA는 보통 같은 리전/데이터센터 안에서 초~분 단위로 자동 복구하는 것을 목표로 하고(가용영역 장애까지 대응), DR(다음 챕터)은 리전 전체가 사라지는 재해를 사람이 개입해 더 긴 시간에 복구하는 것을 목표로 한다. 둘은 목적도 비용도 다르며, HA가 있다고 DR이 되는 것은 아니다.

### 대표 HA 아키텍처 패턴

- **Active-Standby (Primary-Replica)**: 쓰기를 받는 Primary 1대 + 대기 중인 Standby 1대 이상. Primary 장애 시 Standby를 승격(promote). 가장 널리 쓰이는 기본형.
- **Multi-AZ (클라우드 매니지드)**: RDS/Cloud SQL 등에서 서로 다른 가용영역(AZ)에 동기 복제된 Standby를 두고, 벤더가 자동 페일오버를 관리. 운영 부담이 가장 낮다.
- **Pacemaker + Corosync**: 범용 클러스터 리소스 관리자. VIP 이동, 펜싱(STONITH) 등 세밀한 제어가 가능하지만 복잡하다.

### 자동 페일오버의 핵심 난제
1. **장애 오탐(false positive)**: 네트워크 순단을 장애로 오인해 불필요한 페일오버를 하면 오히려 서비스가 흔들린다. → 헬스체크 임계값·재시도 설계가 중요.
2. **스플릿 브레인(split-brain)**: 옛 Primary가 죽지 않았는데 새 Primary가 뜨면 **쓰기가 두 곳에서 발생**해 데이터가 갈린다. 이를 막기 위해 **쿼럼(quorum)**과 **펜싱/STONITH**(옛 리더를 강제로 격리·차단)가 필수다.
3. **데이터 손실 vs 가용성**: 비동기 복제에서 페일오버하면 미전송 트랜잭션이 유실될 수 있다(RPO > 0). 동기 복제는 손실을 없애지만 지연·가용성 비용이 든다.

---

## 2. 주요 명령어/문법



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



**시나리오: "Data Guard Broker 기반 Primary-Standby 구성에서 계획된 switchover와 비상 failover 실습"**

구성: `primary_db`(Primary), `standby_db`(Physical Standby, Maximum Availability 모드). Data Guard Broker(DGMGRL)로 구성을 관리한다.

1. **정상 상태 확인**
   ```sql
   DGMGRL> SHOW CONFIGURATION;
   -- Configuration - dg_config | Protection Mode: MaxAvailability | primary_db - Primary database | standby_db - Physical standby database
   DGMGRL> SHOW DATABASE VERBOSE 'standby_db';
   -- Transport Lag: 0 seconds | Apply Lag: 0 seconds
   ```

2. **계획된 전환(switchover) 훈련**: 정기 점검을 위해 무중단 역할 교체를 수행한다.
   ```sql
   DGMGRL> SWITCHOVER TO 'standby_db';
   -- primary_db는 새 Standby로, standby_db는 새 Primary로 역할이 바뀐다. Maximum Availability 모드이므로 데이터 손실 없음(RPO=0).
   ```
   전환 직후 애플리케이션 커넥션(TNS/서비스)이 새 Primary로 재연결되는지 확인한다.

3. **장애 주입(비상 시나리오)**: 원래 Primary였던 노드(현재는 Standby 역할)의 인스턴스를 강제 중지해 실제 장애를 시뮬레이션한다.

4. **비상 페일오버**: Primary가 응답하지 않으면 남은 Standby로 강제 전환한다. Maximum Availability 모드라도 네트워크 단절 중 전송되지 못한 REDO는 유실될 수 있어 손실 여부를 반드시 확인해야 한다.
   ```sql
   DGMGRL> FAILOVER TO 'standby_db';
   ```

5. **검증 지표 기록**: 감지~페일오버 완료까지의 **실제 다운타임**, 유실 트랜잭션(REDO) 유무, 애플리케이션 재연결 완료 시각을 기록한다. `SHOW DATABASE VERBOSE`의 Transport/Apply Lag 이력이 SLA 근거 자료가 된다.

6. **복구**: 옛 Primary를 새 Primary의 Standby로 재편입한다.
   ```sql
   DGMGRL> REINSTATE DATABASE 'primary_db';
   ```

### 페일오버 Runbook 작성법과 예시

Runbook은 **새벽 3시에 당황한 온콜 담당자가 그대로 따라 할 수 있어야** 한다. 다음 골격을 권한다.

```markdown
# Runbook: Oracle Data Guard 페일오버

## 1. 발동 조건 (언제 이 문서를 여는가)
- Primary 헬스체크 5회 연속 실패 AND 애플리케이션 5xx 급증
- 계획된 switchover가 아닌, Primary 응답 불가로 인한 비상 상황

## 2. 사전 확인 (섣부른 페일오버 금지)
- [ ] 옛 Primary가 정말 죽었는가? (스플릿 브레인 방지 위해 반드시 확인)
- [ ] `SHOW DATABASE VERBOSE 'standby_db'`의 Apply Lag은 0에 가까운가?
- [ ] Protection Mode가 Maximum Availability인가(RPO=0 기대 가능 여부)?

## 3. 실행 절차
1. `DGMGRL> SHOW CONFIGURATION;` 로 상태 확인
2. (계획된 경우) `DGMGRL> SWITCHOVER TO 'standby_db';`
3. (비상 시) `DGMGRL> FAILOVER TO 'standby_db';`
4. 애플리케이션 TNS/커넥션 풀 재시작 및 헬스체크 통과 확인

## 4. 검증
- [ ] 새 Primary에 쓰기 성공
- [ ] 애플리케이션 에러율 정상화
- [ ] 데이터 정합성 스팟 체크

## 5. 사후
- 옛 Primary를 Standby로 재편입 (`DGMGRL> REINSTATE DATABASE`)
- 인시던트 채널 공유 + 포스트모템 티켓 생성 (09장 참조)

## 6. 롤백 / 에스컬레이션
- 페일오버 후에도 장애 지속 → DBA 리드 호출, DR 절차(03장) 검토
```

> **트레이드오프 메모**: switchover는 계획된 무손실 전환이라 정기 점검에 적합하지만, failover는 비상 상황에서 데이터 손실 가능성을 감수하고 가용성을 우선하는 결정이다. Maximum Protection 모드는 RPO=0을 더 강하게 보장하지만 Standby 미응답 시 Primary 커밋 자체가 멈출 수 있어(가용성 저하) 대부분의 운영 환경은 Maximum Availability를 절충안으로 택한다.

---

## 4. 체크리스트

- [ ] HA와 DR의 목적·시간척도·비용 차이를 설명할 수 있다.
- [ ] Active-Standby, Multi-AZ, Patroni, Pacemaker 등 패턴의 장단점을 비교할 수 있다.
- [ ] 스플릿 브레인이 발생하는 원리와 쿼럼/펜싱(STONITH)으로 막는 방법을 설명할 수 있다.
- [ ] 동기 vs 비동기 복제가 페일오버 시 RPO에 미치는 영향을 판단할 수 있다.
- [ ] 자동 페일오버의 오탐을 줄이는 헬스체크·타임아웃 설계를 할 수 있다.
- [ ] 계획된 switchover와 비상 failover의 차이와 각 절차를 수행할 수 있다.
- [ ] 온콜 담당자가 그대로 따라 할 수 있는 페일오버 Runbook을 작성할 수 있다.
