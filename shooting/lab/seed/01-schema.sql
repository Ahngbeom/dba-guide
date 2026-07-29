-- 게임용 최소 주문 스키마.
-- 락 경합 스테이지에서 "같은 행을 노리는 여러 세션"을 만들기 좋게
-- 단일 테이블 + 명확한 기본키 구조로 둔다.
CREATE DATABASE IF NOT EXISTS shop
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS shop.orders (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  customer_id INT UNSIGNED    NOT NULL,
  status      VARCHAR(16)     NOT NULL DEFAULT 'NEW',
  amount      DECIMAL(10, 2)  NOT NULL DEFAULT 0,
  updated_at  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                       ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_status (status),
  KEY idx_customer (customer_id)
) ENGINE = InnoDB;
