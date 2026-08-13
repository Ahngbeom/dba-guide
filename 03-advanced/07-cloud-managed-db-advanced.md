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



### 실습 예제 — Oracle 매니지드 DB 심화 옵션

**먼저 짚어야 할 것**: AWS·GCP에는 Oracle 엔진을 위한 "Aurora/AlloyDB급" 클라우드 네이티브 재설계 매니지드 서비스가 없다. 스토리지-컴퓨트 분리, 서버리스 오토스케일, 전용 인프라 기반 글로벌 복제 같은 Aurora/AlloyDB의 특징을 Oracle 엔진으로 제공하는 AWS/GCP 상품은 존재하지 않는다 — Oracle의 라이선스 정책상 타사 클라우드가 Oracle 엔진 내부를 그렇게 재설계해 서비스하는 것 자체가 허용되지 않는 영향이 크다. "Aurora Oracle" 같은 상품은 없다는 전제에서 출발해야 한다.

**시나리오: "DB는 Oracle이지만 서비스는 AWS 위에 있다. AWS 안에서 최대한 강한 HA/DR을 구성하고, Oracle 자체 클라우드 옵션과 비교해 의사결정한다."**

1. **AWS 안에서의 기본 HA — RDS for Oracle Multi-AZ**: RDS for Oracle의 Multi-AZ는 Aurora와 달리 **AWS 자체의 동기식 스토리지 미러링**이다(Oracle Data Guard가 아니다). 같은 리전 내 AZ 장애에 대비하지만 스탠바이는 읽기 트래픽을 받지 못한다.

   ```bash
   aws rds create-db-instance --db-instance-identifier prod-ora \
     --engine oracle-ee --license-model bring-your-own-license \
     --multi-az --db-instance-class db.r6i.xlarge \
     --master-username admin --manage-master-user-password
   ```

2. **진짜 Data Guard가 필요하면 리플리카**: RDS for Oracle은 **Oracle Active Data Guard 기반 물리적 스탠바이**를 리전 간 리플리카로 제공한다. `--replica-mode mounted`는 DR 전용(읽기 불가, Active Data Guard 라이선스 불필요)이고, `read-only`는 읽기 트래픽까지 받되 Active Data Guard 라이선스가 추가로 필요하다.

   ```bash
   aws rds create-db-instance-read-replica \
     --db-instance-identifier prod-ora-tokyo \
     --source-db-instance-identifier arn:aws:rds:ap-northeast-2:...:db:prod-ora \
     --region ap-northeast-1 \
     --replica-mode mounted   # DR 전용. 읽기까지 받으려면 read-only(+Active Data Guard 라이선스)
   ```

3. **DR 발동은 관리형 스위치오버로**: RDS for Oracle은 무중단(zero data loss) 관리형 Oracle Data Guard 스위치오버를 지원한다. 계획된 전환·재해 시뮬레이션 모두 이 명령으로 처리하고 실제 RTO/RPO를 계측·기록한다(03장 DR 드릴과 연계).

   ```bash
   aws rds switchover-read-replica --db-instance-identifier prod-ora-tokyo
   ```

4. **한계 인지**: RDS for Oracle에는 Aurora의 스토리지 분리·서버리스 오토스케일·전용 인프라 글로벌 복제가 없다. Multi-AZ 스탠바이는 읽기 불가, Data Guard 리플리카는 비동기 복제라 RPO가 0에 완전히 수렴하지 않을 수 있으며, Oracle EE 라이선스(+ 필요 시 Active Data Guard 옵션)가 별도 비용으로 붙는다.

5. **대안 — Oracle Autonomous Database(OCI)**: 컴퓨트-스토리지 분리, 자동 튜닝·패치·스케일링(self-driving·self-securing·self-repairing)까지 Aurora/AlloyDB에 가장 가까운 "클라우드 네이티브 재설계"는 사실 **Oracle 자신의 클라우드(OCI)**에 있다. 다만 이는 AWS/GCP 상품이 아니므로 채택 시 멀티클라우드 전략, 기존 AWS 네트워킹·IAM과의 연동을 별도로 설계해야 한다.

6. **의사결정 기준**: "AWS 안에서 최대한 끌어올린다"(RDS Multi-AZ + Data Guard 리플리카)와 "OCI로 옮겨 진짜 클라우드 네이티브를 누린다"(Autonomous Database) 중 하나를 팀 역량·비용·마이그레이션 리스크 기준으로 선택한다. Aurora/AlloyDB와 동급 상품을 AWS/GCP의 Oracle 엔진에서 찾으려 하지 않는 것이 출발점이다.

> **트레이드오프 메모**: Oracle은 라이선스 구조상 AWS/GCP가 Aurora/AlloyDB처럼 엔진 내부를 재설계해 자사 클라우드에 심을 수 없다. 그 결과 "AWS에 있는 것 중 최선"(RDS Multi-AZ + Data Guard 리플리카)과 "진짜 클라우드 네이티브"(OCI Autonomous Database)가 서로 다른 클라우드 사업자에 나뉘어 있다는 것 자체가 Oracle 워크로드의 근본적 제약이다. 기존 PL/SQL 자산·RAC 등 Oracle을 고집할 이유가 크지 않다면, 이 장이 보여주듯 PostgreSQL/MySQL 기반 Aurora·AlloyDB로 옮기는 쪽이 운영 부담과 비용 모두에서 유리한 경우가 많다.

---

## 4. 체크리스트

- [ ] 컴퓨트-스토리지 분리 아키텍처가 주는 이점(빠른 페일오버·저지연 리플리카·빠른 복원)을 설명할 수 있다.
- [ ] Aurora Serverless v2가 적합한 워크로드와 부적합한 워크로드를 구분할 수 있다.
- [ ] Aurora Global Database로 RPO<1초·RTO<1분급 DR을 설계할 수 있다.
- [ ] writer/reader 엔드포인트를 구분해 읽기/쓰기 라우팅을 구성할 수 있다.
- [ ] I/O 과금 모델(표준 vs I/O-Optimized) 등 비용 구조를 이해하고 최적화할 수 있다.
- [ ] 예약/Serverless/리플리카 수 조정으로 컴퓨트 비용을 최적화할 수 있다.
- [ ] 매니지드 DB의 벤더 종속·블랙박스 한계를 인지하고 이탈 경로를 고려한다.
