# 인덱스와 쿼리 튜닝

## 핵심 개념 설명

인덱스는 책의 색인과 같다. 테이블 전체를 처음부터 끝까지 훑는 대신(Full/Sequential Scan), 정렬된 별도 구조를 통해 원하는 행을 빠르게 찾게 해준다. DBA 업무의 상당 부분은 "어떤 인덱스를 만들 것인가"와 "쿼리가 그 인덱스를 제대로 타는가"를 판단하는 일이다.

인덱스는 조회 속도를 높이지만 **공짜가 아니다**. 인덱스가 걸린 테이블에 `INSERT/UPDATE/DELETE`가 일어나면 인덱스도 함께 갱신되므로 쓰기 비용이 늘고, 저장 공간도 추가로 차지한다. 따라서 "조회가 많고 쓰기가 상대적으로 적으며, 선택도(selectivity)가 높은(=중복이 적은) 컬럼"에 인덱스를 만드는 것이 원칙이다. 성별처럼 값 종류가 두세 개뿐인 컬럼에 단독 인덱스를 만드는 것은 대개 효과가 없다.

### 인덱스의 종류

- **B-Tree 인덱스**: 가장 보편적인 기본 인덱스. 등호(`=`)와 범위(`<, >, BETWEEN`), 정렬(`ORDER BY`), 접두 매칭(`LIKE 'abc%'`)에 효과적이다.
- **Hash 인덱스**: 등호 검색에만 특화. 범위 검색·정렬 불가. PostgreSQL/MySQL이 지원하나 용도가 제한적이다.
- **복합 인덱스(Composite Index)**: 여러 컬럼을 묶은 인덱스. **선두 컬럼(leftmost) 규칙**이 중요하다. `(a, b, c)` 인덱스는 `a`, `(a,b)`, `(a,b,c)` 조건에는 쓰이지만 `b`나 `c` 단독 조건에는 쓰이지 않는다.
- **커버링 인덱스(Covering Index)**: 쿼리가 필요로 하는 모든 컬럼이 인덱스에 포함되어, 테이블 본체를 읽지 않고 인덱스만으로 결과를 반환하는 경우. "Index Only Scan"으로 나타난다.
- **유니크 인덱스**: 값의 유일성을 보장하면서 조회도 빠르게 한다.
- **부분 인덱스(Partial Index)**: 조건을 만족하는 행만 인덱싱(`WHERE deleted = false` 등). 인덱스 크기를 줄인다.

## 주요 명령어/문법

### 인덱스 생성/삭제



**Oracle**
```sql
CREATE INDEX idx_orders_customer ON orders (customer_id);
CREATE UNIQUE INDEX idx_users_email ON users (email);
CREATE INDEX idx_cust_date ON orders (customer_id, created_at);
CREATE INDEX idx_orders_online ON orders (created_at) ONLINE; -- 무중단 생성
DROP INDEX idx_orders_customer;
```

### 실행계획 확인



```sql
-- Oracle
EXPLAIN PLAN FOR SELECT * FROM orders WHERE customer_id = 42;
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY);
-- 실제 실행 통계
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));
```

## 실행계획 읽는 법

`EXPLAIN ANALYZE`의 핵심 관찰 포인트:

- **접근 방식**: `Seq Scan`(PostgreSQL) / `type: ALL`(MySQL) / `TABLE ACCESS FULL`(Oracle)은 전체 스캔이라는 신호다. 큰 테이블에서 나타나면 인덱스 적용을 의심한다. 반대로 `Index Scan` / `type: ref/range` / `INDEX RANGE SCAN`은 인덱스를 탄 것이다.
- **조인 방식**: `Nested Loop`(소량+인덱스에 유리), `Hash Join`(대량 조인에 유리), `Merge Join`(정렬된 대량에 유리). 대량 데이터에 Nested Loop가 잡히면 비효율일 수 있다.

## 쿼리 튜닝 기본 원칙

1. **WHERE 절 컬럼을 함수로 감싸지 않는다.** `WHERE UPPER(name) = 'KIM'`은 인덱스를 무력화한다. 필요하면 함수 기반 인덱스나 생성 컬럼을 쓴다.
2. **선택도 높은 조건을 인덱스로.** 결과가 전체의 몇 % 이내로 좁혀지는 조건이 인덱스 효과가 크다.
3. **복합 인덱스는 선두 컬럼 규칙을 지킨다.** 자주 함께 쓰는 조건 순서와 등호→범위 순으로 컬럼을 배치한다.
4. **`SELECT *`를 피하고 필요한 컬럼만.** 커버링 인덱스로 Index Only Scan을 유도할 수 있다.
5. **암시적 형변환 주의.** 문자 컬럼에 숫자 조건(`WHERE phone = 1012345678`)을 주면 형변환으로 인덱스를 못 탄다.
6. **통계를 최신으로 유지한다.** 옵티마이저는 통계로 계획을 세우므로 대량 변경 후 `ANALYZE`(PostgreSQL/Oracle: `DBMS_STATS`)를 수행한다.
7. **`OR`은 종종 `UNION`으로, `NOT IN`은 `NOT EXISTS`나 `LEFT JOIN ... IS NULL`로** 바꾸면 계획이 개선되는 경우가 있다.

## 실습 예제



시나리오: 100만 건 주문 테이블에서 특정 고객의 최근 주문 조회가 느리다. (Oracle 기준)

```sql
-- 1) 현재 계획 확인 — TABLE ACCESS FULL이면 인덱스가 없거나 안 타는 것(전체 스캔)
EXPLAIN PLAN FOR
SELECT order_id, amount FROM orders
WHERE customer_id = 42
ORDER BY created_at DESC
FETCH FIRST 10 ROWS ONLY;

SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY);

-- 2) 조건+정렬을 커버하는 복합 인덱스 생성 (등호 컬럼 먼저, 정렬 컬럼 다음)
CREATE INDEX idx_orders_cust_created ON orders (customer_id, created_at DESC);

-- 3) 통계 갱신
EXEC DBMS_STATS.GATHER_TABLE_STATS(ownname => USER, tabname => 'ORDERS');

-- 4) 다시 계획 확인 — INDEX RANGE SCAN + TABLE ACCESS BY INDEX ROWID로 바뀌고 SORT 단계가 사라졌는지 확인
EXPLAIN PLAN FOR
SELECT order_id, amount FROM orders
WHERE customer_id = 42
ORDER BY created_at DESC
FETCH FIRST 10 ROWS ONLY;

SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY);
```

기대 결과: `TABLE ACCESS FULL` → `INDEX RANGE SCAN` + `TABLE ACCESS BY INDEX ROWID`로 바뀌고, `SORT ORDER BY` 단계가 사라지며 실행계획의 Cost가 크게 낮아진다.

## 체크리스트

- [ ] B-Tree, Hash, GIN/GiST, 커버링, 복합, 부분 인덱스의 용도를 구분해 설명할 수 있다.
- [ ] 복합 인덱스의 선두 컬럼(leftmost) 규칙을 이해하고 컬럼 순서를 설계할 수 있다.
- [ ] 인덱스의 쓰기 비용·저장 비용 트레이드오프를 설명할 수 있다.
- [ ] `EXPLAIN`과 `EXPLAIN ANALYZE`의 차이를 알고, 각 DBMS에서 실행계획을 뽑을 수 있다.
- [ ] 실행계획에서 전체 스캔/인덱스 스캔/조인 방식과 추정-실제 행 수 차이를 읽을 수 있다.
- [ ] WHERE 절 함수 사용, 암시적 형변환이 인덱스를 무력화함을 알고 회피할 수 있다.
- [ ] 통계 갱신(`ANALYZE`/`DBMS_STATS`)의 필요성을 이해한다.
- [ ] 느린 쿼리를 받아 인덱스 추가·쿼리 재작성으로 개선하고 전후 실행계획을 비교할 수 있다.
