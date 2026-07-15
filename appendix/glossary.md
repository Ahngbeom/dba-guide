# 용어집

학습서 전반에서 등장하는 핵심 용어를 알파벳/가나다 순으로 정리했다. 처음 보는 용어를 만났을 때 이 문서에서 빠르게 찾아보고, 자세한 내용은 관련 챕터를 참고한다.

## A-Z

- **ACID** — 트랜잭션이 보장해야 할 4가지 속성: 원자성(Atomicity), 일관성(Consistency), 고립성(Isolation), 지속성(Durability). → `01-beginner/01-rdbms-fundamentals.md`
- **AWR (Automatic Workload Repository)** — Oracle의 성능 데이터 자동 수집·저장 기능. → `02-intermediate/03-performance-monitoring.md`
- **Binlog (Binary Log)** — MySQL이 데이터 변경 사항을 기록하는 로그로, 복제와 PITR의 기반이 된다. → `02-intermediate/05-replication-basics.md`
- **DDL / DML / DCL / TCL** — SQL 명령어 분류. DDL(구조 정의), DML(데이터 조작), DCL(권한 제어), TCL(트랜잭션 제어). → `01-beginner/02-sql-basics.md`
- **EXPLAIN / EXPLAIN ANALYZE** — 쿼리 실행 계획을 확인하는 명령어. ANALYZE가 붙으면 실제로 쿼리를 실행하여 실측치를 보여준다. → `02-intermediate/02-indexing-and-query-tuning.md`
- **Failover** — 장애 발생 시 대기(Standby) 노드가 주(Primary) 역할을 넘겨받는 과정. → `03-advanced/02-high-availability-and-failover.md`
- **GTID (Global Transaction Identifier)** — MySQL 복제에서 각 트랜잭션을 고유하게 식별하는 ID로, 복제 위치 추적을 단순화한다.
- **MVCC (Multi-Version Concurrency Control)** — 읽기 작업이 쓰기 작업을 블로킹하지 않도록 데이터의 여러 버전을 동시에 유지하는 동시성 제어 기법. → `02-intermediate/01-transaction-and-locking.md`
- **PITR (Point-in-Time Recovery)** — 특정 시점 상태로 데이터베이스를 복구하는 기법. → `02-intermediate/04-backup-recovery-strategies.md`
- **Read Replica (읽기 복제본)** — 읽기 전용 쿼리를 분산 처리하기 위한 복제 노드. → `03-advanced/04-scaling-and-sharding.md`
- **RPO (Recovery Point Objective)** — 장애 시 허용 가능한 최대 데이터 손실 시점(얼마나 과거 데이터까지 복구 가능해야 하는지). → `03-advanced/03-disaster-recovery.md`
- **RTO (Recovery Time Objective)** — 장애 발생 후 서비스가 복구되기까지 허용 가능한 최대 시간. → `03-advanced/03-disaster-recovery.md`
- **Sharding (샤딩)** — 하나의 논리적 데이터셋을 여러 물리적 DB 인스턴스로 수평 분할하는 기법. → `03-advanced/04-scaling-and-sharding.md`
- **Split-brain (스플릿 브레인)** — 클러스터에서 네트워크 단절 등으로 두 노드가 동시에 자신을 Primary라고 인식하는 장애 상황. → `03-advanced/02-high-availability-and-failover.md`
- **TDE (Transparent Data Encryption)** — 저장된 데이터 파일을 애플리케이션 변경 없이 투명하게 암호화하는 기능. → `03-advanced/05-security-and-compliance.md`
- **WAL (Write-Ahead Logging)** — 데이터 변경 전에 로그를 먼저 기록하여 durability와 복구를 보장하는 기법(PostgreSQL의 핵심 메커니즘). → `02-intermediate/04-backup-recovery-strategies.md`

## 가나다

- **격리 수준(Isolation Level)** — 동시에 실행되는 트랜잭션이 서로에게 어떤 영향을 미칠 수 있는지를 정의하는 수준(Read Uncommitted ~ Serializable). → `02-intermediate/01-transaction-and-locking.md`
- **경합(Contention)** — 여러 세션이 동일한 자원(락, 커넥션 등)을 두고 경쟁하는 상황.
- **교착 상태(Deadlock)** — 두 개 이상의 트랜잭션이 서로가 점유한 락을 기다리며 무한 대기하는 상태. → `02-intermediate/01-transaction-and-locking.md`
- **논리 백업 / 물리 백업** — 논리 백업은 SQL 문 형태로 데이터를 추출(예: pg_dump), 물리 백업은 데이터 파일 자체를 복사하는 방식(예: RMAN). → `01-beginner/05-backup-basics.md`, `02-intermediate/04-backup-recovery-strategies.md`
- **온라인 DDL(무중단 스키마 변경)** — 서비스 중단 없이 테이블 구조를 변경하는 기법. → `02-intermediate/06-schema-change-management.md`
- **옵티마이저(Optimizer)** — SQL을 어떤 실행 경로로 처리할지 결정하는 DB 엔진의 구성 요소. → `03-advanced/01-advanced-performance-tuning.md`
- **온콜(On-call)** — 장애 발생 시 즉시 대응할 수 있도록 대기하는 운영 체계. → `03-advanced/08-incident-response-and-postmortem.md`
- **인덱스(Index)** — 조회 성능을 높이기 위해 별도로 구축하는 데이터 접근 경로 구조(B-Tree, Hash, GIN 등). → `02-intermediate/02-indexing-and-query-tuning.md`
- **정규화(Normalization)** — 데이터 중복을 최소화하고 무결성을 높이기 위해 테이블을 분해하는 설계 기법(1NF~3NF). → `01-beginner/01-rdbms-fundamentals.md`
- **최소 권한 원칙(Principle of Least Privilege)** — 계정에게 업무 수행에 필요한 최소한의 권한만 부여하는 보안 원칙. → `01-beginner/04-user-and-privilege-management.md`
- **커넥션 풀링(Connection Pooling)** — DB 연결을 미리 만들어두고 재사용하여 연결 생성 비용을 줄이는 기법. → `03-advanced/04-scaling-and-sharding.md`
- **파티셔닝(Partitioning)** — 하나의 큰 테이블을 여러 물리적 조각으로 나누어 관리·조회 성능을 높이는 기법. → `03-advanced/01-advanced-performance-tuning.md`
- **포스트모템(Postmortem, 사후분석)** — 장애 이후 원인과 재발 방지책을 정리하는 문서/프로세스. → `03-advanced/08-incident-response-and-postmortem.md`
