# 07. 명령어 대조표 (치트시트)

이 문서는 초급 단계에서 다룬 PostgreSQL / MySQL / Oracle 명령어를 한 페이지에서 비교할 수 있게 정리한 대조표입니다. 세 DBMS는 표준 SQL을 공유하므로 많은 부분이 같지만, **접속·계정·백업·모니터링**처럼 벤더마다 다른 부분이 실무에서 자주 헷갈립니다. 필요할 때 빠르게 찾아보는 용도로 활용하세요.

> 표기 규칙: 셸(터미널)에서 실행하는 명령은 `$`, SQL 클라이언트 안에서 실행하는 명령은 `SQL`로 구분해 설명합니다.

---

## 접속과 클라이언트

| 항목 | PostgreSQL | MySQL | Oracle |
|------|------------|-------|--------|
| 기본 포트 | 5432 | 3306 | 1521 |
| 클라이언트 | `psql` | `mysql` | `sqlplus` |
| 접속 (`$`) | `psql -h localhost -p 5432 -U postgres -d dbname` | `mysql -h localhost -P 3306 -u root -p` | `sqlplus system/pw@localhost:1521/XEPDB1` |
| DB 목록 | `\l` | `SHOW DATABASES;` | `SELECT name FROM v$database;` |
| DB 전환/선택 | `\c dbname` | `USE dbname;` | (서비스 접속으로 전환) |
| 테이블 목록 | `\dt` | `SHOW TABLES;` | `SELECT table_name FROM user_tables;` |
| 사용자 목록 | `\du` | `SELECT user FROM mysql.user;` | `SELECT username FROM all_users;` |
| 종료 | `\q` | `EXIT;` | `EXIT;` |

---

## 서비스 시작 / 중지 (셸)

| 항목 | PostgreSQL | MySQL | Oracle (Docker 예) |
|------|------------|-------|--------|
| 시작 | `brew services start postgresql@16` / `systemctl start postgresql` | `brew services start mysql` / `systemctl start mysql` | `docker start oracle-xe` |
| 중지 | `brew services stop postgresql@16` / `systemctl stop postgresql` | `systemctl stop mysql` | `docker stop oracle-xe` |
| 상태 | `systemctl status postgresql` | `systemctl status mysql` | `docker ps` |

---

## DDL — 구조 정의 (대부분 공통)

| 작업 | 공통 SQL |
|------|----------|
| 테이블 생성 | `CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR(50));` |
| 컬럼 추가 | `ALTER TABLE t ADD COLUMN age INT;` |
| 컬럼 삭제 | `ALTER TABLE t DROP COLUMN age;` |
| 테이블 삭제 | `DROP TABLE t;` |

| 자료형 차이 | PostgreSQL | MySQL | Oracle |
|------|------------|-------|--------|
| 가변 문자열 | `VARCHAR(n)` | `VARCHAR(n)` | `VARCHAR2(n)` |
| 대용량 텍스트 | `TEXT` | `TEXT` | `CLOB` |
| 자동 증가 키 | `SERIAL` / `GENERATED ... AS IDENTITY` | `AUTO_INCREMENT` | `GENERATED ... AS IDENTITY` |

---

## DML — 데이터 조작 (공통)

| 작업 | 공통 SQL |
|------|----------|
| 입력 | `INSERT INTO t (id, name) VALUES (1, '홍길동');` |
| 조회 | `SELECT * FROM t WHERE id = 1 ORDER BY name;` |
| 수정 | `UPDATE t SET name = '김영희' WHERE id = 1;` |
| 삭제 | `DELETE FROM t WHERE id = 1;` |

---

## TCL — 트랜잭션 (공통, 자동커밋 동작만 차이)

| 작업 | 공통 SQL | 비고 |
|------|----------|------|
| 시작 | `BEGIN;` | Oracle은 DML 실행 시 자동 시작 |
| 확정 | `COMMIT;` | Oracle SQL*Plus는 반드시 수동 COMMIT 필요 |
| 되돌리기 | `ROLLBACK;` | DDL은 대부분 자동 커밋되어 되돌리기 불가 |

---

## 계정 / 권한 관리

| 작업 | PostgreSQL | MySQL | Oracle |
|------|------------|-------|--------|
| 계정 생성 | `CREATE USER u WITH PASSWORD 'pw';` | `CREATE USER 'u'@'localhost' IDENTIFIED BY 'pw';` | `CREATE USER u IDENTIFIED BY pw;` |
| 접속 권한 | (LOGIN 기본 포함) | (기본 접속 가능) | `GRANT CREATE SESSION TO u;` (필수) |
| 비번 변경 | `ALTER USER u WITH PASSWORD 'pw';` | `ALTER USER 'u'@'localhost' IDENTIFIED BY 'pw';` | `ALTER USER u IDENTIFIED BY pw;` |
| 계정 삭제 | `DROP USER u;` | `DROP USER 'u'@'localhost';` | `DROP USER u CASCADE;` |
| 권한 부여 | `GRANT SELECT ON t TO u;` | `GRANT SELECT ON db.t TO 'u'@'localhost';` | `GRANT SELECT ON t TO u;` |
| 권한 회수 | `REVOKE SELECT ON t FROM u;` | `REVOKE SELECT ON db.t FROM 'u'@'localhost';` | `REVOKE SELECT ON t FROM u;` |
| 역할 생성 | `CREATE ROLE r;` | `CREATE ROLE 'r';` | `CREATE ROLE r;` |

> MySQL은 권한 변경 후 `FLUSH PRIVILEGES;`가 필요한 경우가 있습니다.

---

## 백업 / 복원 (셸에서 실행)

| 작업 | PostgreSQL | MySQL | Oracle |
|------|------------|-------|--------|
| 전체 백업 | `pg_dump -U postgres -d db > db.sql` | `mysqldump -u root -p db > db.sql` | `expdp system/pw schemas=HR directory=dp_dir dumpfile=hr.dmp` |
| 특정 테이블 | `pg_dump -U postgres -d db -t t > t.sql` | `mysqldump -u root -p db t > t.sql` | (schemas/tables 옵션 지정) |
| 복원 | `psql -U postgres -d db < db.sql` | `mysql -u root -p db < db.sql` | `impdp system/pw schemas=HR directory=dp_dir dumpfile=hr.dmp` |
| 압축 포맷 | `pg_dump -Fc ... > db.dump` → `pg_restore` | (`--compress` 옵션) | (Data Pump 기본 지원) |

---

## 모니터링 / 상태 점검

| 항목 | PostgreSQL | MySQL | Oracle |
|------|------------|-------|--------|
| 현재 세션 | `SELECT * FROM pg_stat_activity;` | `SHOW PROCESSLIST;` | `SELECT * FROM v$session;` |
| 세션 종료 | `SELECT pg_terminate_backend(pid);` | `KILL id;` | `ALTER SYSTEM KILL SESSION 'sid,serial#';` |
| DB 크기 | `SELECT pg_size_pretty(pg_database_size('db'));` | (information_schema.tables 합산) | (dba_segments 합산) |
| 로그 위치 | `SHOW log_directory;` | `SHOW VARIABLES LIKE 'log_error';` | `SELECT value FROM v$diag_info;` |
| 디스크(OS) | `df -h` | `df -h` | `df -h` |

---

## 자주 쓰는 셸 보조 명령 (공통)

| 목적 | 명령 |
|------|------|
| 로그 실시간 보기 | `tail -f 로그파일` |
| 로그 마지막 50줄 | `tail -n 50 로그파일` |
| 디스크 여유 확인 | `df -h` |
| 디렉터리 용량 | `du -sh 경로` |
| 실행 중 멈추기 | `Ctrl + C` |

---

이 치트시트는 초급 단계 학습을 마친 뒤에도 오래 참고하게 됩니다. 세 DBMS를 오가며 일할 때 "이 명령이 저쪽에서는 뭐였지?" 싶을 때마다 이 표를 펼쳐 보세요. 중급 단계로 가면 여기에 인덱스·실행계획·복제 관련 명령이 추가됩니다.
