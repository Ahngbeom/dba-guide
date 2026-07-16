# 03. 재해복구 (Disaster Recovery, DR)

## 1. 핵심 개념 설명

재해복구는 **리전 전체 소실, 데이터센터 화재, 대규모 랜섬웨어, 인적 실수로 인한 데이터 파괴** 같은 "HA로는 못 막는 재난"에서 서비스를 되살리는 능력이다. HA(앞 장)가 "가까운 곳의 예비"라면 DR은 "멀리 떨어진 곳의 예비"다. DR 설계의 본질은 기술이 아니라 **비즈니스와의 협상**이다. "얼마나 잃어도 되는가(RPO)"와 "얼마나 빨리 살려야 하는가(RTO)"를 비즈니스가 정하면, DBA는 그 목표를 달성하는 가장 싼 아키텍처를 설계한다.

### RPO / RTO — 모든 DR 설계의 출발점

- **RPO (Recovery Point Objective)**: 허용 가능한 **데이터 손실량**을 시간으로 표현. "RPO 5분" = 최악의 경우 최근 5분치 데이터를 잃어도 된다. RPO는 **복제/백업 주기**가 결정한다.
- **RTO (Recovery Time Objective)**: 허용 가능한 **복구 소요 시간**. "RTO 1시간" = 장애 발생 후 1시간 안에 서비스를 정상화해야 한다. RTO는 **대기 인프라의 준비 상태**가 결정한다.

> 핵심 통찰: RPO=0(무손실)과 RTO=0(무중단)에 가까워질수록 비용은 기하급수적으로 는다. 모든 데이터가 동일한 RPO/RTO를 필요로 하지 않는다. 결제 데이터는 RPO 0을, 분석용 로그는 RPO 24시간을 적용하는 식으로 **데이터 등급별 차등 설계**가 시니어의 판단이다.

### DR 전략의 스펙트럼 (Cold → Warm → Hot)

| 전략 | 대기 인프라 상태 | 데이터 동기화 | RTO | RPO | 비용 |
|------|----------------|-------------|-----|-----|------|
| **Backup & Restore** | 없음, 백업만 보관 | 정기 백업 | 수 시간~일 | 백업 주기 | 최저 |
| **Cold Standby (Pilot Light)** | 최소 코어만 상시(DB 복제만), 앱은 꺼둠 | 비동기 복제 | 수십 분~시간 | 분~시간 | 낮음 |
| **Warm Standby** | 축소된 규모로 상시 가동 | 비동기 복제 | 수 분~수십 분 | 초~분 | 중간 |
| **Hot Standby (Active-Active/멀티리전)** | 풀 규모 상시 가동 | 준동기/양방향 | 초 | ≈0 | 최고 |

### 멀티리전 복구의 어려움
- **네트워크 지연**: 리전 간 왕복 지연(수십~수백 ms)으로 동기 복제는 쓰기 성능을 크게 떨어뜨린다. 대개 리전 간은 비동기가 현실적.
- **데이터 주권/규제**: 데이터를 특정 국가 밖으로 못 내보내는 규제(05장 컴플라이언스 연계)가 리전 선택을 제약한다.
- **페일백(fail-back)**: 재해가 끝난 뒤 원래 리전으로 되돌리는 과정이 페일오버보다 어렵다. 그동안 쌓인 변경을 역방향 동기화해야 한다.

---

## 2. 주요 명령어/문법

### PostgreSQL — PITR + 원격 아카이빙 (Cold/Warm의 기반)
```ini
# postgresql.conf — WAL을 원격(다른 리전 오브젝트 스토리지)로 아카이빙
archive_mode = on
archive_command = 'aws s3 cp %p s3://dr-wal-bucket/%f'
```
```bash
# 다른 리전에서 특정 시점으로 복구 (예: 실수한 DELETE 직전으로)
# recovery.signal 파일 + 아래 설정으로 복구
restore_command = 'aws s3 cp s3://dr-wal-bucket/%f %p'
recovery_target_time = '2026-07-15 03:59:00+09'
```

### MySQL — binlog 기반 PITR
```bash
# 풀백업 복원 후, binlog를 특정 시점까지 재적용
mysqlbinlog --stop-datetime="2026-07-15 03:59:00" \
  mysql-bin.000042 | mysql -u root -p
```

### Oracle — Data Guard 원격 Standby + Flashback
```sql
-- 원격 리전 물리 Standby 로 지속 적용 (Warm/Hot)
DGMGRL> SHOW CONFIGURATION;
-- 인적 실수 대비: 데이터베이스 전체를 특정 시점으로 되감기
FLASHBACK DATABASE TO TIMESTAMP TO_TIMESTAMP('2026-07-15 03:59:00','YYYY-MM-DD HH24:MI:SS');
```

### 클라우드 매니지드 — 크로스리전 복제/복구
```bash
# AWS RDS: 다른 리전에 리드 리플리카(Warm) 생성
aws rds create-db-instance-read-replica \
  --db-instance-identifier proddb-dr \
  --source-db-instance-identifier arn:aws:rds:ap-northeast-2:...:db:proddb \
  --region us-west-2

# AWS: 자동 백업 크로스리전 복제 활성화 (Cold)
aws rds start-db-instance-automated-backups-replication \
  --source-db-instance-arn arn:aws:rds:ap-northeast-2:...:db:proddb \
  --region us-west-2

# GCP Cloud SQL: 크로스리전 리드 리플리카
gcloud sql instances create proddb-dr --master-instance-name=proddb \
  --region=us-west1
```

---

## 3. 실습 예제

**시나리오 A: "운영자가 프로덕션에서 `DELETE FROM orders`를 WHERE 없이 실행했다."**

이건 인프라 재해가 아니라 **논리적 재해**다. 복제본에도 그대로 전파되므로 HA로는 못 막는다. → PITR이 답.

1. **즉시 조치**: 추가 쓰기 차단(애플리케이션을 읽기 전용/점검 모드로).
2. **정확한 시점 특정**: 사고 발생 직전 타임스탬프를 로그·binlog에서 확인(예: `03:59:00`).
3. **별도 인스턴스로 복구**(운영에 바로 덮어쓰지 않는다):
   최근 풀백업을 새 인스턴스에 복원 → WAL/binlog를 사고 **직전 시점까지만** 재적용.
4. **데이터 추출·병합**: 복구본에서 삭제된 `orders`만 추출해 운영에 다시 삽입. 사고 이후 정상 트랜잭션과 충돌하지 않게 병합.
5. **회고**: 왜 WHERE 없는 DELETE가 가능했나 → 프로덕션 접근 통제·`sql_safe_updates`·리뷰 게이트 도입(09장 연계).

**시나리오 B: "서울 리전(ap-northeast-2) 전체 장애 — 멀티리전 페일오버(DR Drill)"**

Warm Standby 구성(도쿄 리전에 크로스리전 리드 리플리카 + 축소 앱)을 가정한다.

1. **재해 선언**: 리전 장애가 일정 시간(예: 15분) 지속되고 자체 복구 조짐이 없으면 **DR 발동 결정권자(사전 지정)**가 선언.
2. **DB 승격**: 도쿄의 리드 리플리카를 독립 Primary로 승격.
   ```bash
   aws rds promote-read-replica --db-instance-identifier proddb-dr --region us-west-2
   ```
3. **애플리케이션 전환**: DNS(예: Route 53 헬스체크 기반 페일오버 레코드)를 DR 리전으로 전환, 앱 오토스케일 그룹을 풀 규모로 확장.
4. **RPO 확인**: 비동기 복제였으므로 마지막 복제 시점과 재해 시점의 차이 = 실제 데이터 손실량을 계측·기록.
5. **정상화 후 페일백**: 서울 리전 복구 후, 그동안 도쿄에 쌓인 변경을 역방향 동기화 → 저트래픽 시간대에 계획된 switchover로 원복.

### DR 훈련(Failover Drill) 절차
DR은 **훈련하지 않으면 존재하지 않는 것과 같다.** 백업이 복원되는지, Runbook이 실제로 동작하는지는 해봐야 안다.

```markdown
# 분기별 DR Drill 체크리스트
1. 계획: 범위(전체/부분), 일시(저트래픽), 참여자(온콜/DBA/앱팀), 성공 기준(RTO 60분, RPO 5분) 정의
2. 사전 공지: 이해관계자 통지, 롤백 계획 준비
3. 실행: Runbook대로 DR 리전 승격·트래픽 전환 (가능하면 실제 트래픽 일부로 검증)
4. 계측: 실제 RTO/RPO, 각 단계 소요시간, 막힌 지점 기록
5. 원복: 페일백 절차 수행
6. 회고: 목표 대비 실제 갭 분석 → Runbook/자동화/아키텍처 개선 항목 도출
```

> **트레이드오프 메모**: "우리는 백업이 있으니 DR이 된다"는 흔한 착각이다. 검증되지 않은 백업, 복원 시간이 RTO를 초과하는 백업, 같은 리전에만 있는 백업은 DR이 아니다. **복원을 실제로 해본 백업만이 백업이다.**

---

## 4. 체크리스트

- [ ] RPO와 RTO를 비즈니스 언어로 정의하고, 아키텍처 선택으로 번역할 수 있다.
- [ ] 데이터 등급별로 RPO/RTO를 차등 적용하는 설계를 할 수 있다.
- [ ] Backup&Restore / Cold(Pilot Light) / Warm / Hot 전략의 비용-효과를 비교할 수 있다.
- [ ] PITR로 논리적 재해(잘못된 DELETE 등)를 특정 시점으로 복구할 수 있다.
- [ ] 멀티리전 복제의 지연·규제·페일백 문제를 이해하고 설계에 반영할 수 있다.
- [ ] 크로스리전 리드 리플리카 승격 등 실제 페일오버 명령을 수행할 수 있다.
- [ ] 정기 DR Drill을 계획·실행하고 실제 RTO/RPO를 계측할 수 있다.
- [ ] "검증되지 않은 백업은 백업이 아니다"를 실천하는 복원 검증 루틴을 운영한다.
