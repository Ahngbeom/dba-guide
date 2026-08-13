# 08. Kubernetes 기반 DB Operator

## 1. 핵심 개념 설명

06장에서 다룬 Terraform/Ansible은 "인프라를 원하는 상태로 만든 뒤, 실행이 끝나면 손을 뗀다"(one-shot)는 공통점이 있다. 그런데 데이터베이스처럼 상태(state)를 가진 워크로드는 만든 이후가 더 중요하다 — 노드가 죽으면 즉시 새 프라이머리를 뽑아야 하고, 백업은 스케줄대로 계속 돌아야 하며, 멤버를 추가할 때 데이터 동기화가 안전하게 이뤄져야 한다. Kubernetes의 **Operator 패턴**은 이런 "지속적인 운영 지식"을 컨트롤러 코드로 인코딩해, 사람이 밤새 지켜보지 않아도 자동으로 수행되게 한다.

### CRD와 reconcile loop

Operator는 **CRD(Custom Resource Definition)**로 "MySQL 클러스터가 3개 노드로 존재해야 한다" 같은 **원하는 상태**를 선언하게 해준다. 사용자가 CR(Custom Resource)을 `kubectl apply`하면, Operator의 컨트롤러가 **reconcile loop**를 통해 현재 상태를 계속 관찰하며 원하는 상태와의 차이를 줄여나간다. 노드가 죽어 실제 상태가 "2개 노드"가 되면, 컨트롤러가 이를 감지해 자동으로 새 노드를 추가하고 클러스터에 재합류시킨다. Terraform의 `plan`/`apply`가 "그 순간의 차이"만 반영한다면, Operator는 **항상 감시하고 있다**는 점이 핵심 차이다.

### 왜 StatefulSet만으로는 부족한가

Kubernetes의 `StatefulSet`은 안정적인 네트워크 식별자(파드 이름 고정)와 파드별 전용 `PersistentVolumeClaim`을 보장한다. 하지만 그것만으로는 DB 클러스터를 운영할 수 없다:

- **클러스터 부트스트랩**: 첫 노드는 새 클러스터를 초기화하고, 이후 노드는 기존 클러스터에 합류(데이터 동기화)해야 한다 — 이 순서와 방식은 DB 엔진마다 다르다.
- **자동 페일오버**: 프라이머리 파드가 죽으면 누가 새 프라이머리가 될지 합의하고, 나머지 노드를 그쪽으로 재연결해야 한다.
- **백업/PITR**: 정기 백업, 특정 시점 복구는 DB 엔진의 도구(예: Xtrabackup, mysqldump)와 스케줄링이 결합돼야 한다.

Operator는 이 도메인 지식을 컨트롤러 코드에 담아, `StatefulSet` 위에 클러스터 부트스트랩·페일오버·백업까지 자동화한다.


### PostgreSQL 진영도 같은 패턴을 따른다

PostgreSQL 생태계에서는 **CloudNativePG**가 가장 활발한 Operator다. `Cluster` CRD로 프라이머리/스탠바이 구성, 자동 페일오버, 백업(WAL 아카이빙 포함)을 선언적으로 관리한다는 점에서 MySQL Operator들과 동일한 철학을 공유한다.


### 트레이드오프

02-intermediate/07-08장의 클라우드 매니지드 DB(RDS/Cloud SQL)와 비교하면, Kubernetes Operator는 **운영 부담이 더 크다**. 스토리지 클래스(StorageClass)와 볼륨 성능, 파드의 리소스 요청/제한(requests/limits), 네트워크 정책(NetworkPolicy)까지 직접 설계·관리해야 한다. 대신 특정 클라우드 벤더에 종속되지 않고(멀티/하이브리드 클라우드 이식성), 애플리케이션과 동일한 플랫폼(K8s)에서 배포·관측 도구를 재사용할 수 있다는 이점이 있다. **K8s 운영 역량을 갖춘 팀**이면서 이식성·비용 이점이 클 때 적합한 선택지다.

## 2. 주요 명령어/문법


### PostgreSQL — CloudNativePG (병기)

```yaml
# pg-cluster.yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: pg-cluster
spec:
  instances: 3
  storage:
    size: 20Gi
```

```bash
kubectl apply -f pg-cluster.yaml
kubectl get cluster
```


## 3. 실습 예제


**PostgreSQL(CloudNativePG) 대응**: 위 `pg-cluster.yaml`(instances: 3)로 동일한 패턴을 적용하면, CloudNativePG가 스트리밍 복제 기반 프라이머리/스탠바이를 구성하고 프라이머리 파드 장애 시 자동으로 스탠바이를 승격시킨다. 백업은 별도 `Backup` CR 또는 지속적 WAL 아카이빙 설정으로 구성한다.

> **트레이드오프 메모**: 클라우드 매니지드 DB(02-intermediate/08장)는 이 모든 것을 클라우드가 대신 해주지만 특정 벤더에 묶인다. Kubernetes Operator는 어떤 클라우드/온프레미스에서도 동일한 방식으로 동작하지만, 스토리지 성능·리소스 튜닝·업그레이드 절차를 팀이 직접 책임져야 한다. "이미 K8s 위에서 애플리케이션을 운영 중이고, 멀티/하이브리드 클라우드 이식성이 중요한 조직"에 특히 유리하다.

## 4. 체크리스트

- [ ] CRD + reconcile loop 기반 Operator 패턴이 Terraform/Ansible의 one-shot 실행과 어떻게 다른지 설명할 수 있다.
- [ ] `StatefulSet`만으로 DB 클러스터 운영이 부족한 이유(부트스트랩·자동 페일오버·백업)를 설명할 수 있다.
- [ ] 파드 장애를 주입해 Operator가 자동으로 재생성·재합류시키는 과정을 관찰할 수 있다.
- [ ] CR 기반으로 온디맨드/스케줄 백업을 구성할 수 있다.
- [ ] PostgreSQL 진영의 대응 Operator(CloudNativePG)를 알고, MySQL Operator들과 철학이 같음을 설명할 수 있다.
- [ ] 클라우드 매니지드 DB 대비 Kubernetes Operator 운영의 트레이드오프(운영 부담 vs 이식성)를 설명할 수 있다.
