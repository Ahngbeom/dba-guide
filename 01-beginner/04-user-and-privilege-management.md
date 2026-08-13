# 04. 사용자와 권한 관리

## 1. 핵심 개념 설명

데이터베이스는 여러 사람과 여러 프로그램이 함께 사용합니다. 만약 모두가 모든 데이터를 마음대로 읽고 지울 수 있다면 큰 사고가 납니다. 그래서 DBMS는 **누가(사용자)** **무엇을(대상)** **어떻게(권한)** 할 수 있는지를 통제합니다. 이 통제를 설계하고 관리하는 것이 DBA의 핵심 책임 중 하나입니다.

기본 개념은 세 가지입니다.

- **사용자/계정(User/Account)**: 데이터베이스에 접속하는 주체. 사람일 수도, 애플리케이션일 수도 있습니다.
- **권한(Privilege)**: 특정 작업을 할 수 있는 자격. 예: 어떤 테이블을 `SELECT`(조회)할 권한, `INSERT`(입력)할 권한.
- **역할(Role)**: 여러 권한을 하나로 묶은 꾸러미. 사용자마다 권한을 일일이 주는 대신, "읽기전용 역할"을 만들어 여러 사용자에게 한 번에 부여하면 관리가 편합니다.

권한 관리의 황금률은 **최소 권한 원칙(Principle of Least Privilege)** 입니다. 각 사용자에게 **꼭 필요한 만큼만** 권한을 주라는 뜻입니다. 예를 들어 통계 조회만 하는 프로그램에는 `SELECT` 권한만 주고, 데이터를 지우는 `DELETE` 권한은 주지 않습니다. 이렇게 하면 실수나 해킹으로 인한 피해를 최소화할 수 있습니다.

---

## 2. 주요 명령어/문법

권한 부여(`GRANT`)와 회수(`REVOKE`)는 표준 SQL이라 세 DBMS가 비슷하지만, **계정 생성** 문법은 차이가 있습니다.

### 계정 생성 / 삭제 / 비밀번호 변경



**Oracle**

```sql
-- 계정 생성
CREATE USER app_user IDENTIFIED BY secret123;

-- 접속(로그인) 권한을 별도로 줘야 실제 접속 가능
GRANT CREATE SESSION TO app_user;

-- 비밀번호 변경
ALTER USER app_user IDENTIFIED BY newsecret456;

-- 계정 삭제 (소유 객체까지 함께 삭제하려면 CASCADE)
DROP USER app_user CASCADE;
```

> **Oracle 주의**: 계정을 만들어도 `CREATE SESSION` 권한이 없으면 접속조차 안 됩니다. 이 점이 다른 DBMS와 크게 다릅니다.

### 권한 부여(GRANT) / 회수(REVOKE)

세 DBMS 공통에 가까운 형태입니다.

```sql
-- 특정 테이블에 대한 조회/입력 권한 부여
GRANT SELECT, INSERT ON employees TO app_user;

-- 조회/입력/수정/삭제 모두 부여
GRANT SELECT, INSERT, UPDATE, DELETE ON employees TO app_user;

-- 권한 회수
REVOKE INSERT ON employees FROM app_user;
```

DBMS별 "모든 권한" 및 스키마 단위 부여 차이:



```sql
-- Oracle: 테이블 대상 모든 권한
GRANT ALL ON employees TO app_user;
```

### 역할(Role) 활용



```sql
-- Oracle: 역할 생성 및 부여
CREATE ROLE readonly;
GRANT SELECT ON employees TO readonly;
GRANT readonly TO app_user;
```

---

## 3. 실습 예제



**시나리오: 통계 조회용 읽기전용 계정을 만들어 보자. 이 계정은 조회만 가능하고, 데이터 변경은 막혀 있어야 한다. (Oracle 기준)**

```sql
-- 1) 조회 대상 테이블이 있다고 가정 (현재 접속한 계정, 예: app_user 소유로 생성됨)
CREATE TABLE employees (id NUMBER PRIMARY KEY, name VARCHAR2(50), salary NUMBER);
INSERT INTO employees VALUES (1, '홍길동', 5000);
INSERT INTO employees VALUES (2, '김영희', 6000);
COMMIT;

-- 2) 읽기전용 계정 생성. Oracle은 계정을 만들어도 접속 권한이 별도로 필요하다
CREATE USER report_user IDENTIFIED BY report_pw;
GRANT CREATE SESSION TO report_user;

-- 3) 최소 권한 원칙: SELECT만 부여 (INSERT/UPDATE/DELETE는 주지 않음)
GRANT SELECT ON employees TO report_user;

-- 4) 이제 report_user로 접속해서 테스트 (sqlplus report_user/report_pw@orcl)
--    Oracle은 스키마가 곧 사용자 계정이라 공용 스키마가 없으므로, 소유자를 붙여 조회한다:
SELECT * FROM app_user.employees;                 -- OK

--    변경은 실패 (권한 없음 오류):
INSERT INTO app_user.employees VALUES (3, '이철수', 4500);
--   ERROR: ORA-01031: insufficient privileges

-- 5) 나중에 조회 권한마저 회수하려면 (테이블 소유 계정에서 실행)
REVOKE SELECT ON employees FROM report_user;
```

4번에서 조회는 되고 입력은 막히는 것이 바로 최소 권한 원칙의 실제 모습입니다. 다만 Oracle은 스키마가 곧 사용자 계정이므로, 다른 계정이 만든 테이블을 조회하려면 `소유자.테이블명`처럼 스키마를 명시해야 한다는 점이 PostgreSQL/MySQL과 다릅니다.

---

## 4. 체크리스트

- [ ] 사용자·권한·역할의 개념을 각각 설명할 수 있다.
- [ ] 최소 권한 원칙이 무엇이고 왜 중요한지 안다.
- [ ] PostgreSQL/MySQL/Oracle에서 계정을 생성·삭제할 수 있다.
- [ ] 계정의 비밀번호를 변경할 수 있다.
- [ ] Oracle에서 접속하려면 CREATE SESSION 권한이 필요하다는 점을 안다.
- [ ] GRANT로 특정 테이블에 특정 권한(SELECT 등)만 부여할 수 있다.
- [ ] REVOKE로 부여한 권한을 회수할 수 있다.
- [ ] 역할을 만들어 여러 권한을 묶고, 사용자에게 한 번에 부여할 수 있다.
