# 03. 설치와 접속

## 1. 핵심 개념 설명

지금까지는 이론과 SQL 문법을 배웠습니다. 이제 실제로 DBMS를 컴퓨터에 설치하고 접속해 봅니다. DBA에게 설치·접속은 가장 기본적인 시작점입니다. 서버가 켜져 있어야 데이터베이스를 쓸 수 있고, 접속할 수 있어야 명령을 내릴 수 있기 때문입니다.

DBMS는 크게 두 부분으로 나뉩니다. **서버(server)** 는 실제 데이터를 저장하고 요청을 처리하는 프로그램으로, 백그라운드에서 계속 돌아갑니다. 이를 **데몬(daemon)** 또는 **서비스(service)** 라고 부릅니다. **클라이언트(client)** 는 서버에 접속해 명령을 주고받는 도구입니다. 우리가 터미널에서 실행하는 `psql`, `mysql`, `sqlplus`가 바로 클라이언트입니다.

서버에 접속하려면 보통 네 가지 정보가 필요합니다: **호스트(host, 서버 주소)**, **포트(port, 서버가 듣는 통로 번호)**, **사용자(user)**, **비밀번호(password)**. 같은 컴퓨터 안에서 접속할 때 호스트는 `localhost`(내 컴퓨터)입니다. 각 DBMS는 기본 포트가 정해져 있어, 특별히 바꾸지 않았다면 그 번호로 접속합니다.

| DBMS | 기본 포트 | 서버 프로그램 | 클라이언트 도구 |
|------|-----------|----------------|-----------------|
| PostgreSQL | 5432 | `postgres` | `psql` |
| MySQL | 3306 | `mysqld` | `mysql` |
| Oracle | 1521 | `oracle`(리스너 `tnslsnr`) | `sqlplus` |

---

## 2. 주요 명령어/문법

### 설치 개요

운영체제와 방법에 따라 설치 명령이 다릅니다. 여기서는 가장 흔한 방법만 소개합니다. 실무에서는 회사 표준 설치 방식을 따르세요.

**PostgreSQL**

```bash
# macOS (Homebrew)
brew install postgresql@16

# Ubuntu/Debian (APT)
sudo apt update && sudo apt install postgresql
```

**MySQL**

```bash
# macOS (Homebrew)
brew install mysql

# Ubuntu/Debian (APT)
sudo apt update && sudo apt install mysql-server
```

**Oracle**

Oracle은 설치가 상대적으로 복잡합니다. 학습용으로는 무료인 **Oracle Database Express Edition(XE)** 이나 공식 **Docker 이미지**를 권장합니다.

```bash
# Docker로 Oracle XE 실행 (학습용 간편 방법)
docker run -d --name oracle-xe -p 1521:1521 \
    -e ORACLE_PASSWORD=oracle \
    gvenzl/oracle-xe
```

### 서비스 시작 / 중지 / 상태 확인

**PostgreSQL**

```bash
# macOS (Homebrew)
brew services start postgresql@16
brew services stop postgresql@16

# Linux (systemd)
sudo systemctl start postgresql
sudo systemctl stop postgresql
sudo systemctl status postgresql   # 상태 확인
```

**MySQL**

```bash
# macOS (Homebrew)
brew services start mysql
brew services stop mysql

# Linux (systemd)
sudo systemctl start mysql
sudo systemctl status mysql
```

**Oracle (Docker 사용 시)**

```bash
docker start oracle-xe    # 시작
docker stop oracle-xe     # 중지
docker ps                 # 실행 중인 컨테이너 확인
```

### 클라이언트로 접속하기

**PostgreSQL — psql**

```bash
# 형식: psql -h 호스트 -p 포트 -U 사용자 -d 데이터베이스
psql -h localhost -p 5432 -U postgres -d postgres

# 접속 후 유용한 메타 명령 (psql 전용, 백슬래시로 시작)
\l          -- 데이터베이스 목록
\c dbname   -- 다른 데이터베이스로 전환
\dt         -- 현재 DB의 테이블 목록
\du         -- 사용자(역할) 목록
\q          -- 종료
```

**MySQL — mysql**

```bash
# 형식: mysql -h 호스트 -P 포트 -u 사용자 -p
mysql -h localhost -P 3306 -u root -p    # -p 뒤에 비번을 안 쓰면 접속 시 물어봄

# 접속 후 유용한 명령
SHOW DATABASES;      -- 데이터베이스 목록
USE mydb;            -- 데이터베이스 선택
SHOW TABLES;         -- 테이블 목록
EXIT;                -- 종료
```

> MySQL은 포트 옵션이 대문자 `-P`입니다. 소문자 `-p`는 비밀번호 옵션이니 헷갈리지 마세요.

**Oracle — sqlplus**

```bash
# 형식: sqlplus 사용자/비밀번호@호스트:포트/서비스명
sqlplus system/oracle@localhost:1521/XEPDB1

# 접속 후 유용한 명령
SELECT name FROM v$database;                 -- 데이터베이스 이름
SELECT table_name FROM user_tables;          -- 내 테이블 목록
EXIT;                                        -- 종료
```

### 연결 문자열(Connection String)

애플리케이션이나 도구에서 접속할 때 자주 쓰는 URL 형식도 알아 두면 좋습니다.

```text
PostgreSQL : postgresql://user:password@localhost:5432/dbname
MySQL      : mysql://user:password@localhost:3306/dbname
Oracle     : jdbc:oracle:thin:@localhost:1521/XEPDB1
```

---

## 3. 실습 예제

**시나리오: 설치한 DBMS 서비스를 시작하고, 클라이언트로 접속해, 데이터베이스와 테이블 목록을 확인해 보자. (PostgreSQL 기준)**

```bash
# 1) 서비스 시작
brew services start postgresql@16       # (Linux면 sudo systemctl start postgresql)

# 2) 서비스가 잘 떴는지 상태 확인
brew services list                      # postgresql 항목이 started인지 확인

# 3) psql로 접속
psql -h localhost -p 5432 -U postgres -d postgres
```

```sql
-- 4) 접속되면 프롬프트가 postgres=# 로 바뀐다. 아래를 실행해 보자.
\l                 -- 어떤 데이터베이스들이 있는지 확인

-- 5) 연습용 데이터베이스 만들기
CREATE DATABASE practice;

-- 6) 만든 DB로 전환
\c practice

-- 7) 테스트 테이블 생성 후 목록 확인
CREATE TABLE test (id INT);
\dt                -- test 테이블이 보이면 성공!

-- 8) 종료
\q
```

접속에 실패한다면 대개 (1) 서비스가 안 켜졌거나, (2) 포트/사용자/비밀번호가 틀렸거나, (3) 방화벽 문제입니다. 이 순서로 점검하면 대부분 해결됩니다.

---

## 4. 체크리스트

- [ ] 서버(데몬)와 클라이언트의 차이를 설명할 수 있다.
- [ ] 접속에 필요한 네 가지(호스트·포트·사용자·비밀번호)를 안다.
- [ ] PostgreSQL(5432)·MySQL(3306)·Oracle(1521)의 기본 포트를 안다.
- [ ] PostgreSQL/MySQL 서비스를 시작·중지·상태 확인할 수 있다.
- [ ] psql / mysql / sqlplus 중 하나 이상으로 실제 접속할 수 있다.
- [ ] psql의 `\l` `\dt`, MySQL의 `SHOW DATABASES`/`SHOW TABLES`로 목록을 조회할 수 있다.
- [ ] 접속 실패 시 서비스·포트·계정 순으로 점검할 줄 안다.
