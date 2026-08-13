# 클라우드 관리형 DB 기초

## 핵심 개념 설명

오늘날 많은 조직이 DB를 직접 서버에 설치·운영하는 대신 **관리형 데이터베이스 서비스**를 사용한다. AWS RDS, GCP Cloud SQL, Azure Database가 대표적이다. 관리형 DB는 하드웨어 프로비저닝, OS 패치, 백업 자동화, 복제 구성, 장애 조치(failover) 같은 반복적인 운영 작업을 클라우드 제공자가 대신 처리해 준다.

DBA의 역할이 없어지는 것이 아니라 **바뀐다.** 물리 서버 관리와 설치는 줄어들지만, 파라미터 튜닝, 인덱스/쿼리 최적화, 용량 계획, 비용 관리, 보안 설정, 모니터링과 알람은 여전히 DBA의 몫이다. 오히려 클라우드에서는 "무엇이 자동화되고 무엇이 내 책임인가(공유 책임 모델)"를 정확히 아는 것이 중요하다.

주의할 점: 관리형 DB는 편의를 위해 **일부 권한을 제한**한다. `SUPERUSER`(PostgreSQL)나 `SYSDBA`(Oracle) 같은 최상위 권한, OS 파일 접근, 일부 확장(extension)은 사용할 수 없다. 그래서 `postgresql.conf`를 직접 수정하는 대신 **파라미터 그룹/플래그**를 통해 설정을 바꾼다.

### 핵심 운영 개념

- **파라미터 그룹(RDS) / 데이터베이스 플래그(Cloud SQL)**: 설정 파일을 직접 못 만지므로, 콘솔/CLI로 관리하는 설정 묶음. `max_connections`, `work_mem`, `shared_buffers`(대개 인스턴스 크기에 연동) 등을 조정한다. 일부는 재부팅이 필요하다(static parameter).
- **스냅샷(Snapshot)**: 특정 시점의 스토리지 이미지. 자동 스냅샷(보존 기간 설정)과 수동 스냅샷이 있으며, 스냅샷에서 새 인스턴스로 복원한다. 관리형 PITR도 이 스냅샷 + 트랜잭션 로그로 동작한다.
- **마이너 버전 업그레이드**: 보안·버그 수정 패치(예: 15.4 → 15.5). 자동 적용 옵션 또는 유지보수 창(maintenance window)에 수행. 메이저 업그레이드(15 → 16)는 별도 절차와 테스트가 필요하다.
- **Multi-AZ / 고가용성**: 다른 가용 영역에 대기 인스턴스를 두고 장애 시 자동 failover. 읽기 확장을 위한 **읽기 전용 복제본(Read Replica)** 도 별개로 제공.
- **모니터링**: CloudWatch(AWS) / Cloud Monitoring(GCP)로 CPU, 커넥션, IOPS, 스토리지, 복제 지연 등을 수집하고 알람을 건다. Enhanced Monitoring / Performance Insights로 쿼리 수준까지 관찰.

## 주요 명령어/문법

관리형 DB는 SQL보다 **클라우드 CLI/콘솔**로 운영한다. 아래는 AWS CLI(RDS)와 gcloud(Cloud SQL) 예시다.

### 파라미터/플래그 변경


### 스냅샷

**AWS RDS**
```bash
# 수동 스냅샷 생성
aws rds create-db-snapshot --db-instance-identifier mydb --db-snapshot-identifier mydb-20260715
# 스냅샷에서 새 인스턴스로 복원
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier mydb-restored --db-snapshot-identifier mydb-20260715
# PITR: 특정 시점으로 복원
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier mydb --target-db-instance-identifier mydb-pitr \
  --restore-time 2026-07-15T14:29:00Z
```

**GCP Cloud SQL**
```bash
gcloud sql backups create --instance=mydb
gcloud sql backups list --instance=mydb
gcloud sql instances clone mydb mydb-pitr --point-in-time '2026-07-15T14:29:00Z'
```

### 마이너 버전 업그레이드




## 실습 예제 — 진단 로깅 켜고 모니터링하기 (RDS Oracle)

시나리오: 관리형 Oracle에는 PostgreSQL/MySQL처럼 "슬로우 쿼리 로그"에 해당하는 파라미터가 없다. RDS for Oracle은 OS/파일 시스템 접근이 막혀 있고, `alter system set sql_trace`나 AWR/ASH 기반 진단은 대부분 **Enterprise Edition + Diagnostics Pack/Tuning Pack** 라이선스를 전제로 한다. 여기서는 라이선스 없이도 쓸 수 있는 현실적인 대안인 **alert log / listener log의 CloudWatch 내보내기**로 느린 쿼리의 간접 신호(에러, 타임아웃, 리스너 지연)를 관찰하는 절차를 다룬다.

```bash
# 1) alert log, listener log를 CloudWatch Logs로 내보내도록 인스턴스 설정
aws rds modify-db-instance --db-instance-identifier mydb \
  --cloudwatch-logs-export-configuration '{"EnableLogTypes":["alert","listener"]}' \
  --apply-immediately

# 2) (라이선스가 있는 경우) Diagnostics Pack 기반 세션 통계 수집 파라미터 확인
#    RDS 콘솔/파라미터 그룹에서 통제되며, Standard Edition에서는 사용할 수 없다.
aws rds describe-db-parameters --db-parameter-group-name my-oracle19 \
  --query "Parameters[?ParameterName=='control_management_pack_access']"

# 3) 커넥션 수 급증에 대한 CloudWatch 알람 생성 (다른 엔진과 동일한 방식)
aws cloudwatch put-metric-alarm --alarm-name mydb-high-connections \
  --namespace AWS/RDS --metric-name DatabaseConnections \
  --dimensions Name=DBInstanceIdentifier,Value=mydb \
  --statistic Average --period 60 --threshold 400 \
  --comparison-operator GreaterThanThreshold --evaluation-periods 3

# 4) Performance Insights는 엔진에 상관없이 켤 수 있어, 라이선스 없이도
#    상위 대기 이벤트/SQL을 근사적으로 관찰하는 창구가 된다.
```

**운영 팁**: RDS for Oracle에서 세션 단위 SQL 트레이스(`10046 이벤트`)나 AWR 리포트는 SYS/SYSDBA 권한이 필요한 경우가 많아 관리형 환경에서 제약이 크다. 라이선스가 없다면 Performance Insights(엔진 무관 제공 기능)와 alert/listener 로그가 사실상의 1차 진단 수단이며, Diagnostics Pack이 있는 조직만 AWR/ASH 기반 정밀 분석으로 넘어간다. "Oracle에도 slow_query_log 파라미터가 있겠지"라고 가정하고 파라미터 그룹을 뒤지는 실수를 하지 않도록 주의한다 — 존재하지 않는다.

## 체크리스트

- [ ] 관리형 DB에서 자동화되는 것과 여전히 DBA 책임인 것(공유 책임 모델)을 구분할 수 있다.
- [ ] 관리형 환경에서 최상위 권한과 OS 접근이 제한되는 이유를 이해한다.
- [ ] 파라미터 그룹(RDS)/데이터베이스 플래그(Cloud SQL)로 설정을 변경할 수 있고, static 파라미터는 재부팅이 필요함을 안다.
- [ ] 수동 스냅샷을 만들고 스냅샷/특정 시점으로 복원할 수 있다.
- [ ] 마이너 버전 업그레이드를 유지보수 창 또는 즉시 적용으로 수행하는 방법을 안다.
- [ ] Multi-AZ(HA)와 Read Replica(읽기 확장)의 차이를 설명할 수 있다.
- [ ] CloudWatch/Cloud Monitoring으로 주요 지표를 수집하고 알람을 설정할 수 있다.
- [ ] 관리형 DB의 비용 구성 요소를 이해하고 낭비를 점검할 수 있다.
