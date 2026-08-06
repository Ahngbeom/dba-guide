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

**PostgreSQL**
```bash
# 백업(커스텀 포맷 권장 — 병렬/선택 복원 가능)
pg_dump -Fc -d mydb -f mydb.dump
pg_dumpall -f all.sql            # 전체 클러스터(롤/전역 객체 포함)

# 복원
pg_restore -d mydb --clean --if-exists mydb.dump
pg_restore -d mydb -j 4 mydb.dump   # 4-way 병렬 복원
```



### 물리 백업

**PostgreSQL**
```bash
# 기준 백업
pg_basebackup -D /backup/base -Fp -Xs -P
# 지속 아카이빙 설정 (postgresql.conf)
#   wal_level = replica
#   archive_mode = on
#   archive_command = 'test ! -f /arch/%f && cp %p /arch/%f'
```



## 실습 예제 — PostgreSQL PITR

시나리오: 매일 새벽 물리 백업 + WAL 아카이빙 중, 오후 2시 30분에 실수로 전체 `DELETE`가 발생. 2시 29분 상태로 복구한다.

```bash
# 사전 준비(평상시): 아카이빙이 켜져 있고 base backup이 있는 상태
pg_basebackup -D /backup/base_20260715 -Fp -Xs -P

# --- 사고 발생 후 복구 절차 ---
# 1) DB 정지
pg_ctl stop -D $PGDATA

# 2) 손상된 데이터 디렉터리를 치우고 base backup 복원
mv $PGDATA ${PGDATA}.broken
cp -a /backup/base_20260715 $PGDATA

# 3) 복구 목표 시점 지정 (postgresql.conf 또는 별도 파일)
cat >> $PGDATA/postgresql.conf <<'EOF'
restore_command = 'cp /arch/%f %p'
recovery_target_time = '2026-07-15 14:29:00+09'
recovery_target_action = 'promote'
EOF
touch $PGDATA/recovery.signal   # PG 12+ : 복구 모드 진입 신호

# 4) 기동 → WAL을 목표 시점까지 재생하고 promote
pg_ctl start -D $PGDATA

# 5) 데이터 확인 후 정상 서비스로 전환
```

MySQL의 PITR은 "가장 가까운 전체 백업 복원 → 그 이후 binlog를 `mysqlbinlog`로 목표 시점까지 재적용"하는 동일한 개념으로 수행한다.

```bash
mysqlbinlog --stop-datetime="2026-07-15 14:29:00" binlog.000123 | mysql
```

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
- [ ] PostgreSQL에서 특정 시점까지 PITR 복구 절차를 수행할 수 있다.
- [ ] 3-2-1 원칙에 따른 백업 주기·보관·원격 저장 정책을 설계할 수 있다.
- [ ] 백업 암호화와 정기 복구 훈련의 필요성을 이해한다.
