# 07. 클라우드 매니지드 DB 심화

## 1. 핵심 개념 설명

중급 단계에서 RDS/Cloud SQL 같은 "전통 DB를 클라우드가 대신 운영해주는" 매니지드 서비스를 다뤘다면, 이 장은 **클라우드 네이티브로 재설계된 차세대 매니지드 DB**를 다룬다. Aurora, AlloyDB 같은 서비스는 단순히 PostgreSQL/MySQL을 호스팅하는 게 아니라, **스토리지와 컴퓨트를 분리(disaggregation)**하고 로그를 스토리지 계층으로 내려 클라우드 인프라에 최적화한 구조다. 시니어 DBA는 이들의 내부 아키텍처를 이해해야 "왜 이게 더 빠르고, 어디에 함정이 있으며, 언제 이걸 골라야 하는가"를 판단할 수 있다.

### 컴퓨트-스토리지 분리 아키텍처
전통 DB는 한 노드가 컴퓨트와 스토리지를 함께 가진다. Aurora/AlloyDB는 이를 분리한다:
- **스토리지 계층**: 여러 AZ에 데이터를 자동 복제(Aurora는 3-AZ 6-way)하는 분산·자가치유 스토리지. DB 노드는 데이터 페이지 전체가 아니라 **redo 로그만** 스토리지로 보내고, 스토리지가 페이지를 재구성한다 → 쓰기 증폭 감소, 빠른 복구.
- **컴퓨트 계층**: 상태를 거의 갖지 않는 DB 인스턴스. 그래서 리플리카 추가/페일오버가 빠르고, 스토리지를 공유하므로 리플리카에 복제 지연이 매우 작다.

이 구조 덕분에 얻는 것: **빠른 페일오버**(스토리지가 이미 공유·복제됨), **저지연 리드 리플리카**, **빠른 백업/복원**(스토리지 스냅샷), **독립적 스케일링**(컴퓨트만 키우거나 리플리카만 늘림).

### 주요 심화 기능
- **Aurora Serverless v2**: 부하에 따라 컴퓨트 용량(ACU)을 초 단위로 자동 스케일. 변동성 크거나 예측 어려운 워크로드·개발환경에 적합. 상시 고부하엔 프로비저닝이 더 싸다.
- **Aurora Global Database**: 한 리전을 Primary로, 여러 리전에 read-only 세컨더리를 두고 **스토리지 레벨 복제**(전용 인프라, 지연 통상 1초 미만). 리전 장애 시 세컨더리를 1분 내 승격 → 03장 DR의 강력한 구현체. RPO는 초 단위, RTO는 분 단위.
- **GCP AlloyDB**: PostgreSQL 호환 + 컬럼형 엔진(분석 가속) + ML 기반 자동 튜닝. HTAP(트랜잭션+분석 혼합) 워크로드를 노린다.
- **읽기 오토스케일링**: 리플리카를 부하에 따라 자동 증감.

### 비용 최적화 (FinOps 관점)
매니지드 DB는 편하지만 비싸다. 시니어는 비용도 아키텍처 결정의 일부로 본다:
- **컴퓨트**: 예약 인스턴스/Savings Plan(상시 부하), Serverless(변동 부하), 리드 리플리카 수 최적화.
- **스토리지·I/O**: Aurora는 I/O 과금 모델(표준 vs I/O-Optimized) 선택이 큰 차이를 만든다. I/O가 많으면 I/O-Optimized가 오히려 싸다.
- **데이터 전송**: 리전 간·AZ 간 트래픽 비용. 멀티리전은 편의와 비용의 트레이드오프.
- **백업·스냅샷 보관**, 유휴 리소스 정리, 환경별(운영/스테이징) 스펙 차등.

---

## 2. 주요 명령어/문법

### AWS Aurora
```bash
# Aurora PostgreSQL 클러스터 생성
aws rds create-db-cluster --db-cluster-identifier prod-aurora \
  --engine aurora-postgresql --engine-version 16.3 \
  --master-username admin --manage-master-user-password \
  --storage-type aurora-iopt1                    # I/O-Optimized (I/O 많을 때 비용 유리)

# Serverless v2 용량 범위 지정 (0.5~16 ACU 자동 스케일)
aws rds create-db-cluster --db-cluster-identifier dev-aurora \
  --engine aurora-postgresql \
  --serverless-v2-scaling-configuration MinCapacity=0.5,MaxCapacity=16

# 저지연 리드 리플리카(리더) 추가
aws rds create-db-instance --db-instance-identifier prod-aurora-r1 \
  --db-cluster-identifier prod-aurora --engine aurora-postgresql \
  --db-instance-class db.r6g.large

# Global Database: 세컨더리 리전 추가 (DR)
aws rds create-global-cluster --global-cluster-identifier prod-global \
  --source-db-cluster-identifier arn:aws:rds:ap-northeast-2:...:cluster:prod-aurora
aws rds create-db-cluster --db-cluster-identifier prod-aurora-tokyo \
  --global-cluster-identifier prod-global --engine aurora-postgresql \
  --region ap-northeast-1

# 리전 장애 시 세컨더리 승격 (계획된 전환은 failover-global-cluster)
aws rds failover-global-cluster --global-cluster-identifier prod-global \
  --target-db-cluster-identifier arn:aws:rds:ap-northeast-1:...:cluster:prod-aurora-tokyo
```

### GCP AlloyDB / Cloud SQL
```bash
# AlloyDB 클러스터 + Primary 인스턴스
gcloud alloydb clusters create prod-alloy --region=asia-northeast3 --password=...
gcloud alloydb instances create prod-primary --cluster=prod-alloy \
  --region=asia-northeast3 --instance-type=PRIMARY --cpu-count=8

# 읽기 풀 인스턴스(오토스케일 읽기)
gcloud alloydb instances create prod-readpool --cluster=prod-alloy \
  --region=asia-northeast3 --instance-type=READ_POOL --read-pool-node-count=3

# 크로스리전 DR (Cloud SQL 예)
gcloud sql instances create proddb-dr --master-instance-name=proddb --region=us-west1
```

### 엔진 접속 자체는 표준 PostgreSQL/MySQL
```bash
# Aurora PostgreSQL 엔드포인트에 표준 psql로 접속 (writer/reader 엔드포인트 구분)
psql "host=prod-aurora.cluster-xxx.rds.amazonaws.com dbname=app sslmode=require"       # 쓰기
psql "host=prod-aurora.cluster-ro-xxx.rds.amazonaws.com dbname=app sslmode=require"    # 읽기(리더 엔드포인트)
```

---

## 3. 실습 예제

**시나리오: "글로벌 사용자를 가진 서비스. 서울이 주 리전, RPO<1초·RTO<1분의 DR과 읽기 지연 최소화가 목표. Aurora로 설계한다."**

1. **주 리전 구성(서울)**: Aurora PostgreSQL 클러스터. Writer 1 + Reader 2(다른 AZ). 스토리지가 3-AZ 6-way로 자동 복제되므로 AZ 장애는 스토리지 계층이 흡수, 컴퓨트 페일오버는 초 단위.

2. **엔드포인트 라우팅**: 애플리케이션은 **cluster 엔드포인트(쓰기)**와 **reader 엔드포인트(읽기)**를 구분해 사용. reader 엔드포인트는 리더들에 자동 부하분산. 리더 지연이 매우 작아 04장의 "복제 지연" 고민이 크게 준다.

3. **글로벌 DR**: Aurora Global Database로 도쿄에 세컨더리 리전 구성. 스토리지 레벨 복제(지연 <1초)로 RPO 목표 충족. 도쿄 세컨더리의 read-only 엔드포인트는 일본 사용자의 읽기 지연도 낮춘다(일석이조).

4. **DR 발동 시뮬레이션(Drill, 03장 연계)**: 서울 리전 장애 가정 → `failover-global-cluster`로 도쿄 승격. 승격 후 애플리케이션 라우팅(Route 53)을 도쿄로 전환. 실제 RTO/RPO 계측·기록.

5. **비용 최적화**:
   - 운영은 프로비저닝 인스턴스 + Savings Plan(상시 부하), 개발/스테이징은 Serverless v2(min 0.5 ACU)로 유휴 시 비용 절감.
   - I/O가 많은 워크로드라 **I/O-Optimized** 스토리지로 전환해 I/O 과금 폭을 줄임(측정 후 판단).
   - 리더 수는 실제 읽기 부하 기준으로 조정, 오토스케일링으로 과다 프로비저닝 방지.

6. **함정 인지**: Serverless v2는 급격한 스파이크에 스케일 지연이 있을 수 있고, Global Database 세컨더리는 승격 전엔 쓰기 불가. Aurora는 벤더 종속(lock-in)이 크다 — 표준 PostgreSQL로의 이탈 경로(논리 복제 등)를 미리 염두에 둔다.

> **트레이드오프 메모**: Aurora/AlloyDB는 운영 부담을 크게 줄이고 HA/DR을 강력하게 제공하지만, ① 비용이 표준 RDS보다 높을 수 있고, ② 벤더 종속이 강하며, ③ 내부 동작이 블랙박스라 세밀한 커널 레벨 튜닝은 제한적이다. "관리 부담 감소·강력한 HA/DR"의 가치가 "비용·종속"보다 큰 워크로드(핵심 OLTP, 글로벌 서비스)에 적합하고, 단순·저비용이 우선이면 표준 매니지드나 셀프 매니지드가 나을 수 있다.

---

## 4. 체크리스트

- [ ] 컴퓨트-스토리지 분리 아키텍처가 주는 이점(빠른 페일오버·저지연 리플리카·빠른 복원)을 설명할 수 있다.
- [ ] Aurora Serverless v2가 적합한 워크로드와 부적합한 워크로드를 구분할 수 있다.
- [ ] Aurora Global Database로 RPO<1초·RTO<1분급 DR을 설계할 수 있다.
- [ ] writer/reader 엔드포인트를 구분해 읽기/쓰기 라우팅을 구성할 수 있다.
- [ ] AlloyDB의 HTAP·컬럼형 엔진 등 특성과 적합 사례를 안다.
- [ ] I/O 과금 모델(표준 vs I/O-Optimized) 등 비용 구조를 이해하고 최적화할 수 있다.
- [ ] 예약/Serverless/리플리카 수 조정으로 컴퓨트 비용을 최적화할 수 있다.
- [ ] 매니지드 DB의 벤더 종속·블랙박스 한계를 인지하고 이탈 경로를 고려한다.
