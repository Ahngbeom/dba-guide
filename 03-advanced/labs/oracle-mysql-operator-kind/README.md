# 로컬 kind + Oracle MySQL Operator 단계별 실습 가이드

`03-advanced/08-kubernetes-db-operators.md`를 참고서로, 로컬 kind 클러스터에 Oracle
MySQL Operator(InnoDB Cluster)를 직접 배포하고 페일오버까지 손으로 겪어보는 실습이다.

이 디렉토리에는 실행 스크립트가 없다 — **모든 명령은 아래를 보며 직접 터미널에
타이핑하거나 붙여넣어 실행한다.** `kind create cluster`, `kubectl apply`,
`kubectl port-forward` 같은 클러스터 변경 명령은 안전 훅 때문에 Claude가 대신
실행할 수 없기도 하지만, 그보다 먼저 — 명령을 직접 치고 출력을 눈으로 봐야
"무엇을 왜 하는지"가 남는다. 스크립트로 감싸면 그 학습 효과가 사라진다.

이 디렉토리에 남아있는 건 3개의 선언적 리소스 정의 파일뿐이다:

- `kind-config.yaml` — kind 클러스터 노드 구성
- `secret.yaml.example` — InnoDBCluster용 root 자격 증명 템플릿
- `innodbcluster.yaml` — InnoDBCluster CR (08장 예제를 로컬 랩용으로 축소)

## 사전 확인 (이미 완료됨)

- Docker Desktop 실행 중, `kind` 설치됨(0.32.0)
- `kubectl`, `mysql` CLI가 로컬에 이미 있음 — 별도 설치 불필요
- Operator는 `trunk` 대신 최신 안정 태그 `9.7.0-2.2.8`을 사용 (아래 명령에 이미 반영)

막히는 부분이 있으면 실제 출력(에러 메시지, `kubectl get` 결과 등)을 그대로
공유해달라 — 클러스터가 뜬 뒤에는 `kubectl get/describe/logs`(읽기 전용)로
같이 상태를 보며 원인을 짚어줄 수 있다.

---

## 0단계 — 자격 증명 준비

```bash
cd 03-advanced/labs/oracle-mysql-operator-kind
cp secret.yaml.example secret.yaml
```

`secret.yaml`을 열어 `rootPassword: CHANGE_ME_local_lab_password`를 원하는
비밀번호로 바꾼다. 이 파일은 `.gitignore`에 걸려 있어 커밋되지 않는다 —
이후 단계에서 이 비밀번호를 `mysql` 접속 때마다 직접 타이핑하게 되니 기억해둘 것.

- [x] 0단계 완료 (secret.yaml 생성 및 비밀번호 변경)

---

## 1단계 — kind 클러스터 생성

```bash
kind create cluster --name mysql-operator-lab --config kind-config.yaml
kubectl cluster-info --context kind-mysql-operator-lab
kubectl get nodes -o wide
```

**왜**: `kind-config.yaml`은 control-plane 1개 + worker 3개를 정의한다. InnoDB
Cluster의 mysqld 파드 3개가 서로 다른 노드에 분산돼야, 08장이 설명하는
"페일오버 시 다른 노드로 트래픽/역할이 옮겨간다"는 상황을 실제로 관찰할 수 있다.

**확인**: `get nodes`에 4개 노드(`-control-plane`, `-worker`, `-worker2`, `-worker3`)가
모두 `Ready`.

- [x] 1단계 완료 (kind 클러스터 생성, 4개 노드 Ready 확인)

---

## 2단계 — Operator 설치

```bash
TAG=9.7.0-2.2.8
kubectl apply -f "https://raw.githubusercontent.com/mysql/mysql-operator/${TAG}/deploy/deploy-crds.yaml"
kubectl apply -f "https://raw.githubusercontent.com/mysql/mysql-operator/${TAG}/deploy/deploy-operator.yaml"
kubectl -n mysql-operator rollout status deployment/mysql-operator --timeout=180s
kubectl -n mysql-operator get pods
```

**왜**: 08장이 설명하는 CRD가 여기서 등록된다(`kind: InnoDBCluster`를 K8s가
이해하게 만드는 단계) — 그다음 배포되는 Operator 컨트롤러가 이 CRD를 감시하며
reconcile loop를 돈다. 최신 태그를 확인하려면:
`gh api repos/mysql/mysql-operator/tags --jq '.[0].name'`

**확인**: `mysql-operator` 네임스페이스의 파드가 `Running`.

- [x] 2단계 완료 (Operator 설치, mysql-operator 파드 Running 확인)

---

## 3단계 — InnoDBCluster 배포

```bash
kubectl create namespace mysql-lab
kubectl apply -f secret.yaml
kubectl apply -f innodbcluster.yaml
```

**왜**: `secret.yaml`이 먼저 있어야 InnoDBCluster 컨트롤러가 root 계정을
초기화할 수 있다. `innodbcluster.yaml`은 `instances: 3`(mysqld 3개) +
`router.instances: 1`을 선언한다 — `kubectl apply` 한 번으로 끝이지만,
실제로는 Operator가 첫 파드를 새 클러스터로 부트스트랩하고 나머지 두 파드를
Group Replication에 순서대로 합류시키는 몇 분짜리 과정이 뒤에서 진행된다
(08장 "왜 StatefulSet만으로는 부족한가" 참고).

- [x] 3단계 완료 (secret/InnoDBCluster apply 완료)

---

## 4단계 — 상태 관찰

```bash
kubectl -n mysql-lab get pods -w
```

파드 3개(mysqld) + Router 1개가 순차적으로 `Running`이 될 때까지 지켜본다
(`Ctrl-C`로 중단). 다 뜨면:

```bash
kubectl -n mysql-lab get innodbcluster my-cluster
```

`STATUS`가 `ONLINE`이 될 때까지 몇 분 걸릴 수 있다.

- [x] 4단계 완료 (파드 전부 Running, InnoDBCluster STATUS: ONLINE 확인)

---

## 5단계 — Router 경유 접속 + Group Replication 멤버 확인

**터미널을 하나 더 연다.** 그 터미널에서 port-forward를 띄우고 그대로 둔다
(포그라운드로 유지 — Ctrl-C 전까지 계속 떠 있어야 함):

```bash
kubectl -n mysql-lab port-forward svc/my-cluster 6446:6446
```

원래 터미널로 돌아와서 접속:

```bash
mysql -h 127.0.0.1 -P 6446 -uroot -p
```

비밀번호 프롬프트에 0단계에서 정한 값을 직접 입력한다. 접속되면:

```sql
SELECT MEMBER_HOST, MEMBER_STATE, MEMBER_ROLE
FROM performance_schema.replication_group_members;
```

**확인**: 멤버 3개, 전부 `MEMBER_STATE=ONLINE`, 그중 정확히 1개가
`MEMBER_ROLE=PRIMARY`. 이게 08장이 강조하는 "Galera의 멀티 프라이머리와 달리
Group Replication은 단일 프라이머리"라는 구조를 눈으로 보는 지점이다.

- [x] 5단계 완료 (Router 경유 접속, GR 멤버 3개 ONLINE·PRIMARY 1개 확인)

---

## 6단계 — 페일오버 드릴

같은 mysql 세션에서 계속 진행한다. 먼저 쓰기 테스트용 테이블을 만든다:

```sql
CREATE DATABASE IF NOT EXISTS lab;
CREATE TABLE IF NOT EXISTS lab.probe (id INT PRIMARY KEY, ts DATETIME);
```

5단계에서 확인한 `MEMBER_HOST` 값에서 파드 이름을 읽어낸다 — 예를 들어
`MEMBER_HOST`가 `my-cluster-0.my-cluster-instances.mysql-lab.svc.cluster.local`이면
파드 이름은 첫 `.` 앞부분인 `my-cluster-0`이다.

**세 번째 터미널**을 열어(mysql 세션과 port-forward는 그대로 둔 채) PRIMARY
파드를 강제 종료한다:

```bash
kubectl -n mysql-lab delete pod my-cluster-0   # 방금 확인한 실제 PRIMARY 파드 이름으로 교체
```

바로 이어서 같은 터미널에 아래 타이밍 루프를 붙여넣는다 — `<password>`를
0단계에서 정한 실제 비밀번호로 바꾼 뒤 실행:

```bash
DELETE_TS=$(date +%s)
for i in $(seq 1 60); do
  if mysql -h 127.0.0.1 -P 6446 -uroot -p'<password>' -e \
    "INSERT INTO lab.probe (id, ts) VALUES (1, NOW())
     ON DUPLICATE KEY UPDATE ts=NOW();" 2>/dev/null; then
    echo "쓰기 성공: $(( $(date +%s) - DELETE_TS ))초 경과 (시도 ${i}회째)"
    break
  fi
  echo "시도 ${i}: 아직 쓰기 불가"
  sleep 1
done
```

**왜**: `kubectl delete pod`로 PRIMARY를 강제 종료하면, 남은 두 멤버가 합의를
거쳐 새 PRIMARY를 선출한다 — 이 합의가 끝날 때까지 클러스터 전체가 쓰기를
받지 않는다. 이 루프는 그 "몇 초"를 실제 초 단위로 재는 것이다. 08장이
서술로만 언급한 대목(Galera 멀티 프라이머리와 달리 GR은 이 구간이 존재한다)을
직접 숫자로 확인하는 게 이 드릴의 핵심이다.

드릴이 끝나면 원래 mysql 세션(아직 살아있다면)이나 새 접속으로 새 PRIMARY를
다시 확인한다:

```sql
SELECT MEMBER_HOST, MEMBER_STATE, MEMBER_ROLE
FROM performance_schema.replication_group_members;
```

이 단계의 실제 결과(쓰기 재개까지 걸린 시간, 새 PRIMARY로 누가 뽑혔는지)를
공유해주면, `03-advanced/lab-notes-oracle-mysql-operator-kind.md`에 실제
관찰값으로 기록한다.

- [x] 6단계 완료 (PRIMARY 강제 종료 → 쓰기 재개 시간 측정 → 새 PRIMARY 확인)

---

## 7단계 — 정리

다 끝나면 두 번째 터미널(port-forward)을 Ctrl-C로 종료하고, 클러스터 자체를
지운다:

```bash
kind delete cluster --name mysql-operator-lab
```

- [x] 7단계 완료 (kind 클러스터 삭제, 선택)

