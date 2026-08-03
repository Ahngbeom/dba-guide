-- PostgreSQL 랩의 주문 스키마. MySQL 쪽(seed/01-schema.sql)과 같은 모양이라
-- 같은 시나리오를 두 벤더로 옮겨 놓고 비교할 수 있다.
CREATE TABLE IF NOT EXISTS orders (
  id          BIGSERIAL PRIMARY KEY,
  customer_id INTEGER      NOT NULL,
  status      VARCHAR(16)  NOT NULL DEFAULT 'NEW',
  amount      NUMERIC(10,2) NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_status   ON orders (status);
CREATE INDEX IF NOT EXISTS idx_customer ON orders (customer_id);

-- 시드 데이터 20만 행. generate_series 한 문장으로 채운다 —
-- 초기화가 몇 초 안에 끝나야 `./shoot up`이 답답하지 않다.
INSERT INTO orders (customer_id, status, amount)
SELECT (i % 5000) + 1,
       (ARRAY['NEW','PAID','SHIPPED','DONE'])[(i % 4) + 1],
       (i % 1000)::numeric
FROM generate_series(1, 200000) AS i
WHERE NOT EXISTS (SELECT 1 FROM orders);
