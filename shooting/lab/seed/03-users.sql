-- 사용자 분리는 이 게임의 판정 구조를 떠받치는 토대다.
--
--   dba  = 플레이어. 엔진은 mysql.general_log에서 이 사용자의 명령만 감시한다.
--   app  = 엔진이 띄우는 범인/피해 세션. 앱 트래픽처럼 보이면서
--          플레이어의 명령 로그를 오염시키지 않는다.
--   root = 엔진 자신(docker exec 로컬 소켓 접속 → user_host가 다르다).

-- 플레이어 --------------------------------------------------------------
CREATE USER IF NOT EXISTS 'dba'@'%' IDENTIFIED BY 'shoot';
GRANT ALL PRIVILEGES ON shop.* TO 'dba'@'%';
GRANT SELECT ON performance_schema.* TO 'dba'@'%';
GRANT SELECT ON sys.* TO 'dba'@'%';
-- PROCESS: 다른 세션까지 SHOW PROCESSLIST로 볼 수 있어야 진단이 가능하다.
-- CONNECTION_ADMIN: 남의 세션을 KILL 할 수 있어야 복구가 가능하다.
GRANT PROCESS, REPLICATION CLIENT, REPLICATION SLAVE ON *.* TO 'dba'@'%';
GRANT CONNECTION_ADMIN, SYSTEM_VARIABLES_ADMIN ON *.* TO 'dba'@'%';
-- REPLICATION_SLAVE_ADMIN: CHANGE REPLICATION SOURCE / START·STOP REPLICA.
-- RELOAD: RESET REPLICA ALL과 FLUSH 계열. 복제를 잘못 걸었을 때 되돌릴 수단이
--   없으면 구축 스테이지에서 한 번 실수한 플레이어가 그대로 갇힌다.
GRANT REPLICATION_SLAVE_ADMIN ON *.* TO 'dba'@'%';
GRANT RELOAD ON *.* TO 'dba'@'%';
-- SHUTDOWN은 일부러 주지 않는다 — SQL RESTART 문을 막는다.
-- "그냥 껐다 켜기" 반사는 docker restart 감지(no-restart 제약)로 잡는다.

-- 엔진이 띄우는 부하/장애 세션 ------------------------------------------
CREATE USER IF NOT EXISTS 'app'@'%' IDENTIFIED BY 'app';
GRANT SELECT, INSERT, UPDATE, DELETE ON shop.* TO 'app'@'%';
-- ALTER/EXECUTE는 장애 주입에 필요하다 — 막힌 배포 ALTER(1-4), 부하 프로시저
-- 호출(4-1). 해당 스테이지의 setup도 같은 GRANT를 다시 실행하므로,
-- 이 파일을 고치기 전에 만들어진 랩 볼륨에서도 동작한다.
GRANT ALTER, EXECUTE ON shop.* TO 'app'@'%';

-- 복제 전용 계정 ---------------------------------------------------------
-- replica가 primary의 binlog를 받아갈 때 쓰는 계정. 사람이 쓰는 계정과
-- 분리해 두는 것이 실무 관례이고, 구축 스테이지(2-1)가 이 계정을 쓴다.
CREATE USER IF NOT EXISTS 'repl'@'%' IDENTIFIED BY 'repl';
GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%';

FLUSH PRIVILEGES;
