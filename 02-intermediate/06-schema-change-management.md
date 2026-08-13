# 스키마 변경 관리

## 핵심 개념 설명

애플리케이션이 발전하면 스키마도 계속 변한다. 컬럼 추가, 인덱스 생성, 테이블 분리, 제약 조건 변경 등은 피할 수 없다. 문제는 이 변경을 **여러 환경(개발/스테이징/운영)에 일관되게, 되돌릴 수 있게, 서비스 중단 없이** 적용하는 것이다. `ALTER TABLE`을 수동으로 각 서버에 실행하는 방식은 누락·불일치·장애를 부른다.

이를 체계화한 것이 **스키마 마이그레이션(형상 관리)** 이다. 스키마 변경을 코드처럼 버전 관리하고, 어떤 변경이 어떤 환경에 적용됐는지 추적하며, 순서대로 자동 적용한다. 핵심 원칙은 "**모든 변경은 스크립트로, 버전으로, 이력으로**"이다.

### 마이그레이션 도구

- **Flyway**: SQL 파일(`V1__init.sql`, `V2__add_index.sql`) 기반. 버전 번호 순으로 적용하고, `flyway_schema_history` 테이블에 적용 이력과 체크섬을 기록한다. 단순하고 직관적이다.
- **Liquibase**: XML/YAML/JSON/SQL 형식의 "changeSet" 단위로 변경을 정의한다. rollback 정의, DB 종류별 추상화, 조건부 실행 등 기능이 풍부하다.
- 그 외 프레임워크 내장 도구: Django Migrations, Rails Active Record Migrations, Spring Boot + Flyway/Liquibase 통합 등.

공통 규칙: 이미 적용된 마이그레이션 파일은 **수정하지 않는다**(체크섬 불일치 발생). 잘못됐으면 새 마이그레이션으로 교정한다.

### 온라인 DDL (무중단 스키마 변경)

전통적으로 `ALTER TABLE`은 테이블에 강한 락을 걸어, 큰 테이블에서는 변경 동안 읽기/쓰기가 막히는 서비스 중단을 유발했다. **온라인 DDL**은 변경 중에도 테이블에 대한 DML을 허용해 무중단으로 스키마를 바꾼다.

- **PostgreSQL**: `CREATE INDEX CONCURRENTLY`, `ADD COLUMN`(기본값 있는 컬럼도 11+부터 메타데이터만 수정), `ALTER TABLE ... VALIDATE CONSTRAINT`를 분리해 락 시간 최소화.

## 주요 명령어/문법

### 무중단 인덱스/컬럼 추가

**PostgreSQL**
```sql
-- 락을 거의 걸지 않고 인덱스 생성(빌드 중 쓰기 허용)
CREATE INDEX CONCURRENTLY idx_orders_status ON orders (status);

-- 컬럼 추가(11+에서 상수 기본값은 즉시, 테이블 재작성 없음)
ALTER TABLE orders ADD COLUMN priority int NOT NULL DEFAULT 0;

-- 큰 테이블 제약 추가는 2단계로: 우선 NOT VALID로 락 최소화 후 검증
ALTER TABLE orders ADD CONSTRAINT chk_amount CHECK (amount >= 0) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT chk_amount;
```



### Flyway 사용 흐름

```text
sql/
  V1__create_orders.sql
  V2__add_priority_column.sql
  V3__add_status_index.sql
```
```bash
flyway migrate    # 미적용 버전을 순서대로 적용
flyway info       # 각 버전의 적용 상태 확인
flyway validate   # 파일 체크섬과 이력 일치 검증
```

## 실습 예제 — 무중단으로 NOT NULL 컬럼 추가하기

시나리오: 운영 중인 대용량 `users` 테이블에 `signup_source` 컬럼을 서비스 중단 없이 추가한다. (기본값 없는 NOT NULL을 한 번에 걸면 테이블 전체 재작성/락이 발생할 수 있으므로 단계를 나눈다.)

```sql
-- 1) 우선 nullable 컬럼으로 추가 (빠름, 락 최소)
ALTER TABLE users ADD COLUMN signup_source VARCHAR(30);   -- PostgreSQL/MySQL 공통

-- 2) 애플리케이션 배포: 신규 INSERT는 이 컬럼을 채우도록 코드 반영

-- 3) 기존 행을 배치로 백필(한 번에 UPDATE 금지 — 락/부하)
UPDATE users SET signup_source = 'unknown'
WHERE signup_source IS NULL AND id BETWEEN 1 AND 100000;
-- ... 범위를 나눠 반복 ...

-- 4) 모두 채워진 뒤 NOT NULL 제약 추가
--    PostgreSQL: NOT VALID 후 검증으로 락 최소화 가능
ALTER TABLE users ALTER COLUMN signup_source SET NOT NULL;   -- 값이 다 차 있으면 짧게 끝남
```

이 "확장(expand) → 백필(migrate) → 정리(contract)" 패턴은 컬럼 이름 변경, 타입 변경, 테이블 분리 등 대부분의 무중단 스키마 변경에 적용되는 표준 전략이다.

## 변경 관리 프로세스

1. **변경은 코드로**: 마이그레이션 스크립트를 애플리케이션 코드와 같은 저장소에서 버전 관리한다.
2. **리뷰**: DDL도 코드 리뷰 대상. 락 영향, 롤백 방법, 데이터 백필 여부를 점검한다.
3. **하위 호환**: 애플리케이션의 구/신 버전이 잠시 공존해도 깨지지 않도록 확장-정리 단계를 분리한다.
4. **환경 순차 적용**: 개발 → 스테이징 → 운영 순으로 동일 스크립트를 적용한다.
5. **롤백 계획**: 되돌리는 스크립트 또는 복구 절차를 사전에 준비한다.
6. **적용 시점**: 트래픽이 낮은 시간대, 백업 직후에 수행하고 적용 전후 모니터링한다.

## 체크리스트

- [ ] 수동 DDL 방식의 문제(불일치·누락·이력 부재)를 설명하고 마이그레이션 도구의 필요성을 안다.
- [ ] Flyway와 Liquibase의 기본 개념과 차이를 안다.
- [ ] 이미 적용된 마이그레이션 파일을 수정하면 안 되는 이유(체크섬)를 안다.
- [ ] 전통적 `ALTER TABLE`이 왜 서비스 중단을 유발하는지, 온라인 DDL이 이를 어떻게 해결하는지 설명할 수 있다.
- [ ] 각 DBMS의 온라인 DDL 옵션(`CONCURRENTLY`, `ALGORITHM=INSTANT/INPLACE`, `ONLINE`)을 안다.
- [ ] 확장 → 백필 → 정리 패턴으로 무중단 컬럼/제약 변경을 설계할 수 있다.
- [ ] 스키마 변경의 리뷰·하위 호환·롤백·순차 적용 프로세스를 설명할 수 있다.
