# 백업/복구 전략

## 핵심 개념 설명

백업은 DBA의 존재 이유이자 마지막 방어선이다. 하드웨어 고장, 실수로 인한 `DELETE`, 애플리케이션 버그, 랜섬웨어 등 어떤 상황에서도 데이터를 복구할 수 있어야 한다. 그리고 **검증되지 않은 백업은 백업이 아니다.** 실제로 복구가 되는지 주기적으로 시험해야 한다.

백업 전략은 두 가지 목표 지표로 설계한다.

- **RPO(Recovery Point Objective)**: 얼마만큼의 데이터 손실을 허용하는가. "최대 5분 전 상태까지 복구"라면 RPO=5분. RPO를 줄이려면 WAL/binlog 아카이빙이나 복제가 필요하다.
- **RTO(Recovery Time Objective)**: 복구에 얼마나 걸려도 되는가. "1시간 내 서비스 재개"라면 RTO=1시간. RTO를 줄이려면 물리 백업, 대기 서버(standby)가 유리하다.

### 논리 백업 vs 물리 백업

| 구분 | 논리 백업 | 물리 백업 |
|---|---|---|
| 방식 | SQL/데이터를 논리적으로 추출(`INSERT` 문, 텍스트/커스텀 포맷) | 데이터 파일 자체를 그대로 복사 |
| 도구 | `pg_dump`, `mysqldump`, Data Pump(`expdp`) | `pg_basebackup`, Percona XtraBackup, RMAN |
| 장점 | 이식성 좋음, 특정 테이블만 선택 가능, 버전 간 이동 용이 | 대용량에서 빠름, PITR 기반, 인스턴스 전체 복원 | 
| 단점 | 대용량에서 느림, 복원 시 인덱스 재생성 부담 | 이식성 낮음, 보통 전체 단위, 버전 종속 |
| 적합 | 중소 규모, 마이그레이션, 부분 백업 | 대규모 운영 DB, PITR 필요 시 |

### PITR (Point-in-Time Recovery)

PITR은 **기준 백업(base backup) + 그 이후의 변경 로그(WAL/binlog/아카이브 로그)** 를 결합해 "원하는 특정 시점"까지 복구하는 기법이다. 예를 들어 오늘 새벽 3시에 물리 백업을 받고 이후 WAL을 계속 보관했다면, "오후 2시 30분 00초, 잘못된 `DELETE` 직전"까지 정확히 되돌릴 수 있다.

PITR의 핵심 전제는 **변경 로그의 연속 아카이빙**이다. PostgreSQL은 WAL 아카이빙, MySQL은 binlog, Oracle은 아카이브 로그 모드가 켜져 있어야 한다.

## 주요 명령어/문법

### 논리 백업/복원


**MySQL**
```bash
mysqldump --single-transaction --routines --triggers mydb > mydb.sql
# 복원
mysql mydb < mydb.sql
# 논리 백업 고속 대안(8.0+)
mysqlpump / mysqlsh util.dumpInstance()
```


### 물리 백업


**MySQL (Percona XtraBackup)**
```bash
xtrabackup --backup --target-dir=/backup/full
xtrabackup --prepare --target-dir=/backup/full
```


## 실습 예제 — 특정 시점 복구(PITR)

시나리오: 매일 새벽 전체 백업을 받고 그 사이의 변경은 연속 로그로 아카이빙하는 운영 중, 오후 2시 30분에 실수로 전체 `DELETE`가 발생했다. 2시 29분 상태로 되돌린다.

DBMS가 달라도 원리는 같다 — **목표 시각 이전의 백업을 복원한 뒤, 그 시점부터 목표 시각까지의 로그만 재적용한다.** 다른 것은 로그의 이름, 재적용을 멈추는 방법, 그리고 복구를 끝내고 서비스를 여는 절차다.


### MySQL — 전체 백업 복원 + binlog 재적용

```bash
# 사전 준비(평상시): binlog가 켜져 있고 전체 백업이 있는 상태
#   my.cnf:  log_bin = /var/log/mysql/binlog

# --- 사고 발생 후 복구 절차 ---
# 1) 가장 가까운 전체 백업 복원
mysql < /backup/full_20260715.sql        # 논리 백업인 경우
# (XtraBackup이면 --prepare 후 데이터 디렉터리를 교체한다)

# 2) 재적용을 시작할 binlog 좌표 확인
head -30 /backup/full_20260715.sql       # --source-data=2(구 --master-data=2)가 주석으로 남긴 CHANGE REPLICATION SOURCE TO
                                          # (=1이면 주석이 아니라 실행 문으로 남아 1)번 복원 때 이미 실행돼 버린다 — 반드시 =2로 뜬다)
                                          # XtraBackup이면 xtrabackup_binlog_info 파일에 같은 좌표가 있다
mysql -e "SHOW BINARY LOGS;"             # 그 이후 파일들의 목록

# 3) 백업 시점 이후부터 목표 시각 직전까지만 재적용 (--start-position은 2)에서 확인한 값)
mysqlbinlog --start-position=194 \
            --stop-datetime="2026-07-15 14:29:00" \
            /var/log/mysql/binlog.000123 /var/log/mysql/binlog.000124 \
  | mysql

# 4) 데이터 확인 후 정상 서비스로 전환
```

여러 binlog 파일은 **한 번의 `mysqlbinlog` 호출에 함께 넘긴다.** 파일마다 따로 돌려 파이프하면 파일 경계를 걸친 트랜잭션이 끊긴다. GTID가 켜진 인스턴스에 재적용할 때 이미 실행된 트랜잭션이라는 오류가 나면 `--skip-gtids`를 준다.


어느 절차든 검증하는 것은 "백업이 있다"가 아니라 **"백업으로 목표 시각까지 실제로 되돌려 봤다"** 이다. 아카이브 한 조각이 비어 있으면 재적용은 정확히 그 지점에서 멈추는데, 사고 당일에 알게 되면 이미 늦다. 아래 정책이 분기 1회 복구 훈련을 요구하는 이유가 이것이다.

## 백업 주기/보관 정책 설계

- **주기**: 일반적으로 "주 1회 전체(full) + 매일 증분/차등 + 지속적 로그 아카이빙" 조합. RPO 요구가 엄격할수록 로그 아카이빙 간격을 촘촘히.
- **보관(retention)**: 예) 일간 백업 7일, 주간 백업 4주, 월간 백업 12개월(3-2-1 원칙: 사본 3개, 서로 다른 매체 2종, 원격지 1곳).
- **원격/오프사이트 저장**: 동일 데이터센터 소실에 대비해 S3 등 원격 스토리지에 복제.
- **암호화**: 백업 파일은 유출 시 전체 데이터 노출이므로 저장 시 암호화한다.
- **복구 훈련**: 분기 1회 이상 실제 복원 리허설로 백업 유효성과 RTO를 검증한다.
- **모니터링**: 백업 성공/실패를 알림으로 받고, 백업 크기·소요 시간 추세를 관찰한다.

## 체크리스트

- [ ] 논리 백업과 물리 백업의 원리·장단점·적합 상황을 구분해 설명할 수 있다.
- [ ] RPO와 RTO의 정의를 알고, 요구사항에서 백업 전략을 역산할 수 있다.
- [ ] 각 DBMS에서 논리 백업(`pg_dump`/`mysqldump`/`expdp`)을 수행하고 복원할 수 있다.
- [ ] 물리 백업 도구(`pg_basebackup`/XtraBackup/RMAN)의 용도를 안다.
- [ ] PITR의 원리(기준 백업 + 연속 로그 아카이빙)를 설명할 수 있다.
- [ ] MySQL에서 전체 백업을 복원한 뒤 binlog를 목표 시각까지 재적용할 수 있다.
- [ ] 3-2-1 원칙에 따른 백업 주기·보관·원격 저장 정책을 설계할 수 있다.
- [ ] 백업 암호화와 정기 복구 훈련의 필요성을 이해한다.
