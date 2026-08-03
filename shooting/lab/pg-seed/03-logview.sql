-- 감시 소스. MySQL의 mysql.general_log 에 해당한다.
--
-- PostgreSQL은 명령 이력을 테이블이 아니라 로그 파일로 남기므로, contrib 모듈
-- file_fdw 로 그 CSV를 외부 테이블처럼 읽는다. 그래야 판정 구조(SQL로 조회하고
-- 사용자로 가른다)를 그대로 옮겨올 수 있다.
--
-- 파일명 주의: log_filename 이 'pg' 여도 csvlog 가 '.csv' 를 덧붙여 pg.csv 가 된다.
CREATE EXTENSION IF NOT EXISTS file_fdw;
CREATE SERVER IF NOT EXISTS dbshoot_log FOREIGN DATA WRAPPER file_fdw;

CREATE FOREIGN TABLE IF NOT EXISTS command_log (
  log_time timestamptz, user_name text, database_name text, process_id int,
  connection_from text, session_id text, session_line_num bigint,
  command_tag text, session_start_time timestamptz, virtual_transaction_id text,
  transaction_id bigint, error_severity text, sql_state_code text, message text,
  detail text, hint text, internal_query text, internal_query_pos int,
  context text, query text, query_pos int, location text, application_name text,
  backend_type text, leader_pid int, query_id bigint
) SERVER dbshoot_log
  OPTIONS (filename '/var/lib/postgresql/data/log/pg.csv', format 'csv');
