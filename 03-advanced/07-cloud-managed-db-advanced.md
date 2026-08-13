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
- **읽기 오토스케일링**: 리플리카를 부하에 따라 자동 증감.

### 비용 최적화 (FinOps 관점)
매니지드 DB는 편하지만 비싸다. 시니어는 비용도 아키텍처 결정의 일부로 본다:
- **컴퓨트**: 예약 인스턴스/Savings Plan(상시 부하), Serverless(변동 부하), 리드 리플리카 수 최적화.
- **스토리지·I/O**: Aurora는 I/O 과금 모델(표준 vs I/O-Optimized) 선택이 큰 차이를 만든다. I/O가 많으면 I/O-Optimized가 오히려 싸다.
- **데이터 전송**: 리전 간·AZ 간 트래픽 비용. 멀티리전은 편의와 비용의 트레이드오프.
- **백업·스냅샷 보관**, 유휴 리소스 정리, 환경별(운영/스테이징) 스펙 차등.

---

## 2. 주요 명령어/문법


### GCP AlloyDB / Cloud SQL

```bash
# 크로스리전 DR (Cloud SQL 예)
gcloud sql instances create proddb-dr --master-instance-name=proddb --region=us-west1
```


---

## 3. 실습 예제


### 실습 예제 — Aurora MySQL

**시나리오: "글로벌 사용자를 가진 서비스. 서울이 주 리전, RPO<1초·RTO<1분의 DR과 읽기 지연 최소화가 목표. Aurora MySQL로 설계한다."**

1. **주 리전 구성(서울)**: `--engine aurora-mysql`로 Aurora MySQL(8.0 호환) 클러스터를 생성. Writer 1 + Reader 2(다른 AZ). 스토리지 분리·3-AZ 6-way 복제·빠른 페일오버는 엔진에 무관하게 Aurora 공통 아키텍처라 PostgreSQL과 동일하게 적용된다.

   ```bash
   aws rds create-db-cluster --db-cluster-identifier prod-aurora-mysql \
     --engine aurora-mysql --engine-version 8.0.mysql_aurora.3.05.2 \
     --master-username admin --manage-master-user-password \
     --storage-type aurora-iopt1

   aws rds create-db-instance --db-instance-identifier prod-aurora-mysql-r1 \
     --db-cluster-identifier prod-aurora-mysql --engine aurora-mysql \
     --db-instance-class db.r6g.large
   ```

2. **엔드포인트 라우팅**: PostgreSQL과 동일하게 cluster 엔드포인트(쓰기)/reader 엔드포인트(읽기)를 구분해 사용. 도메인 포맷도 동일(`cluster-xxx.../cluster-ro-xxx...`).

3. **글로벌 DR**: Aurora Global Database는 MySQL·PostgreSQL 엔진을 모두 지원한다. 도쿄에 `--engine aurora-mysql` 세컨더리 클러스터를 추가해 전용 인프라 기반 스토리지 레벨 복제(지연 통상 1초 미만)로 RPO 목표를 충족한다.

   ```bash
   aws rds create-global-cluster --global-cluster-identifier prod-global-mysql \
     --source-db-cluster-identifier arn:aws:rds:ap-northeast-2:...:cluster:prod-aurora-mysql
   aws rds create-db-cluster --db-cluster-identifier prod-aurora-mysql-tokyo \
     --global-cluster-identifier prod-global-mysql --engine aurora-mysql \
     --region ap-northeast-1
   ```

4. **DR 발동 시뮬레이션**: `aws rds failover-global-cluster`로 도쿄를 승격하고 애플리케이션 라우팅을 전환, 실제 RTO/RPO를 계측한다 — 절차는 PostgreSQL 시나리오와 동일하다.

5. **비용 최적화**: 상시 부하 운영 인스턴스는 예약/Savings Plan, 개발·스테이징은 Serverless v2(`aurora-mysql`도 지원)로 절감. I/O가 많은 워크로드라면 I/O-Optimized 스토리지로 전환 — 과금 모델은 엔진과 무관하게 동일하다.

6. **MySQL 엔진 고유 특성 인지**: Aurora MySQL은 **InnoDB 스토리지 엔진만** 지원한다(MyISAM 등을 쓰던 기존 MySQL을 옮길 때는 InnoDB 변환이 선행돼야 한다). **Backtrack**(백업 복원 없이 특정 시점으로 클러스터를 되돌리는 기능)은 Aurora MySQL 전용이지만, 크로스 리전 리플리카·Global Database 세컨더리와 함께 쓸 때는 제약이 있으므로(백트랙 활성 클러스터의 크로스 리전 스냅샷/리플리카 제한) DR 설계와 별도로 검토한다.

> **트레이드오프 메모**: Aurora MySQL은 PostgreSQL 엔진과 아키텍처·운영 절차(엔드포인트 구조, Global Database, 비용 모델)를 거의 그대로 공유한다. 그래서 두 엔진 중 선택은 "어느 쪽이 더 나은 클라우드 아키텍처인가"보다 "기존 애플리케이션 스택·팀 숙련도가 어느 생태계에 맞춰져 있는가"로 결정하는 경우가 많다. 다만 PostgreSQL 계열의 확장 생태계(PostGIS 등)나 AlloyDB급 HTAP 컬럼 엔진은 MySQL 계열에 없고, Backtrack처럼 MySQL 엔진에만 있는 기능도 있다 — 세부 기능 차이는 남아 있으니 요구사항에 맞춰 확인한다.


---

## 4. 체크리스트

- [ ] 컴퓨트-스토리지 분리 아키텍처가 주는 이점(빠른 페일오버·저지연 리플리카·빠른 복원)을 설명할 수 있다.
- [ ] Aurora Serverless v2가 적합한 워크로드와 부적합한 워크로드를 구분할 수 있다.
- [ ] Aurora Global Database로 RPO<1초·RTO<1분급 DR을 설계할 수 있다.
- [ ] writer/reader 엔드포인트를 구분해 읽기/쓰기 라우팅을 구성할 수 있다.
- [ ] I/O 과금 모델(표준 vs I/O-Optimized) 등 비용 구조를 이해하고 최적화할 수 있다.
- [ ] 예약/Serverless/리플리카 수 조정으로 컴퓨트 비용을 최적화할 수 있다.
- [ ] 매니지드 DB의 벤더 종속·블랙박스 한계를 인지하고 이탈 경로를 고려한다.
