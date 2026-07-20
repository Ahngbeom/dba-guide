# 06. 자동화와 IaC (Infrastructure as Code)

## 1. 핵심 개념 설명

DB 인프라를 사람이 콘솔에서 클릭으로 만드는 방식은 세 가지 근본 문제가 있다. **재현 불가능**(똑같이 다시 못 만듦), **감사 불가능**(누가 왜 바꿨는지 모름), **드리프트**(문서와 실제가 어긋남). IaC(Infrastructure as Code)는 인프라의 원하는 상태를 코드로 선언하고, 그 코드를 유일한 진실의 원천(single source of truth)으로 삼아 이 문제들을 해결한다. 시니어 DBA에게 IaC는 선택이 아니라, **DR·확장·컴플라이언스를 실현하는 토대**다 — DR 리전에 동일 인프라를 5분 만에 세울 수 있는 것도, 변경 이력을 감사에 제출할 수 있는 것도 IaC 덕분이다.

### Terraform — 프로비저닝(Provisioning)
클라우드 리소스(인스턴스, 파라미터 그룹, 서브넷, 보안 그룹, 리드 리플리카)의 **존재와 형상**을 선언적으로 관리한다. "무엇이 있어야 하는가"를 코드로 쓰면 Terraform이 현재 상태와 비교(plan)해 차이만 적용(apply)한다. 상태 파일(state)이 실제 인프라와 코드를 잇는 핵심이며, 원격 상태 저장·잠금(locking)이 팀 협업의 전제다.

### Ansible — 구성(Configuration)
이미 존재하는 서버 **내부의 상태**(패키지 설치, 설정 파일, DB 파라미터, 사용자·확장 생성)를 관리한다. Terraform이 "그릇을 만든다"면 Ansible은 "그릇 안을 채운다". **멱등성(idempotency)**이 핵심 — 몇 번을 실행해도 결과가 같아야 한다.

> 실무 조합: **Terraform으로 인프라를 만들고, Ansible로 그 위를 구성**하는 것이 전형적이다. 클라우드 매니지드 DB(RDS 등)는 내부 OS 접근이 제한되므로 Terraform 비중이 크고, 셀프 매니지드(EC2 위 PostgreSQL)는 Ansible 역할이 커진다.

### 자동화 스크립트 설계 원칙
- **멱등성**: 여러 번 실행해도 안전. "이미 있으면 건너뛴다".
- **드라이런/플랜 우선**: 실제 변경 전 `plan`/`--check`로 영향 범위를 먼저 본다.
- **최소 권한·비밀 분리**: 자동화 계정도 최소 권한. 비밀번호·키는 코드가 아니라 Vault/KMS/Secrets Manager에서 주입.
- **관측·롤백**: 실패 시 명확히 멈추고(silent failure 금지) 롤백 경로를 둔다.
- **파괴적 작업 가드**: DB 삭제·재생성 같은 작업엔 `prevent_destroy`·승인 게이트.

### GitOps 연계
인프라 코드를 Git에 두고, **PR → 리뷰 → 머지 → 자동 적용**의 흐름으로 운영한다. Git 이력이 곧 변경 감사 로그가 되고, 리뷰가 게이트가 되며, main 브랜치가 곧 인프라의 목표 상태가 된다. DB 스키마 변경(중급 단계의 Flyway/Liquibase)도 이 파이프라인에 얹으면 애플리케이션·인프라·스키마가 한 흐름으로 관리된다.

---

## 2. 주요 명령어/문법

<!-- dbms:postgresql -->
### Terraform — RDS PostgreSQL 예시
```hcl
resource "aws_db_parameter_group" "pg16" {
  family = "postgres16"
  parameter { name = "log_min_duration_statement"  value = "500" }  # 느린 쿼리 로깅
  parameter { name = "max_connections"              value = "500" }
}

resource "aws_db_instance" "prod" {
  identifier              = "proddb"
  engine                  = "postgres"
  engine_version          = "16.3"
  instance_class          = "db.r6g.2xlarge"
  allocated_storage       = 500
  multi_az                = true                 # 02장 HA
  storage_encrypted       = true                 # 05장 저장 암호화
  kms_key_id              = aws_kms_key.rds.arn
  backup_retention_period = 14                    # 03장 DR 기반
  parameter_group_name    = aws_db_parameter_group.pg16.name
  deletion_protection     = true                  # 파괴적 작업 가드
  lifecycle { prevent_destroy = true }
}
```
```bash
terraform plan     # 드라이런: 무엇이 바뀌는지 먼저 확인 (필수 습관)
terraform apply    # 승인 후 적용
```

### Ansible — 셀프 매니지드 PostgreSQL 구성 (멱등적)
```yaml
- hosts: db_servers
  become: true
  tasks:
    - name: PostgreSQL 설치
      ansible.builtin.package:
        name: postgresql-16
        state: present            # 이미 있으면 변경 없음 (멱등)

    - name: postgresql.conf 배포
      ansible.builtin.template:
        src: postgresql.conf.j2
        dest: /etc/postgresql/16/main/postgresql.conf
      notify: restart postgresql   # 변경 시에만 재시작

    - name: 복제 계정 생성
      community.postgresql.postgresql_user:
        name: replicator
        role_attr_flags: REPLICATION
        password: "{{ vault_replicator_pw }}"   # 비밀은 Vault에서 주입
  handlers:
    - name: restart postgresql
      ansible.builtin.service: { name: postgresql, state: restarted }
```
```bash
ansible-playbook site.yml --check --diff   # 드라이런
ansible-playbook site.yml                  # 적용
```

### GCP (gcloud + Terraform 동등)
```hcl
resource "google_sql_database_instance" "prod" {
  name             = "proddb"
  database_version = "POSTGRES_16"
  settings {
    tier = "db-custom-8-32768"
    availability_type = "REGIONAL"     # HA
    backup_configuration { enabled = true  point_in_time_recovery_enabled = true }
  }
}
```
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
### Terraform — RDS MySQL 예시
```hcl
resource "aws_db_parameter_group" "mysql80" {
  family = "mysql8.0"
  parameter { name = "slow_query_log"     value = "1" }    # 느린 쿼리 로깅
  parameter { name = "long_query_time"    value = "0.5" }
  parameter { name = "max_connections"    value = "500" }
}

resource "aws_db_instance" "prod" {
  identifier              = "proddb"
  engine                  = "mysql"
  engine_version          = "8.0.36"
  instance_class          = "db.r6g.2xlarge"
  allocated_storage       = 500
  multi_az                = true                 # 02장 HA
  storage_encrypted       = true                 # 05장 저장 암호화
  kms_key_id              = aws_kms_key.rds.arn
  backup_retention_period = 14                    # 03장 DR 기반
  parameter_group_name    = aws_db_parameter_group.mysql80.name
  deletion_protection     = true                  # 파괴적 작업 가드
  lifecycle { prevent_destroy = true }
}
```
```bash
terraform plan     # 드라이런: 무엇이 바뀌는지 먼저 확인 (필수 습관)
terraform apply    # 승인 후 적용
```

### Ansible — 셀프 매니지드 MySQL 구성 (멱등적)
```yaml
- hosts: db_servers
  become: true
  tasks:
    - name: MySQL 설치
      ansible.builtin.package:
        name: mysql-server
        state: present            # 이미 있으면 변경 없음 (멱등)

    - name: my.cnf 배포
      ansible.builtin.template:
        src: my.cnf.j2
        dest: /etc/mysql/my.cnf
      notify: restart mysql        # 변경 시에만 재시작

    - name: 복제 계정 생성
      community.mysql.mysql_user:
        name: replicator
        priv: "*.*:REPLICATION SLAVE"
        password: "{{ vault_replicator_pw }}"   # 비밀은 Vault에서 주입
  handlers:
    - name: restart mysql
      ansible.builtin.service: { name: mysql, state: restarted }
```
```bash
ansible-playbook site.yml --check --diff   # 드라이런
ansible-playbook site.yml                  # 적용
```

### GCP (gcloud + Terraform 동등)
```hcl
resource "google_sql_database_instance" "prod" {
  name             = "proddb"
  database_version = "MYSQL_8_0"
  settings {
    tier = "db-custom-8-32768"
    availability_type = "REGIONAL"     # HA
    backup_configuration { enabled = true  binary_log_enabled = true }
  }
}
```
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
### Terraform — RDS Oracle 예시
```hcl
resource "aws_db_parameter_group" "oracle19" {
  family = "oracle-ee-19"
  parameter { name = "audit_trail"   value = "DB,EXTENDED" }   # 감사 로깅 (05장)
  parameter { name = "open_cursors"  value = "1000" }
  parameter { name = "processes"     value = "1000" }
}

resource "aws_db_instance" "prod" {
  identifier              = "proddb"
  engine                  = "oracle-ee"
  engine_version          = "19.0.0.0.ru-2024-01.rur-2024-01.r1"
  license_model           = "bring-your-own-license"   # 또는 license-included
  instance_class          = "db.r6i.2xlarge"
  allocated_storage       = 500
  multi_az                = true                 # 02장 HA (Data Guard 대신 RDS Multi-AZ)
  storage_encrypted       = true                 # 05장 저장 암호화
  kms_key_id              = aws_kms_key.rds.arn
  backup_retention_period = 14                    # 03장 DR 기반
  parameter_group_name    = aws_db_parameter_group.oracle19.name
  deletion_protection     = true                  # 파괴적 작업 가드
  lifecycle { prevent_destroy = true }
}
```
```bash
terraform plan     # 드라이런: 무엇이 바뀌는지 먼저 확인 (필수 습관)
terraform apply    # 승인 후 적용
```

> **RDS Oracle과 Ansible**: RDS는 매니지드 서비스라 OS 접근이 막혀 있어(SSH 불가) Ansible로 구성할 대상 자체가 없다 — 파라미터·옵션 그룹까지가 Terraform의 몫이고 그 이상은 RDS API/콘솔로만 다룬다. 아래는 **EC2에 셀프 매니지드로 설치하는 경우**(면허를 직접 들고 온 온프레미스 이관·RAC 구성 등)의 예시다.

### Ansible — 셀프 매니지드 Oracle 구성 (EC2, 멱등적)
```yaml
- hosts: db_servers
  become: true
  tasks:
    - name: 사일런트 설치 응답 파일 배포
      ansible.builtin.template:
        src: db_install.rsp.j2
        dest: /tmp/db_install.rsp

    - name: Oracle Database 사일런트 설치 (이미 설치돼 있으면 스킵)
      ansible.builtin.command:
        cmd: /u01/stage/database/runInstaller -silent -responseFile /tmp/db_install.rsp
      args:
        creates: /u01/app/oracle/product/19.0.0/dbhome_1/bin/sqlplus   # 멱등성 보장

    - name: listener.ora 배포
      ansible.builtin.template:
        src: listener.ora.j2
        dest: /u01/app/oracle/product/19.0.0/dbhome_1/network/admin/listener.ora
      notify: restart listener

    - name: 복제(스탠바이) 계정 생성
      community.general.oracle_user:
        username: replicator
        password: "{{ vault_replicator_pw }}"   # 비밀은 Vault에서 주입
        state: present
  handlers:
    - name: restart listener
      ansible.builtin.command: lsnrctl reload
```
```bash
ansible-playbook site.yml --check --diff   # 드라이런
ansible-playbook site.yml                  # 적용
```

> GCP Cloud SQL은 PostgreSQL/MySQL만 지원하며 매니지드 Oracle 상품은 제공하지 않는다 — Oracle을 GCP에서 쓰려면 Bare Metal Solution 등 별도 트랙이 필요하므로 여기서는 다루지 않는다.
<!-- /dbms:oracle -->

---

## 3. 실습 예제

**시나리오: "새 서비스용 프로덕션 DB 스택을 코드로 구축하고, DR 리전에 동일 스택을 재현한다."**

1. **리포지토리 구조 설계**:
   ```text
   infra/
     modules/rds/          # 재사용 가능한 DB 모듈 (인스턴스+파라미터+보안그룹)
     envs/
       prod-seoul/         # 운영 (ap-northeast-2)
       dr-tokyo/           # DR (ap-northeast-1) — 같은 모듈, 다른 변수
     backend.tf            # 원격 state (S3+DynamoDB 잠금)
   ```
   같은 모듈에 리전·크기만 변수로 바꿔 **운영과 DR을 동일 코드로** 만든다 → 03장 DR의 "동일 인프라 재현"이 코드로 보장된다.

2. **비밀 분리**: DB 마스터 비밀번호는 코드에 넣지 않고 Secrets Manager에서 생성·주입. Terraform은 ARN만 참조.

3. **GitOps 파이프라인**:
   - 개발자가 `envs/prod-seoul`에 변경 PR 생성.
   - CI가 자동으로 `terraform plan`을 돌려 **PR 코멘트로 변경 예정 사항**을 보여준다(리뷰어가 영향 확인).
   - DBA 승인 후 머지 → CD가 `terraform apply`. main 브랜치 = 인프라의 진실.

4. **드리프트 감지**: 정기적으로(예: 매일) `terraform plan`을 돌려 콘솔에서 누군가 수동 변경한 드리프트를 탐지·알림. 발견되면 코드로 흡수하거나 되돌린다.

5. **파괴적 작업 방어**: `prevent_destroy`·`deletion_protection`으로 실수 삭제 차단. DB 교체가 필요한 변경(엔진 major 업그레이드 등)은 반드시 계획된 창에서 사람이 확인.

6. **구성 자동화**: 셀프 매니지드 부분(모니터링 에이전트, 확장 설치, 파라미터 튜닝)은 Ansible 플레이북으로 멱등하게 적용, 역시 Git으로 관리.

> **트레이드오프 메모**: IaC의 초기 투자 비용(학습·구조 설계·state 관리)은 작지 않다. 일회성 실험 DB에까지 IaC를 강제하면 오히려 느려진다. **오래 살아남을 프로덕션·DR·스테이징**부터 코드화하고, 실험 환경은 가볍게 두는 균형이 현실적이다. 또한 상태 파일(state)에는 민감 정보가 섞일 수 있으니 원격 state는 반드시 암호화·접근통제한다.

---

## 4. 체크리스트

- [ ] 수동 프로비저닝의 문제(재현·감사·드리프트)를 설명하고 IaC로 해결할 수 있다.
- [ ] Terraform(프로비저닝)과 Ansible(구성)의 역할 차이를 구분해 조합할 수 있다.
- [ ] `plan`/`--check` 드라이런을 습관화하고 변경 영향을 사전에 검토한다.
- [ ] 멱등성 있는 자동화 스크립트를 작성할 수 있다.
- [ ] 비밀정보를 코드에서 분리해 Vault/KMS/Secrets Manager로 주입한다.
- [ ] `prevent_destroy`·승인 게이트로 파괴적 작업을 방어할 수 있다.
- [ ] GitOps 흐름(PR→plan→리뷰→apply)으로 인프라 변경을 관리할 수 있다.
- [ ] 동일 모듈로 운영·DR 환경을 재현해 DR 준비를 코드로 보장할 수 있다.
- [ ] 드리프트를 정기적으로 감지·해소할 수 있다.
