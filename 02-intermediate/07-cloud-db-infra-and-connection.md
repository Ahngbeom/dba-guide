# 클라우드 DB 인프라 구축과 접속

## 핵심 개념 설명

[08. 클라우드 관리형 DB 기초](08-cloud-managed-db-basics.md)는 "이미 만들어진 관리형 DB를 어떻게 운영하는가"를 다뤘다. 하지만 실무에서는 그 이전에 **인프라를 처음부터 만들고, 처음으로 접속에 성공**하는 과정이 먼저 온다. 이 장은 그 공백을 메운다.

### 관리형 DB도 결국 네트워크 안에 있다

`01-beginner/03-installation-and-access.md`에서 로컬 DB는 `localhost`와 방화벽 없는 환경에서 접속했다. 클라우드에서는 관리형 DB 인스턴스도 **가상 네트워크(VPC)** 안의 특정 위치에 놓인다.

- **VPC(Virtual Private Cloud)**: 계정 안에 격리된 가상 네트워크. 그 안에 여러 **서브넷(subnet)**을 가용 영역(AZ)별로 나눠 둔다.
- **서브넷 그룹(RDS) / 프라이빗 서비스 액세스(Cloud SQL)**: RDS는 최소 2개 AZ의 서브넷을 묶은 "서브넷 그룹"을 지정해야 인스턴스를 만들 수 있다(Multi-AZ 페일오버의 전제). Cloud SQL은 VPC 피어링 기반의 프라이빗 서비스 액세스로 프라이빗 IP를 할당한다.
- **보안 그룹(Security Group) / 방화벽 규칙(Firewall Rule)**: 어떤 출발지(IP 대역)에서 어떤 포트로 접근을 허용할지 정의하는 규칙. RDS의 보안 그룹은 **상태 저장(stateful)** 방화벽이라 응답 트래픽은 별도 규칙 없이 자동 허용된다.

온프레미스 설치(01장)와 다른 점은, 서버 자체의 `iptables`나 OS 방화벽을 직접 만지는 대신 **클라우드가 제공하는 네트워크 경계와 상태 저장 방화벽을 선언적으로 구성**한다는 것이다. 그러나 "어떤 출발지에 어떤 포트를 열 것인가"라는 설계 책임은 여전히 DBA/인프라 담당자에게 있다.

### 퍼블릭 접속 vs 프라이빗 접속

- **퍼블릭 액세스**: 인스턴스에 퍼블릭 IP를 부여해 인터넷에서 직접 접속. 학습·개인 프로젝트에는 편리하지만, 보안 그룹으로 출발지를 좁게 제한하지 않으면 공격 표면이 커진다.
- **프라이빗 액세스**(실무 기본값): 퍼블릭 IP를 부여하지 않고 VPC 내부에서만 접속 가능하게 한다. 이 경우 VPC 밖(로컬 개발 환경 등)에서 접속하려면 **SSH 배스천 호스트, AWS SSM 포트 포워딩, GCP Cloud SQL Auth Proxy** 같은 중개 수단이 필요하다.

### 인증과 전송 구간 암호화

- **IAM 데이터베이스 인증**: 고정된 DB 비밀번호 대신, 클라우드 IAM 자격 증명으로 짧은 수명의 인증 토큰을 발급받아 접속한다. 비밀번호 로테이션·유출 위험을 줄이는 대표적인 실무 관행이다.
- **전송 구간 암호화(SSL/TLS)**: 관리형 DB는 클라우드가 발급한 서버 인증서를 기본 제공한다. 클라이언트는 `sslmode=verify-full`처럼 인증서를 검증하는 모드로 접속해야 중간자 공격을 막을 수 있다. (03-advanced/05-security-and-compliance.md에서 셀프 매니지드 환경의 전송 암호화를 더 깊이 다룬다.)

## 주요 명령어/문법

### 네트워크 준비: 서브넷 그룹과 보안 그룹/방화벽 규칙

**AWS**
```bash
# 서브넷 그룹 생성 (최소 2개 AZ의 서브넷 필요)
aws rds create-db-subnet-group \
  --db-subnet-group-name mydb-subnet-group \
  --db-subnet-group-description "prod subnet group" \
  --subnet-ids subnet-aaaa1111 subnet-bbbb2222

# 보안 그룹 생성 및 인바운드 규칙 (사내 VPN 대역에서만 5432 허용)
aws ec2 create-security-group \
  --group-name mydb-sg --description "DB access" --vpc-id vpc-0123456789
aws ec2 authorize-security-group-ingress \
  --group-id sg-0123456789 --protocol tcp --port 5432 --cidr 10.0.0.0/16
```

**GCP**
```bash
# 방화벽 규칙 (VPC 내부에서만 허용)
gcloud compute firewall-rules create allow-cloudsql-internal \
  --network=default --direction=INGRESS --action=ALLOW \
  --rules=tcp:5432 --source-ranges=10.0.0.0/16
```

### 인스턴스 생성 (프라이빗 vs 퍼블릭)

**AWS RDS**
```bash
# 프라이빗 (퍼블릭 IP 미부여, 실무 기본값)
aws rds create-db-instance \
  --db-instance-identifier mydb --engine postgres --engine-version 16.3 \
  --db-instance-class db.t3.medium --allocated-storage 50 \
  --master-username admin --manage-master-user-password \
  --db-subnet-group-name mydb-subnet-group \
  --vpc-security-group-ids sg-0123456789 \
  --no-publicly-accessible

# 엔드포인트 조회
aws rds describe-db-instances --db-instance-identifier mydb \
  --query 'DBInstances[0].Endpoint'
```

**GCP Cloud SQL**
```bash
# 프라이빗 IP만 할당 (퍼블릭 IP 없음)
gcloud sql instances create mydb \
  --database-version=POSTGRES_16 --region=asia-northeast3 \
  --tier=db-custom-2-8192 --network=default --no-assign-ip
```

<!-- dbms:postgresql -->
### SSL 검증 접속

```bash
# AWS RDS: 리전 CA 번들 다운로드 후 인증서 검증 접속
psql "host=mydb.xxxx.ap-northeast-2.rds.amazonaws.com dbname=app \
      sslmode=verify-full sslrootcert=global-bundle.pem"
```
<!-- /dbms:postgresql -->

### IAM 인증으로 비밀번호 없이 접속

**AWS RDS**
```bash
# 15분짜리 인증 토큰 발급
TOKEN=$(aws rds generate-db-auth-token \
  --hostname mydb.xxxx.ap-northeast-2.rds.amazonaws.com \
  --port 5432 --username iam_app_user)

# 토큰을 비밀번호로 사용해 접속 (반드시 SSL 필요)
PGPASSWORD="$TOKEN" psql "host=mydb.xxxx.ap-northeast-2.rds.amazonaws.com \
  dbname=app user=iam_app_user sslmode=require"
```

**GCP Cloud SQL**
```bash
# IAM 인증 사용자 생성
gcloud sql users create iam_app_user@my-project.iam \
  --instance=mydb --type=cloud_iam_user
```

### 프라이빗 DB에 안전하게 접속하기 (배스천 없이)

**AWS: SSM 포트 포워딩** — 별도 배스천 서버 없이, SSM 관리 인스턴스 하나를 경유해 로컬 포트를 RDS로 터널링한다.
```bash
aws ssm start-session \
  --target i-0123456789abcdef0 \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["mydb.xxxx.ap-northeast-2.rds.amazonaws.com"],"portNumber":["5432"],"localPortNumber":["15432"]}'

# 다른 터미널에서 로컬 포트로 접속
psql -h localhost -p 15432 -U admin -d app
```

**GCP: Cloud SQL Auth Proxy** — 프라이빗/퍼블릭 IP와 무관하게 IAM 권한만으로 암호화된 터널을 연다.
```bash
# 프록시 실행 (IAM 인증 자동 사용 시 --auto-iam-authn)
./cloud-sql-proxy my-project:asia-northeast3:mydb --port 5432 --auto-iam-authn

# 다른 터미널에서 로컬 접속
psql -h 127.0.0.1 -p 5432 -U iam_app_user -d app
```

## 실습 예제

<!-- dbms:postgresql -->
**시나리오: "회사 정책상 DB에 퍼블릭 IP를 부여할 수 없다. VPC 내부에서만 접속 가능한 관리형 PostgreSQL 인스턴스를 만들고, 로컬 개발자가 안전하게 접속할 수 있게 하라."**

```bash
# 1) 서브넷 그룹 생성 (최소 2개 AZ)
aws rds create-db-subnet-group \
  --db-subnet-group-name app-subnet-group \
  --db-subnet-group-description "app db subnets" \
  --subnet-ids subnet-aaaa1111 subnet-bbbb2222

# 2) 보안 그룹 생성 — 사내 VPN 대역(10.0.0.0/16)에서만 5432 허용
aws ec2 create-security-group --group-name app-db-sg \
  --description "app db access" --vpc-id vpc-0123456789
aws ec2 authorize-security-group-ingress \
  --group-id sg-app-db --protocol tcp --port 5432 --cidr 10.0.0.0/16

# 3) 퍼블릭 IP 없이 인스턴스 생성
aws rds create-db-instance \
  --db-instance-identifier app-db --engine postgres --engine-version 16.3 \
  --db-instance-class db.t3.medium --allocated-storage 50 \
  --master-username admin --manage-master-user-password \
  --db-subnet-group-name app-subnet-group \
  --vpc-security-group-ids sg-app-db --no-publicly-accessible

# 4) 인스턴스가 available 상태가 될 때까지 대기 후 엔드포인트 확인
aws rds describe-db-instances --db-instance-identifier app-db \
  --query 'DBInstances[0].[DBInstanceStatus,Endpoint.Address]'

# 5) 로컬 개발자는 SSM 포트 포워딩으로 터널을 연다 (배스천 서버 불필요)
aws ssm start-session --target i-0123456789abcdef0 \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["<위에서 확인한 엔드포인트>"],"portNumber":["5432"],"localPortNumber":["15432"]}'

# 6) 다른 터미널에서, 비밀번호 대신 IAM 토큰으로 접속
TOKEN=$(aws rds generate-db-auth-token --hostname <엔드포인트> --port 5432 --username iam_app_user)
PGPASSWORD="$TOKEN" psql -h localhost -p 15432 -U iam_app_user -d app "sslmode=require"
```

**GCP Cloud SQL 대응 요약**: 동일한 요구사항을 GCP에서는 `--no-assign-ip`로 퍼블릭 IP 없는 인스턴스를 만들고, 로컬 개발자는 배스천이나 SSM 없이 **Cloud SQL Auth Proxy**를 실행해 `--auto-iam-authn`으로 IAM 인증까지 한 번에 처리한다. 별도의 VPN·서브넷 설계 없이도 IAM 권한만으로 안전한 터널이 열린다는 점이 AWS의 SSM 포트 포워딩 방식과 다르다.
<!-- /dbms:postgresql -->

**운영 팁**: 퍼블릭 액세스가 꼭 필요한 경우(예: 사내에 VPN이 없는 초기 스타트업)라면, 보안 그룹의 출발지를 `0.0.0.0/0`이 아니라 실제 사무실/사무실 VPN의 고정 IP 대역으로 반드시 좁혀야 한다.

## 체크리스트

- [ ] VPC, 서브넷, 서브넷 그룹(RDS)/프라이빗 서비스 액세스(Cloud SQL)의 역할을 설명할 수 있다.
- [ ] 보안 그룹(상태 저장 방화벽)과 방화벽 규칙의 차이와 공통 목적을 안다.
- [ ] 퍼블릭 액세스와 프라이빗 액세스의 보안 트레이드오프를 설명하고, 실무에서 어느 쪽을 기본으로 해야 하는지 안다.
- [ ] AWS CLI/gcloud로 네트워크 설정(서브넷 그룹·보안 그룹/방화벽 규칙)을 포함해 관리형 DB 인스턴스를 생성할 수 있다.
<!-- dbms:postgresql -->
- [ ] `sslmode=verify-full` 등으로 서버 인증서를 검증하며 접속할 수 있다.
<!-- /dbms:postgresql -->
- [ ] IAM 인증 토큰으로 비밀번호 없이 접속하는 방법을 안다.
- [ ] SSM 포트 포워딩 또는 Cloud SQL Auth Proxy로 배스천 서버 없이 프라이빗 DB에 접속할 수 있다.
- [ ] 퍼블릭 엔드포인트를 열어야 할 때 보안 그룹으로 출발지를 좁게 제한하는 방법을 안다.
