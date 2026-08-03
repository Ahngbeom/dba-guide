-- 사용자 분리는 MySQL 쪽과 같은 구조다. 판정이 여기에 얹혀 있다.
--
--   dba      = 플레이어. 엔진은 로그에서 이 사용자의 명령만 감시한다.
--   app      = 엔진이 띄우는 범인/피해 세션.
--   postgres = 엔진 자신. user_name 필터에 자연히 걸러지므로,
--              MySQL의 sql_log_off 같은 장치가 필요 없다.

-- 플레이어. 다른 세션을 보고 끊을 수 있어야 진단·복구가 가능하다.
-- pg_monitor 는 pg_stat_activity 의 다른 세션 질의문을 볼 수 있게 한다
-- (없으면 남의 query 가 <insufficient privilege> 로 가려진다).
CREATE ROLE dba LOGIN PASSWORD 'shoot';
GRANT pg_monitor TO dba;
GRANT pg_signal_backend TO dba;
GRANT ALL ON SCHEMA public TO dba;
GRANT ALL ON ALL TABLES IN SCHEMA public TO dba;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO dba;

-- 엔진이 띄우는 부하/장애 세션.
CREATE ROLE app LOGIN PASSWORD 'app';
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app;
