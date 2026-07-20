# DBMS별 브랜치 분기 전략

이 문서는 `postgresql`/`mysql`/`oracle` 단일 벤더 브랜치를 어떻게 만들고 유지하는지 설명한다. 대상 독자는 이 저장소에 챕터를 쓰거나 브랜치를 재생성하는 사람이다.

## 왜 브랜치를 나누는가

`main`은 지금처럼 PostgreSQL/MySQL/Oracle을 한 챕터 안에 병기하는 **vendor-neutral 원본**을 유지한다. 하지만 특정 DBMS 하나만 담당하는 독자는 다른 두 벤더 내용까지 헤치고 읽어야 하는 불편이 있다. `postgresql`/`mysql`/`oracle` 브랜치는 이 독자를 위해 **main에서 자동으로 걸러낸 파생 뷰**다.

- `main`만 고치면 된다. 벤더 브랜치는 사람이 직접 편집하지 않는다 — 편집해도 다음 재생성 때 덮어써진다.
- 재생성은 `scripts/generate-branch.sh`를 수동 실행해 트리거한다. CI는 없다(이 저장소는 학습서이지 소프트웨어 프로젝트가 아니다).

## 마커 문법

챕터 본문에서 특정 DBMS에만 해당하는 문단·코드블록·리스트 항목을 HTML 주석으로 감싼다. GitHub Markdown 렌더링에는 보이지 않는다.

```markdown
<!-- dbms:postgresql -->
**PostgreSQL** — ...
```sql
CREATE ROLE app_user WITH LOGIN PASSWORD 'secret123';
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
**MySQL** — ...
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
**Oracle** — ...
<!-- /dbms:oracle -->
```

규칙:

- 벤더 값은 `postgresql`, `mysql`, `oracle` 세 가지다. 이 외에 **`neutral`** 이라는 특수 값이 있다 — 실제 브랜치 이름과 절대 일치하지 않으므로 어떤 벤더 브랜치를 생성하든 항상 제거된다. `main`에서만 보여야 하는 문구(예: "이 저장소는 특정 DBMS에 종속되지 않는다"처럼 벤더 브랜치에서는 모순이 되는 소개 문단)를 감쌀 때 쓴다. `README.md` 상단이 이 패턴의 실제 예시다.
- 마커가 없는 텍스트(개념 설명, 공통 SQL 등)는 모든 브랜치에 그대로 남는다 — 애매하면 마커를 씌우지 않는 쪽을 기본으로 한다.
- 섹션 전체(`## 주요 명령어/문법` 등)를 통째로 감싸지 말고, **DBMS 특정적인 문단/항목 단위**로만 감싼다. 예: 체크리스트에서 "Oracle에서 접속하려면 CREATE SESSION 권한이 필요하다" 항목 하나만 `dbms:oracle`로 감싸고, 나머지 공통 항목은 그대로 둔다.
- 마커는 중첩되지 않는다. 열고 닫는 이름이 반드시 일치해야 하며, 파일 끝까지 닫히지 않은 마커가 있으면 생성 스크립트가 오류를 낸다.

## 필터링하지 않는 파일

`*-commands-cheatsheet.md`와 `appendix/dbms-comparison-matrix.md`는 DBMS를 열(column)로 둔 비교표다. 애초에 "비교"가 목적인 문서이므로 마커를 넣지 않고 **모든 브랜치에서 원본 그대로 유지**한다. `appendix/glossary.md`도 마찬가지로 필터링 대상이 아니다.

## 생성 스크립트

```bash
./scripts/generate-branch.sh postgresql   # 또는 mysql / oracle
```

동작 방식:

1. 현재 작업 트리가 클린한지 확인한다.
2. `git worktree add ../<repo>-<dbms> -B <dbms> main` — 별도 워크트리에 `main` 기준으로 브랜치를 새로 만들거나 재설정한다. 현재 체크아웃 중인 브랜치는 건드리지 않는다.
3. 워크트리의 모든 `*.md` 파일에 `scripts/filter_dbms.py`를 적용해 대상 DBMS가 아닌 마커 블록을 제거한다. 마커가 없는 파일은 바이트 단위로 그대로 남는다.
4. 변경 사항을 그 워크트리에 커밋한다.
5. **push는 스크립트가 하지 않는다.** 결과를 검토한 뒤 안내된 명령으로 직접 push한다. 매번 main의 현재 시점에서 새로 재생성되는 브랜치라 첫 push 이후로는 fast-forward가 아닌 경우가 대부분이다 — `git push --force-with-lease origin <dbms>`를 쓴다.

## 챕터별 마킹 진행 상황

마커가 없는 챕터는 모든 브랜치에서 3개 DBMS가 그대로 보이는 안전한 폴백 상태다. 아래 표는 마킹 완료 여부를 추적한다 — 챕터를 마킹하면 이 표를 갱신한다.

| 파일 | 마킹 상태 |
|---|---|
| `README.md` | ✅ 완료 |
| `01-beginner/00-overview.md` | ✅ 확인 완료 (DBMS 전용 콘텐츠 없음, 마커 불필요) |
| `01-beginner/01-rdbms-fundamentals.md` | ✅ 완료 |
| `01-beginner/02-sql-basics.md` | ✅ 완료 |
| `01-beginner/03-installation-and-access.md` | ✅ 완료 |
| `01-beginner/04-user-and-privilege-management.md` | ✅ 완료 |
| `01-beginner/05-backup-basics.md` | ✅ 완료 |
| `01-beginner/06-basic-monitoring.md` | ✅ 완료 |
| `01-beginner/07-commands-cheatsheet.md` | 대상 아님 (비교표) |
| `02-intermediate/00-overview.md` | ✅ 확인 완료 (DBMS 전용 콘텐츠 없음, 마커 불필요) |
| `02-intermediate/01-transaction-and-locking.md` | ✅ 완료 |
| `02-intermediate/02-indexing-and-query-tuning.md` | ✅ 완료 |
| `02-intermediate/03-performance-monitoring.md` | ✅ 완료 |
| `02-intermediate/04-backup-recovery-strategies.md` | ✅ 완료 |
| `02-intermediate/05-replication-basics.md` | ✅ 완료 |
| `02-intermediate/06-schema-change-management.md` | ✅ 완료 |
| `02-intermediate/07-cloud-db-infra-and-connection.md` | ✅ 완료 |
| `02-intermediate/08-cloud-managed-db-basics.md` | ✅ 완료 |
| `02-intermediate/09-commands-cheatsheet.md` | 대상 아님 (비교표) |
| `03-advanced/00-overview.md` | ✅ 확인 완료 (DBMS 전용 콘텐츠 없음, 마커 불필요) |
| `03-advanced/01-advanced-performance-tuning.md` | ✅ 완료 |
| `03-advanced/02-high-availability-and-failover.md` | ✅ 완료 |
| `03-advanced/03-disaster-recovery.md` | ✅ 완료 |
| `03-advanced/04-scaling-and-sharding.md` | ✅ 완료 |
| `03-advanced/05-security-and-compliance.md` | ✅ 완료 |
| `03-advanced/06-automation-and-iac.md` | ✅ 완료 |
| `03-advanced/07-cloud-managed-db-advanced.md` | ✅ 완료 |
| `03-advanced/08-kubernetes-db-operators.md` | ✅ 완료 |
| `03-advanced/09-incident-response-and-postmortem.md` | ✅ 완료 |
| `03-advanced/10-commands-cheatsheet.md` | 대상 아님 (비교표) |
| `03-advanced/labs/oracle-mysql-operator-kind/README.md` | ✅ 완료 |
| `appendix/dbms-comparison-matrix.md` | 대상 아님 (비교표) |
| `appendix/glossary.md` | 대상 아님 (비교표) |
