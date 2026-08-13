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


**MySQL** — 계정은 `'사용자'@'접속허용호스트'` 형태로 지정합니다.

```sql
-- 계정 생성 (localhost에서만 접속 허용)
CREATE USER 'app_user'@'localhost' IDENTIFIED BY 'secret123';

-- 모든 호스트에서 접속 허용하려면 '%'
CREATE USER 'app_user'@'%' IDENTIFIED BY 'secret123';

-- 비밀번호 변경
ALTER USER 'app_user'@'localhost' IDENTIFIED BY 'newsecret456';

-- 계정 삭제
DROP USER 'app_user'@'localhost';
```


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
-- MySQL: 특정 데이터베이스의 모든 테이블에 모든 권한
GRANT ALL PRIVILEGES ON mydb.* TO 'app_user'@'localhost';
FLUSH PRIVILEGES;    -- MySQL은 권한 변경 후 반영 명령을 쓰기도 함
```


### 역할(Role) 활용


```sql
-- MySQL 8.0+: 역할 생성 및 부여
CREATE ROLE 'readonly';
GRANT SELECT ON mydb.* TO 'readonly';
GRANT 'readonly' TO 'app_user'@'localhost';
```


---

## 3. 실습 예제


**시나리오: 통계 조회용 읽기전용 계정을 만들어 보자. 이 계정은 조회만 가능하고, 데이터 변경은 막혀 있어야 한다. (MySQL 기준)**

```sql
-- 1) 조회 대상 테이블이 있다고 가정 (mydb 데이터베이스를 사용 중이라고 가정)
CREATE TABLE employees (id INT PRIMARY KEY, name VARCHAR(50), salary INT);
INSERT INTO employees VALUES (1, '홍길동', 5000), (2, '김영희', 6000);

-- 2) 읽기전용 계정 생성 (localhost에서만 접속 허용)
CREATE USER 'report_user'@'localhost' IDENTIFIED BY 'report_pw';

-- 3) 최소 권한 원칙: SELECT만 부여 (INSERT/UPDATE/DELETE는 주지 않음)
GRANT SELECT ON mydb.employees TO 'report_user'@'localhost';

-- 4) 이제 report_user로 접속해서 테스트 (mysql -u report_user -p)
--    조회는 성공:
SELECT * FROM employees;                  -- OK

--    변경은 실패 (권한 없음 오류):
INSERT INTO employees VALUES (3, '이철수', 4500);
--   ERROR 1142 (42000): INSERT command denied to user 'report_user'@'localhost' for table 'employees'

-- 5) 나중에 조회 권한마저 회수하려면
REVOKE SELECT ON mydb.employees FROM 'report_user'@'localhost';
```

4번에서 조회는 되고 입력은 막히는 것이 바로 최소 권한 원칙의 실제 모습입니다. DBA는 이렇게 각 계정에 필요한 권한만 골라 부여합니다.


---

## 4. 체크리스트

- [ ] 사용자·권한·역할의 개념을 각각 설명할 수 있다.
- [ ] 최소 권한 원칙이 무엇이고 왜 중요한지 안다.
- [ ] PostgreSQL/MySQL/Oracle에서 계정을 생성·삭제할 수 있다.
- [ ] 계정의 비밀번호를 변경할 수 있다.
- [ ] MySQL 계정은 `'사용자'@'호스트'` 형태로 식별되며, 접속 허용 호스트에 따라 같은 이름도 별개 계정으로 취급된다는 점을 안다.
- [ ] GRANT로 특정 테이블에 특정 권한(SELECT 등)만 부여할 수 있다.
- [ ] REVOKE로 부여한 권한을 회수할 수 있다.
- [ ] 역할을 만들어 여러 권한을 묶고, 사용자에게 한 번에 부여할 수 있다.
