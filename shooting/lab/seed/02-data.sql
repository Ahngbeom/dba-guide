-- 시드 데이터 20만 행.
-- 재귀 CTE 한 문장으로 채운다 — 프로시저 루프보다 훨씬 빠르고,
-- 초기화가 몇 초 안에 끝나야 `./shoot up`이 답답하지 않다.
--
-- 행 수 자체가 장애 재현에 꼭 필요하진 않지만, SHOW PROCESSLIST와
-- 실행 계획이 "장난감이 아닌" 규모로 보여야 실습 감각이 산다.
SET SESSION cte_max_recursion_depth = 1000000;

INSERT INTO shop.orders (customer_id, status, amount)
WITH RECURSIVE seq (n) AS (
  SELECT 1
  UNION ALL
  SELECT n + 1 FROM seq WHERE n < 200000
)
SELECT
  n % 5000,
  ELT(1 + (n % 4), 'NEW', 'PAID', 'SHIPPED', 'DONE'),
  ROUND(1000 + (n % 9000) + 0.99, 2)
FROM seq;
