# 02. SQL 기초

## 1. 핵심 개념 설명

**SQL(Structured Query Language)** 은 관계형 데이터베이스와 대화하기 위한 표준 언어입니다. 사람이 한국어로 대화하듯, DBMS에게는 SQL로 "이 테이블을 만들어라", "이 데이터를 보여 달라"고 지시합니다. PostgreSQL·MySQL·Oracle은 모두 이 SQL을 사용하므로, 하나를 배우면 나머지도 대부분 통합니다.

SQL 명령은 하는 일에 따라 네 가지 부류로 나눕니다. 이 분류를 알아 두면 명령을 체계적으로 이해할 수 있습니다.

- **DDL (Data Definition Language, 데이터 정의어)**: 테이블 같은 **구조**를 만들고 바꾸고 지운다. → `CREATE`, `ALTER`, `DROP`
- **DML (Data Manipulation Language, 데이터 조작어)**: 테이블 안의 **데이터**를 조회·추가·수정·삭제한다. → `SELECT`, `INSERT`, `UPDATE`, `DELETE`
- **DCL (Data Control Language, 데이터 제어어)**: **권한**을 주고 뺏는다. → `GRANT`, `REVOKE`
- **TCL (Transaction Control Language, 트랜잭션 제어어)**: 작업을 **확정하거나 되돌린다**. → `COMMIT`, `ROLLBACK`

DBA는 이 네 가지를 모두 다룹니다. 개발자는 주로 DML을 쓰지만, DBA는 DDL로 구조를 설계하고, DCL로 접근을 통제하며, TCL로 데이터 안전을 관리합니다. 이 장에서는 각 부류의 가장 기본적인 문법을 익힙니다.

---

## 2. 주요 명령어/문법

세 DBMS의 기본 문법은 표준 SQL을 따르므로 거의 같습니다. 자료형 이름 등 일부만 다르며, 차이가 큰 부분은 별도로 표기합니다.

### DDL — 구조 만들기

```sql
-- 테이블 생성
CREATE TABLE employees (
    id     INT PRIMARY KEY,
    name   VARCHAR(50) NOT NULL,
    salary INT
);

-- 컬럼 추가 (구조 변경)
ALTER TABLE employees ADD COLUMN hire_date DATE;

-- 컬럼 삭제
ALTER TABLE employees DROP COLUMN salary;

-- 테이블 삭제
DROP TABLE employees;
```

> **자료형 차이 주의**: 문자열은 세 DBMS 모두 `VARCHAR(n)`을 쓸 수 있습니다. 다만 대용량 텍스트는 PostgreSQL `TEXT`, MySQL `TEXT`, Oracle `CLOB`으로 다릅니다. Oracle에서는 가변 문자열에 `VARCHAR2(n)`을 관례적으로 사용합니다.

### DML — 데이터 다루기

```sql
-- 데이터 추가 (INSERT)
INSERT INTO employees (id, name, salary) VALUES (1, '홍길동', 5000);

-- 여러 행 한 번에 추가 (세 DBMS 모두 지원)
INSERT INTO employees (id, name, salary) VALUES
    (2, '김영희', 6000),
    (3, '이철수', 4500);

-- 데이터 조회 (SELECT)
SELECT id, name, salary FROM employees;              -- 특정 컬럼만
SELECT * FROM employees;                             -- 전체 컬럼
SELECT * FROM employees WHERE salary >= 5000;        -- 조건 필터
SELECT * FROM employees ORDER BY salary DESC;        -- 급여 높은 순 정렬
SELECT * FROM employees WHERE name LIKE '김%';        -- '김'으로 시작하는 이름

-- 데이터 수정 (UPDATE) — WHERE를 반드시 신경 쓸 것!
UPDATE employees SET salary = 5500 WHERE id = 1;

-- 데이터 삭제 (DELETE) — WHERE 없으면 전체 삭제되니 주의!
DELETE FROM employees WHERE id = 3;
```

> **초보자 필수 경고**: `UPDATE`와 `DELETE`에서 `WHERE` 절을 빠뜨리면 **테이블의 모든 행**이 영향을 받습니다. 실무 사고의 단골 원인입니다. 실행 전 항상 WHERE 조건을 확인하세요.

### DCL — 권한 제어

```sql
-- 특정 사용자에게 조회/입력 권한 부여
GRANT SELECT, INSERT ON employees TO app_user;

-- 권한 회수
REVOKE INSERT ON employees FROM app_user;
```

권한 관리의 자세한 내용은 04장에서 다룹니다.

### TCL — 트랜잭션 제어



```sql
-- Oracle: DML 실행 시 트랜잭션이 자동으로 시작됨 (BEGIN 불필요)
UPDATE employees SET salary = 7000 WHERE id = 2;
COMMIT;    -- 변경을 확정 (영구 저장)

-- 되돌리기 예시
DELETE FROM employees WHERE id = 2;
ROLLBACK;  -- 방금 DELETE를 취소 (삭제 안 됨)
```

> **자동 커밋(autocommit) 차이**: MySQL과 PostgreSQL의 기본 클라이언트는 대개 각 문장을 자동으로 커밋합니다. `BEGIN`으로 트랜잭션을 열면 `COMMIT` 전까지 확정되지 않습니다. Oracle의 SQL*Plus는 DML이 자동 커밋되지 않으므로 반드시 `COMMIT`을 실행해야 저장됩니다. **DDL(CREATE/ALTER/DROP)은 Oracle·MySQL에서 자동 커밋되어 ROLLBACK으로 되돌릴 수 없다**는 점도 기억하세요.

---

## 3. 실습 예제

**시나리오: 직원 테이블을 만들고, 데이터를 넣고, 조회·수정하고, 트랜잭션으로 되돌리기까지 해 보자.**

```sql
-- 1) 테이블 생성 (DDL)
CREATE TABLE employees (
    id     INT PRIMARY KEY,
    name   VARCHAR(50) NOT NULL,
    salary INT
);

-- 2) 데이터 입력 (DML)
INSERT INTO employees (id, name, salary) VALUES
    (1, '홍길동', 5000),
    (2, '김영희', 6000),
    (3, '이철수', 4500);

-- 3) 조회 — 급여 5000 이상인 직원을 급여 높은 순으로
SELECT name, salary
FROM employees
WHERE salary >= 5000
ORDER BY salary DESC;
--   결과: 김영희(6000), 홍길동(5000)

-- 4) 수정 — 이철수 급여 인상
UPDATE employees SET salary = 5000 WHERE id = 3;

-- 5) 트랜잭션으로 실수 되돌리기
BEGIN;
DELETE FROM employees;          -- 앗! WHERE를 깜빡해서 전체 삭제 시도
SELECT COUNT(*) FROM employees; -- 결과: 0 (아직 확정 전)
ROLLBACK;                       -- 되돌리기!
SELECT COUNT(*) FROM employees; -- 결과: 3 (원상 복구됨)

-- 6) 구조 변경 — 입사일 컬럼 추가 (DDL)
ALTER TABLE employees ADD COLUMN hire_date DATE;
```

5번 흐름이 TCL의 진가입니다. 확정(COMMIT) 전이라면 실수를 ROLLBACK으로 되돌릴 수 있습니다. 반대로 한 번 COMMIT하면 되돌릴 수 없으니, 중요한 변경 전에는 트랜잭션을 열어 두는 습관이 안전합니다.

---

## 4. 체크리스트

- [ ] DDL / DML / DCL / TCL이 각각 무슨 일을 하는지 구분할 수 있다.
- [ ] CREATE / ALTER / DROP로 테이블을 만들고 바꾸고 지울 수 있다.
- [ ] INSERT로 한 행과 여러 행을 입력할 수 있다.
- [ ] SELECT에 WHERE, ORDER BY, LIKE를 붙여 원하는 데이터를 걸러 낼 수 있다.
- [ ] UPDATE와 DELETE에서 WHERE를 빠뜨리면 안 되는 이유를 안다.
- [ ] BEGIN / COMMIT / ROLLBACK으로 트랜잭션을 제어할 수 있다.
- [ ] Oracle SQL*Plus에서 DML 후 COMMIT을 해야 저장된다는 점을 안다.
- [ ] DDL은 대부분 자동 커밋되어 되돌릴 수 없다는 점을 안다.
