# DBA 학습서 — 예비 DBA부터 시니어 DBA/아키텍트까지

<!-- dbms:postgresql -->
이 저장소는 DBA(Database Administrator)로 성장하기 위한 단계별 학습 자료다. 이 브랜치는 [vendor-neutral 원본](https://github.com/Ahngbeom/dba-guide/tree/main)에서 **PostgreSQL 명령어만 걸러낸 뷰**이며, main 브랜치에서 자동 재생성된다(자세한 내용은 `docs/dbms-branch-strategy.md` 참고).
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
이 저장소는 DBA(Database Administrator)로 성장하기 위한 단계별 학습 자료다. 이 브랜치는 [vendor-neutral 원본](https://github.com/Ahngbeom/dba-guide/tree/main)에서 **MySQL 명령어만 걸러낸 뷰**이며, main 브랜치에서 자동 재생성된다(자세한 내용은 `docs/dbms-branch-strategy.md` 참고).
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
이 저장소는 DBA(Database Administrator)로 성장하기 위한 단계별 학습 자료다. 이 브랜치는 [vendor-neutral 원본](https://github.com/Ahngbeom/dba-guide/tree/main)에서 **Oracle 명령어만 걸러낸 뷰**이며, main 브랜치에서 자동 재생성된다(자세한 내용은 `docs/dbms-branch-strategy.md` 참고).
<!-- /dbms:oracle -->

<!-- dbms:neutral -->
이 저장소는 DBA(Database Administrator)로 성장하기 위한 단계별 학습 자료다. 특정 DBMS 벤더에 종속되지 않고 관계형 데이터베이스 공통 이론을 다루되, 주요 DBMS(PostgreSQL / MySQL / Oracle, 필요시 MSSQL)의 명령어를 함께 병기·비교한다.

> **특정 DBMS만 보고 싶다면** — [`postgresql`](https://github.com/Ahngbeom/dba-guide/tree/postgresql) / [`mysql`](https://github.com/Ahngbeom/dba-guide/tree/mysql) / [`oracle`](https://github.com/Ahngbeom/dba-guide/tree/oracle) 브랜치에서 해당 DBMS 명령어만 걸러낸 뷰를 볼 수 있다. 이 브랜치들은 `main`에서 자동 생성되는 파생 뷰이며 직접 수정하지 않는다 — 자세한 내용은 [`docs/dbms-branch-strategy.md`](docs/dbms-branch-strategy.md) 참고.
<!-- /dbms:neutral -->

## 학습 로드맵

```
01-beginner (예비/신입 DBA)
   └─ RDBMS 기초 → SQL 기초 → 설치/접속 → 계정·권한 → 기본 백업 → 기본 모니터링
        │
        ▼
02-intermediate (실무 독립 수행 DBA)
   └─ 트랜잭션/락 → 인덱싱/쿼리튜닝 → 성능 모니터링 → 백업·복구 전략
      → 복제 → 스키마 변경 관리 → 클라우드 DB 인프라 구축/접속 → 클라우드 매니지드 DB 기초
        │
        ▼
03-advanced (시니어 DBA / DB 아키텍트)
   └─ 고급 성능 튜닝 → 고가용성/페일오버 → 재해복구(DR) → 확장/샤딩
      → 보안/컴플라이언스 → 자동화(IaC) → 클라우드 매니지드 DB 심화
      → Kubernetes 기반 DB Operator → 장애 대응/포스트모템
```

각 단계는 순서대로 학습하는 것을 권장하지만, 이미 실무 경험이 있다면 `00-overview.md`의 체크리스트로 자신의 현재 위치를 먼저 진단하고 필요한 챕터부터 골라 보는 것도 좋다.

## 목차

### [01. 초급 (예비/신입 DBA)](01-beginner/00-overview.md)

| 챕터 | 내용 |
|---|---|
| [00. 개요](01-beginner/00-overview.md) | 선수 지식, 이 단계의 목표, 중급 진입 체크리스트 |
| [01. RDBMS 기초](01-beginner/01-rdbms-fundamentals.md) | 관계형 모델, ACID, 정규화, 키·제약조건 |
| [02. SQL 기초](01-beginner/02-sql-basics.md) | DDL/DML/DCL/TCL |
| [03. 설치와 접속](01-beginner/03-installation-and-access.md) | 설치, 서비스 시작/중지, 클라이언트 도구 접속 |
| [04. 계정과 권한 관리](01-beginner/04-user-and-privilege-management.md) | 계정 생성, GRANT/REVOKE, 역할 |
| [05. 백업 기초](01-beginner/05-backup-basics.md) | 논리 백업/복원(pg_dump, mysqldump, expdp) |
| [06. 기본 모니터링](01-beginner/06-basic-monitoring.md) | 로그 확인, 세션 조회, 디스크 사용량 |
| [07. 명령어 대조표](01-beginner/07-commands-cheatsheet.md) | 초급 단계 명령어 요약 |

### [02. 중급 (실무 독립 수행 DBA)](02-intermediate/00-overview.md)

| 챕터 | 내용 |
|---|---|
| [00. 개요](02-intermediate/00-overview.md) | 선수 지식, 이 단계의 목표, 고급 진입 체크리스트 |
| [01. 트랜잭션과 락](02-intermediate/01-transaction-and-locking.md) | 격리 수준, MVCC, 락, 데드락 |
| [02. 인덱싱과 쿼리 튜닝](02-intermediate/02-indexing-and-query-tuning.md) | 인덱스 종류, 실행계획 분석 |
| [03. 성능 모니터링](02-intermediate/03-performance-monitoring.md) | 슬로우 쿼리, 성능 지표, 통계 뷰 |
| [04. 백업·복구 전략](02-intermediate/04-backup-recovery-strategies.md) | 물리 백업, PITR, 보관 정책 |
| [05. 복제 기초](02-intermediate/05-replication-basics.md) | 동기/비동기 복제, 주요 구현체 |
| [06. 스키마 변경 관리](02-intermediate/06-schema-change-management.md) | 마이그레이션 도구, 온라인 DDL |
| [07. 클라우드 DB 인프라 구축과 접속](02-intermediate/07-cloud-db-infra-and-connection.md) | VPC/서브넷 그룹/보안 그룹, 인스턴스 생성, SSL/IAM 인증, 프라이빗 접속 |
| [08. 클라우드 매니지드 DB 기초](02-intermediate/08-cloud-managed-db-basics.md) | RDS/Cloud SQL 기본 운영 |
| [09. 명령어 대조표](02-intermediate/09-commands-cheatsheet.md) | 중급 단계 명령어 요약 |

### [03. 고급 (시니어 DBA / DB 아키텍트)](03-advanced/00-overview.md)

| 챕터 | 내용 |
|---|---|
| [00. 개요](03-advanced/00-overview.md) | 선수 지식, 이 단계의 목표, 지속적 성장 방향 |
| [01. 고급 성능 튜닝](03-advanced/01-advanced-performance-tuning.md) | 옵티마이저 내부, 파티셔닝, 캐싱 |
| [02. 고가용성과 페일오버](03-advanced/02-high-availability-and-failover.md) | HA 아키텍처, 자동 페일오버, Runbook |
| [03. 재해복구(DR)](03-advanced/03-disaster-recovery.md) | RPO/RTO, 멀티리전 복구, DR 훈련 |
| [04. 확장과 샤딩](03-advanced/04-scaling-and-sharding.md) | 샤딩 전략, 리드 리플리카, 커넥션 풀링 |
| [05. 보안과 컴플라이언스](03-advanced/05-security-and-compliance.md) | 암호화, 감사 로그, 컴플라이언스 |
| [06. 자동화와 IaC](03-advanced/06-automation-and-iac.md) | Terraform/Ansible, GitOps |
| [07. 클라우드 매니지드 DB 심화](03-advanced/07-cloud-managed-db-advanced.md) | Aurora/AlloyDB, 멀티리전, 비용 최적화 |
| [08. Kubernetes 기반 DB Operator](03-advanced/08-kubernetes-db-operators.md) | Percona XtraDB Cluster Operator, Oracle MySQL Operator, CloudNativePG |
| [09. 장애 대응과 포스트모템](03-advanced/09-incident-response-and-postmortem.md) | 인시던트 대응 프로세스, 사후분석 |
| [10. 명령어 대조표](03-advanced/10-commands-cheatsheet.md) | 고급 단계 명령어 요약 |

### 부록

| 문서 | 내용 |
|---|---|
| [DBMS 비교표](appendix/dbms-comparison-matrix.md) | PostgreSQL/MySQL/Oracle/MSSQL/클라우드 매니지드 서비스 전체 비교 |
| [용어집](appendix/glossary.md) | 학습서 전반의 핵심 용어 정리 |

## 이 학습서를 읽는 법

- 각 챕터(개요 제외)는 **핵심 개념 설명 → 주요 명령어/문법 → 실습 예제 → 체크리스트** 순서로 구성되어 있다. 체크리스트를 통과하면 다음 챕터로 넘어간다.
- 명령어는 기본적으로 **PostgreSQL → MySQL → Oracle** 순서로 병기하며, 차이가 클 때는 별도로 표기한다. 용어나 명령어가 헷갈리면 [DBMS 비교표](appendix/dbms-comparison-matrix.md)를 확인한다.
- 낯선 용어가 나오면 [용어집](appendix/glossary.md)에서 찾아보고, 관련 챕터로 이동해 더 깊이 학습한다.
- 각 단계의 `00-overview.md`에 있는 체크리스트로 스스로 단계 이동 준비가 되었는지 점검한다.
