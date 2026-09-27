-- 앱은 superuser 로 붙지 않는다.
--
-- PostgreSQL 은 max_connections 중 superuser_reserved_connections(기본 3)를 남겨 두지만,
-- 그 슬롯은 superuser 만 쓸 수 있다. 앱 계정이 superuser 면 앱이 예약분까지 전부 먹어
-- 커넥션이 고갈된 순간 관측용 psql 도 접속하지 못한다 — 실제로 첫 실행에서 실험군의
-- pg_stat_activity 표집이 전부 실패했다.
--
-- 부호만 보면 "느려졌다"까지밖에 말할 수 없고, 총 커넥션 수가 인스턴스 수를 따라
-- 올랐는지를 봐야 기전을 주장할 수 있다. 그래서 관측 경로를 고갈로부터 보호한다.

CREATE ROLE appuser LOGIN PASSWORD 'appuser' NOSUPERUSER NOCREATEDB NOCREATEROLE;
