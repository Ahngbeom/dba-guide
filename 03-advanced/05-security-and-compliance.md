# 05. 보안과 컴플라이언스 (Security & Compliance)

## 1. 핵심 개념 설명

데이터베이스는 조직에서 가장 민감한 자산이 모이는 곳이다. 시니어 DBA의 보안 책임은 방화벽 설정을 넘어, **"데이터가 저장될 때, 전송될 때, 접근될 때 각각 어떻게 보호되는가"**를 계층적으로 설계하고, 그 설계가 법적 요구(GDPR, 개인정보보호법 등)를 충족함을 **증명**하는 데까지 이른다. 보안은 "뚫리지 않게"만이 아니라 "뚫렸을 때 피해를 최소화하고, 무슨 일이 있었는지 추적 가능하게" 만드는 일이다(방어 심화, defense in depth).

### 저장 데이터 암호화 (Encryption at Rest / TDE)
디스크·백업·스냅샷이 물리적으로 유출돼도 데이터를 읽지 못하게 한다. **TDE(Transparent Data Encryption)**는 애플리케이션 변경 없이 스토리지 계층에서 자동 암복호화한다. 핵심 쟁점은 **키 관리**다 — 데이터와 키를 같은 곳에 두면 의미가 없으므로, KMS/HSM 같은 별도 키 관리 시스템과 키 회전(rotation) 정책이 필수다. TDE는 "디스크 도난"은 막지만, **정상 인증된 접근으로부터는 보호하지 못한다**는 한계를 이해해야 한다(그래서 컬럼 암호화·접근 통제가 별도로 필요).

### 전송 구간 암호화 (Encryption in Transit / SSL/TLS)
클라이언트-DB, DB-DB(복제) 간 트래픽을 TLS로 암호화해 스니핑·중간자 공격을 막는다. 단순히 켜는 것을 넘어 **강제(require)**하고, 서버·클라이언트 인증서 검증(`verify-full`)까지 해야 진짜 안전하다. "TLS를 켰지만 검증은 안 함"은 흔한 구멍이다.

### 감사 로그 (Audit Log)
누가 언제 무엇을 했는지 기록한다. 컴플라이언스의 핵심 증거이자 사고 조사의 출발점이다. **무엇을 남길지**(로그인, 권한 변경, 민감 테이블 접근, DDL)와 **어떻게 보호할지**(감사 로그 자체의 무결성·변조 방지, 별도 저장소로 전송)가 설계 포인트다. 과도한 감사는 성능·스토리지 부담이므로 **민감도 기반 선별**이 실무 감각이다.

### 컴플라이언스 대응 (개념 수준)
- **최소 권한 원칙(Least Privilege)**: 계정·역할이 딱 필요한 만큼만 권한을 갖게 한다.
- **개인정보 취급**: GDPR의 "잊힐 권리"(삭제 요구), 개인정보보호법의 수집·이용·보관·파기 절차를 데이터 계층에서 지원해야 한다. → **삭제/익명화(anonymization)/가명화(pseudonymization)** 기능 설계.
- **데이터 마스킹**: 개발·테스트 환경에 운영 데이터를 쓸 때 민감 정보를 마스킹.
- **데이터 주권(Data Residency)**: 특정 국가 밖으로 데이터 반출 금지 → 리전 선택·백업 위치 제약(03·07장 연계).
- **책임 분리·증거 보존**: 감사 로그·백업·접근 기록을 규정 기간 보관하고 감사에 제출할 수 있어야 한다.

---

## 2. 주요 명령어/문법

### 전송 구간 암호화 (TLS)

**PostgreSQL**
```ini
# postgresql.conf
ssl = on
ssl_cert_file = 'server.crt'
ssl_key_file = 'server.key'
```
```conf
# pg_hba.conf — TLS 강제 (hostssl), 비암호화 연결 거부
hostssl  all  all  0.0.0.0/0  scram-sha-256
```
```bash
# 클라이언트: 서버 인증서까지 검증
psql "host=db.example.com sslmode=verify-full sslrootcert=root.crt dbname=app"
```

**MySQL**
```sql
-- 특정 계정에 TLS 연결 강제
ALTER USER 'app'@'%' REQUIRE SSL;      -- 또는 REQUIRE X509 (인증서 검증)
```

**Oracle** — `sqlnet.ora`/`listener.ora`에 TCPS(TLS) 설정, 지갑(wallet) 기반 인증서 관리.

### 저장 데이터 암호화 (TDE)

**Oracle (네이티브 TDE)**
```sql
-- 키스토어 구성 후 테이블스페이스 암호화
ADMINISTER KEY MANAGEMENT SET KEYSTORE OPEN IDENTIFIED BY "pwd";
CREATE TABLESPACE enc_ts ... ENCRYPTION USING 'AES256' ENCRYPT;
-- 컬럼 단위 암호화
CREATE TABLE members (id NUMBER, ssn VARCHAR2(13) ENCRYPT);
```

**MySQL (InnoDB 테이블스페이스 암호화)**
```sql
ALTER TABLE members ENCRYPTION='Y';
```

**PostgreSQL** — 코어 TDE는 없고, 통상 **스토리지/파일시스템 암호화**(LUKS, 클라우드 볼륨 암호화)로 대체하거나 `pgcrypto`로 컬럼 암호화.
```sql
-- 컬럼 암호화 (pgcrypto)
INSERT INTO members(ssn_enc) VALUES (pgp_sym_encrypt('900101-1234567', :key));
SELECT pgp_sym_decrypt(ssn_enc, :key) FROM members WHERE id = 1;
```

**클라우드 (관리형 KMS 기반, 사실상 표준)**
```bash
# AWS RDS: 생성 시 KMS 키로 저장 암호화 (스냅샷·복제본까지 자동 적용)
aws rds create-db-instance --db-instance-identifier proddb \
  --storage-encrypted --kms-key-id alias/rds-prod ...
# GCP Cloud SQL: CMEK(고객 관리 키)
gcloud sql instances create proddb --disk-encryption-key=projects/.../cryptoKeys/sql-key
```

### 감사 로그

**PostgreSQL (pgAudit 확장)**
```ini
shared_preload_libraries = 'pgaudit'
pgaudit.log = 'ddl, role, write'   # DDL·권한변경·쓰기 감사 (read는 부하 커서 선별)
```

**MySQL** (Enterprise Audit 또는 MariaDB audit plugin)
```sql
SET GLOBAL audit_log_policy = 'ALL';   -- 또는 LOGINS/QUERIES 선별
```

**Oracle (Unified Auditing)**
```sql
CREATE AUDIT POLICY sensitive_access
  ACTIONS SELECT, UPDATE, DELETE ON app.members;
AUDIT POLICY sensitive_access;
```

### 최소 권한 (공통 개념)
```sql
-- 읽기 전용 역할을 만들어 필요한 계정에만 부여 (PostgreSQL 예)
CREATE ROLE readonly;
GRANT CONNECT ON DATABASE app TO readonly;
GRANT USAGE ON SCHEMA public TO readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly;
GRANT readonly TO analyst_kim;
```

---

## 3. 실습 예제

**시나리오: "회원 개인정보(SSN, 연락처)를 다루는 서비스의 데이터 보안·컴플라이언스 설계"**

1. **데이터 분류**: 먼저 컬럼을 민감도 등급으로 분류(공개/내부/민감/극비). SSN·결제정보 = 극비, 이메일·전화 = 민감. 등급이 통제 강도를 결정한다.

2. **저장 암호화(다층)**:
   - 전체 볼륨: 클라우드 KMS로 스토리지 암호화(스냅샷·백업·복제본까지 자동) → 물리 유출 방어.
   - 극비 컬럼(SSN): 추가로 **컬럼/애플리케이션 레벨 암호화**(pgcrypto 또는 앱단 KMS 봉투암호화) → 정상 접근자에게도 평문 노출 최소화.

3. **전송 암호화**: 모든 연결에 `hostssl`/`REQUIRE SSL` 강제 + 클라이언트 `verify-full`. 복제 트래픽도 TLS.

4. **접근 통제**: 최소 권한 역할 설계. 운영 DB 직접 접근은 배스천/JIT(just-in-time) 승인 + 세션 기록. 개발 DB에는 운영 데이터를 **마스킹**해 복제.

5. **감사**: 극비 테이블에 대한 SELECT/UPDATE/DELETE와 모든 권한 변경·로그인을 감사 로그로 남기고, 로그는 **별도의 변조 방지 저장소**(WORM/오브젝트 스토리지 객체 잠금)로 전송, 규정 기간 보관.

6. **컴플라이언스 절차 지원**:
   - "잊힐 권리" 요청 → 해당 사용자 데이터 삭제 또는 **가명화**(분석 지표는 유지하되 식별 불가) 배치 설계.
   - 데이터 주권 → 국내 리전 고정, 백업도 동일 관할권 내(03·07장).
   - 정기 접근 권한 리뷰(분기별) + 미사용 계정 회수 자동화.

7. **키 관리 정책**: KMS 키 회전 주기(예: 1년), 키 접근 권한 분리(DB 관리자 ≠ 키 관리자), 봉투 암호화로 데이터 키와 마스터 키 분리.

> **트레이드오프 메모**: 컬럼 암호화는 강력하지만 **암호화된 컬럼으로는 검색·인덱싱·범위조회가 사실상 불가능**하다(결정적 암호화로 동등 비교만 제한적 허용). "이 컬럼으로 조회해야 하는가"와 "얼마나 민감한가" 사이에서 등급별로 타협해야 한다. 또한 감사는 많이 남길수록 안전하지만 성능·비용·프라이버시(감사 로그도 개인정보) 부담이 있으므로 **선별**이 핵심이다.

---

## 4. 체크리스트

- [ ] 저장/전송/접근 각 계층의 위협과 방어 수단을 구분해 설명할 수 있다.
- [ ] TDE(저장 암호화)가 막는 위협과 막지 못하는 위협(정상 접근)을 구분한다.
- [ ] KMS/HSM 기반 키 관리와 키 회전·권한 분리 정책을 설계할 수 있다.
- [ ] TLS를 강제하고 인증서 검증(verify-full/X509)까지 적용할 수 있다.
- [ ] 민감도 기반으로 감사 대상을 선별하고 감사 로그를 변조 방지 저장소로 보낼 수 있다.
- [ ] 최소 권한 원칙에 따라 역할 기반 접근 통제를 설계할 수 있다.
- [ ] "잊힐 권리"·데이터 주권 등 규제 요구를 데이터 계층 기능으로 번역할 수 있다.
- [ ] 개발/테스트 환경 데이터 마스킹 정책을 수립할 수 있다.
