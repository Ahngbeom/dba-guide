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
| [08. Kubernetes 기반 DB Operator](03-advanced/08-kubernetes-db-operators.md) | Percona XtraDB Cluster Operator, Oracle MySQL Operator, CloudNativePG · [로컬 kind 실습](03-advanced/labs/oracle-mysql-operator-kind/README.md) |
| [09. 장애 대응과 포스트모템](03-advanced/09-incident-response-and-postmortem.md) | 인시던트 대응 프로세스, 사후분석 |
| [10. 명령어 대조표](03-advanced/10-commands-cheatsheet.md) | 고급 단계 명령어 요약 |

### 부록

| 문서 | 내용 |
|---|---|
| [DBMS 비교표](appendix/dbms-comparison-matrix.md) | PostgreSQL/MySQL/Oracle/MSSQL/클라우드 매니지드 서비스 전체 비교 |
| [용어집](appendix/glossary.md) | 학습서 전반의 핵심 용어 정리 |

## 학습 점검 (퀴즈/시험)

각 챕터를 실제로 이해했는지 스스로 검증할 수 있는 **TUI 퀴즈/시험** 기능을 제공한다. 이론 개념은 객관식으로, 명령어·실전 내용은 주관식/서술형으로 출제된다. 파이썬 표준 라이브러리만 사용하므로 별도 설치가 필요 없다.

```bash
./exam
```

이 한 줄이면 된다. 실행하면 TUI 안에서 순서대로 고른다:

1. **DBMS 선택** — 전체(공통 + 모든 벤더) / PostgreSQL / MySQL / Oracle. 특정 벤더를 고르면 공통 문항 + 그 벤더 문항만 출제된다.
2. **티어 선택** — 초급/중급/고급 (문제은행이 있는 티어만, 하나뿐이면 자동 생략).
3. **챕터 선택** — 해당 티어의 챕터.

이후 시험이 진행된다. 시험 중 조작:

- **객관식**: `↑`/`↓`로 보기 이동, `Enter`로 제출. 보기 순서는 매번 무작위로 섞인다.
- **주관식/서술형**: `Enter`로 입력창을 열어 답을 적는다. 입력창에서 `←`/`→`로 커서 이동(`Option+←/→`는 단어 단위), `Home`/`End`·`Backspace`/`Delete`로 중간 편집, `Esc`로 닫으면 **작성 내용은 초안으로 보존**되어 다시 열면 이어서 쓸 수 있다. 주관식은 자동 채점, 서술형은 모범답안을 보고 스스로 채점(`y`/`n`)한다.
- **서술형은 여러 줄로 쓸 수 있다**: `Shift+Enter`(또는 `Option+Enter`)로 줄을 바꾸고 `↑`/`↓`로 줄 사이를 이동한다. `Enter`는 제출이다. 터미널이 `Shift+Enter`를 구분하지 못하면 `Option+Enter`를 쓰면 된다.
- 입력창에서 `Option` 조합이 반응하지 않으면 터미널 설정 문제다(Ghostty `macos-option-as-alt = true`, iTerm2 *Left Option key: Esc+*, Terminal.app *Option 키를 Meta 키로 사용*). 설정과 무관하게 **`Ctrl+A`(줄 처음) · `Ctrl+E`(줄 끝) · `Ctrl+←`/`Ctrl+→`(단어 이동)** 는 항상 동작한다. 키가 이상하면 `./exam --keydebug`로 터미널이 보내는 값을 확인할 수 있다.
- **제출은 문항당 한 번으로 확정된다.** 채점과 동시에 정답이 공개되므로, 제출 후에는 답을 바꿀 수 없다(점수·연속 정답 조작 방지). 제출한 문항에서 `Enter`를 누르면 **다음 문항**으로 넘어가고, 마지막 문항에서는 **결과 화면**으로 간다.
- **공통**: `←`/`→`로 이전·다음 문항을 오갈 수 있고(이미 제출한 문항은 내 답·정답·해설을 읽기 전용으로 다시 볼 수 있다), `h`로 힌트를 보고, `m`으로 효과음/플래시를 끄고 켜며, `q`로 결과를 본다.
- 정답/오답 시 강조 배너와 화면 플래시(오답은 비프음)가 잠깐 재생되고, **연속 정답**이 이어지면 상단에 `🔥 연속 N`이 표시되며 3·5·10연속에서 축하 배너가 뜬다. 효과가 거슬리면 `m`으로 끄거나 `./exam --no-effects`로 시작한다.
- 상단에 실시간 진행상황(정답·오답·정답률·**등급 A~F**)과 문항별 점 스트립이 표시된다. 자동채점 정답률 70% 미만이면 재학습을 권장한다.
- 시험을 마치면 결과 화면에서 **다른 챕터 (같은 티어) / 처음부터 다시 선택 / 종료**를 고를 수 있어, 나가지 않고 이어서 여러 챕터를 볼 수 있다.
- 각 시험 결과는 저장소 내 `.exam-results/results.jsonl`에 자동 저장된다(개인 학습 기록이라 **git에 커밋되지 않음** — `.gitignore` 처리). 챕터를 다시 고를 때 그 챕터의 `[지난 최고 등급·정답률]`이 함께 표시된다.

<details>
<summary>바로 특정 챕터/옵션으로 실행하기 (선택 사항)</summary>

```bash
./exam exams/01-beginner/01-rdbms-fundamentals.json   # 특정 챕터 바로
./exam 01-beginner                                    # 초급 티어 전체를 한 번에
./exam --dbms postgresql                              # DBMS 선택만 건너뛰고 티어·챕터는 TUI에서
./exam 01-beginner/02-sql-basics.json --shuffle       # 문항 순서 섞기
```

`./exam`은 `python3 scripts/exam.py`의 단축이며, 인자·옵션을 그대로 받는다.
</details>

- 문항은 `exams/<티어>/<챕터>.json`에 있다. 문항 추가·작성 규칙과 시드 자동 생성 워크플로는 [`docs/exam-authoring.md`](docs/exam-authoring.md)를 참고한다.

> 현재 **초급(01-beginner) 6개 챕터**의 문제은행이 제공된다. 중급·고급 티어는 같은 포맷으로 점진 추가된다.

## 이 학습서를 읽는 법

- 각 챕터(개요 제외)는 **핵심 개념 설명 → 주요 명령어/문법 → 실습 예제 → 체크리스트** 순서로 구성되어 있다. 체크리스트를 통과하면 다음 챕터로 넘어간다.
- 명령어는 기본적으로 **PostgreSQL → MySQL → Oracle** 순서로 병기하며, 차이가 클 때는 별도로 표기한다. 용어나 명령어가 헷갈리면 [DBMS 비교표](appendix/dbms-comparison-matrix.md)를 확인한다.
- 낯선 용어가 나오면 [용어집](appendix/glossary.md)에서 찾아보고, 관련 챕터로 이동해 더 깊이 학습한다.
- 각 단계의 `00-overview.md`에 있는 체크리스트로 스스로 단계 이동 준비가 되었는지 점검한다.
