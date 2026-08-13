# 05. 백업 기초

## 1. 핵심 개념 설명

DBA의 여러 임무 중 **가장 중요한 하나를 꼽으라면 단연 백업**입니다. 성능이 조금 느린 것은 참을 수 있지만, 데이터가 사라지면 회사가 무너질 수도 있기 때문입니다. "백업이 없는 DBA는 언젠가 반드시 후회한다"는 말이 있을 정도입니다. 실수로 테이블을 지웠거나, 디스크가 고장 났거나, 랜섬웨어에 걸렸을 때, 우리를 구해 주는 것은 오직 백업뿐입니다.

백업은 크게 두 종류로 나뉩니다.

- **논리 백업(Logical Backup)**: 데이터를 SQL 문(`CREATE TABLE`, `INSERT` 등)이나 텍스트 형태로 뽑아내는 방식. 사람이 읽을 수 있고, 특정 테이블만 골라 백업·복원하기 좋으며, 다른 버전이나 다른 서버로 옮기기 편합니다. 다만 데이터가 크면 느립니다. 대표 도구: `pg_dump`, `mysqldump`, `expdp`.
- **물리 백업(Physical Backup)**: 데이터베이스 파일 자체를 통째로 복사하는 방식. 매우 빠르고 대용량에 유리하지만, 초급 범위를 넘어서므로 중급 단계에서 다룹니다.

이 장에서는 초급 DBA가 반드시 익혀야 할 **논리 백업**에 집중합니다. 논리 백업은 개념이 단순하고, 작은 데이터베이스에서 바로 써먹을 수 있어 첫 백업으로 안성맞춤입니다.

기억할 점: **백업은 만들어 두는 것으로 끝이 아니라, 실제로 복원이 되는지 확인해야 진짜 백업입니다.** 복원을 테스트하지 않은 백업은 없는 것과 마찬가지일 수 있습니다.

---

## 2. 주요 명령어/문법

논리 백업 도구는 대부분 **DBMS 서버 프로그램이 아니라, 터미널(셸)에서 실행하는 별도 명령어**입니다. 따라서 `bash`에서 실행합니다.

### PostgreSQL — pg_dump / pg_restore / psql

```bash
# 1) 데이터베이스 전체를 SQL 텍스트 파일로 백업
pg_dump -h localhost -U postgres -d practice > practice_backup.sql

# 2) 특정 테이블만 백업
pg_dump -h localhost -U postgres -d practice -t employees > employees_backup.sql

# 3) 복원 (SQL 텍스트 백업은 psql로 되돌림)
psql -h localhost -U postgres -d practice < practice_backup.sql

# 4) 압축된 커스텀 포맷으로 백업 (pg_restore로 복원, 선택적 복원 가능)
pg_dump -h localhost -U postgres -d practice -Fc > practice_backup.dump
pg_restore -h localhost -U postgres -d practice practice_backup.dump
```



### 세 도구 한눈에 비교

| 작업 | PostgreSQL | MySQL | Oracle |
|------|------------|-------|--------|
| 백업 | `pg_dump` | `mysqldump` | `expdp` |
| 복원 | `psql` / `pg_restore` | `mysql` | `impdp` |
| 실행 위치 | 터미널(bash) | 터미널(bash) | 터미널(bash) |

---

## 3. 실습 예제

**시나리오: 연습용 데이터베이스를 백업하고, 데이터를 일부러 지운 뒤, 백업으로 복원해 보자. (PostgreSQL 기준)**

```bash
# 1) 백업 대상 준비 — psql로 데이터 넣기 (생략 가능, 이미 있다고 가정)
psql -h localhost -U postgres -d practice -c \
  "CREATE TABLE IF NOT EXISTS employees (id INT PRIMARY KEY, name VARCHAR(50));
   INSERT INTO employees VALUES (1,'홍길동'),(2,'김영희');"

# 2) 백업 파일 생성
pg_dump -h localhost -U postgres -d practice -t employees > employees_backup.sql

# 3) 백업 파일이 잘 만들어졌는지 확인 (INSERT 문이 보이면 성공)
head -n 30 employees_backup.sql

# 4) 사고 발생! 실수로 테이블 삭제
psql -h localhost -U postgres -d practice -c "DROP TABLE employees;"

# 5) 백업으로 복원
psql -h localhost -U postgres -d practice < employees_backup.sql

# 6) 복원 확인 — 데이터가 돌아왔는지 조회
psql -h localhost -U postgres -d practice -c "SELECT * FROM employees;"
#   결과에 홍길동, 김영희가 다시 보이면 복원 성공!
```

이 실습의 핵심은 **6번에서 복원을 직접 확인**하는 것입니다. 백업만 하고 복원을 안 해 봤다면, 실제 사고 때 백업이 쓸모없을 수도 있습니다. 정기적으로 "복원 훈련"을 하는 것이 좋은 DBA의 습관입니다.



---

## 4. 체크리스트

- [ ] 백업이 DBA 업무에서 왜 가장 중요한지 설명할 수 있다.
- [ ] 논리 백업과 물리 백업의 차이를 안다.
- [ ] pg_dump / mysqldump / expdp가 각각 어느 DBMS의 논리 백업 도구인지 안다.
- [ ] 이 도구들이 SQL 클라이언트가 아니라 터미널(bash)에서 실행된다는 점을 안다.
- [ ] 데이터베이스 전체와 특정 테이블만 골라 백업할 수 있다.
- [ ] 백업 파일로 복원(psql/mysql/impdp)할 수 있다.
- [ ] 백업 후 반드시 복원이 되는지 확인해야 한다는 원칙을 이해했다.
