# 학습 점검(퀴즈/시험) 문항 작성 가이드

이 문서는 TUI 학습 점검 기능의 **문제은행(JSON) 스키마**와 **작성 워크플로**를 설명한다. 관련 코드는 외부 의존성 없이 파이썬 표준 라이브러리만 사용한다.

- `exam` — 저장소 루트의 간편 실행 래퍼. `./exam`(인자 없이)으로 실행하면 TUI에서 **DBMS → 티어 → 챕터**를 골라 시험을 시작한다. `python3 scripts/exam.py`의 단축이며 인자·옵션을 그대로 전달한다.
- `scripts/exam.py` — TUI 시험 러너(엔트리포인트). curses 풀스크린, 비-tty 환경에서는 라인 모드로 자동 폴백.
- `scripts/seed_exam.py` — 챕터 Markdown에서 문항 **초안(seed)** 을 생성.
- `exams/<티어>/<챕터>.json` — 챕터별 문제은행.
- `tests/test_exam.py` — 채점·정규화·시드 파싱 단위 테스트(`python3 -m unittest discover -s tests`).

## 전체 흐름 (하이브리드)

문항은 "자동 초안 생성 → 사람 보정"의 하이브리드 방식으로 만든다.

```
1) python3 scripts/seed_exam.py 01-beginner/01-rdbms-fundamentals.md \
       > exams/01-beginner/01-rdbms-fundamentals.json
2) 사람이 초안을 열어 객관식 오답지·정답(accept)·모범답안(reference)·해설을 보완하고
   _draft / TODO 표시를 제거한다.
3) ./exam exams/01-beginner/01-rdbms-fundamentals.json 로 확인
   (또는 그냥 ./exam 으로 TUI에서 골라 확인).
```

시드는 어디까지나 **출발점**이다. 체크리스트 항목은 essay 스텁으로, 실습 예제 코드블록의 `-- 결과:`/`-- ERROR:` 주석은 short 스텁으로 뽑히며, 좋은 오답지와 채점 기준은 사람이 채워야 한다.

## JSON 스키마

챕터 1개 = 파일 1개. 최상위 구조:

```json
{
  "chapter": "01-beginner/01-rdbms-fundamentals.md",
  "title": "01. 관계형 데이터베이스 기초",
  "questions": [ { ... }, { ... } ]
}
```

- `chapter` — 대응하는 챕터의 저장소 루트 기준 상대 경로(불합격 시 재학습 안내에 쓰임).
- `title` — 시험 화면 상단에 표시되는 제목.
- `questions` — 문항 배열(비어 있으면 검증 실패).

### 문항 공통 필드

| 필드 | 필수 | 설명 |
|---|---|---|
| `id` | 권장 | 문항 고유 식별자(kebab-case). |
| `type` | ✅ | `mcq` \| `short` \| `essay`. |
| `dbms` | 권장 | `neutral`(기본) \| `postgresql` \| `mysql` \| `oracle`. 런타임 `--dbms` 필터 기준. |
| `q` | ✅ | 질문 텍스트. **정답을 노출하면 안 된다**(아래 규칙 참고). |
| `explain` | 선택 | 정오 판정 후 보여 줄 해설. |
| `hint` | 권장 | TUI에서 `h`(라인 모드는 답 대신 `h` 입력)로 보는 힌트. **정답을 직접 알려주지 않는** 실마리로 작성. |
| `shuffle` | 선택 | mcq 전용. 기본 `true`(보기 순서 무작위). `false`면 보기 순서 고정(예: '위 모두 정답' 류 보기가 있을 때). |

### 유형별 필드

**`mcq` — 객관식(단일 정답).** 이론 개념 검증에 사용.

```json
{
  "id": "rdbms-acid-i", "type": "mcq", "dbms": "neutral",
  "q": "ACID의 'I'가 뜻하는 것은?",
  "choices": ["격리성(Isolation)", "무결성(Integrity)", "독립성", "색인"],
  "answer": 0,
  "explain": "I는 Isolation(격리성)..."
}
```

- `choices` — 보기 배열(2개 이상).
- `answer` — 정답 보기의 **0-기반 인덱스**(범위를 벗어나면 검증 실패).
- O/X 문항은 보기 2개짜리 mcq로 표현한다.

**오답을 정답만큼 구체적으로 써라.** 보기 순서는 출제할 때 섞으므로 위치는 정답을
알려주지 않는다. 그런데 **길이**는 알려준다 — 처음 측정했을 때 85문항 중 64개(75%)
에서 정답이 다른 어떤 보기보다 길었고, 정답 평균 37자 대 오답 21자였다. "항상 가장
긴 것을 찍는다"만으로 통과 기준(70%)을 넘겨, 한 문제도 읽지 않고 객관식을 통과할
수 있었다.

원인은 나쁜 의도가 아니라 습관이다 — 정답은 **정확하게** 쓰려다 길어지고, 오답은
"어차피 틀린 것"이라 짧게 던지게 된다. 위 예시가 그 모양이다("격리성(Isolation)"
대 "색인").

가장 좋은 방법은 **정답을 뒤집어 오답을 만드는 것**이다. "A는 X, B는 Y"가 정답이면
"A는 Y, B는 X"를 오답으로 둔다 — 길이가 저절로 같아지고, 개념을 반대로 알고 있는
사람만 걸린다. `03-advanced/02`의 HA/DR 문항이 그 모양이다.

**수식어 하나가 오답을 정답으로 만든다.** "TDE가 막지 **못하는** 위협은?"의 오답을
구체화한다고 "**암호화되지 않은** 백업 매체의 유출"이라 쓰면, TDE는 그것을 막지
못하므로 정답이 하나 더 생긴다. 오답을 늘린 뒤에는 **각 오답이 여전히 거짓인지**
다시 읽어라.

`tests/test_exam.py`의 `AnswerLeakLintTest`가 은행별로 못을 박아 두었다(스테이지
진단 문항은 `tests/test_shooting.py`의 `DiagnosisChoiceLengthTest`). 남은 빚은
기준선으로 적어 두었으니 **늘리지 말고 줄여라** — 은행을 고쳤으면 그 줄의 숫자도
함께 낮춘다. 새로 만든 은행은 기준선이 없어 절반이 상한이다. 동점은 세지 않는다
(같은 길이의 오답이 있으면 "가장 긴 것"을 고를 수 없다).

**`short` — 주관식(단답/명령어).** 명령어·실전 검증에 사용. **자동 채점.**

```json
{
  "id": "priv-revoke-insert", "type": "short", "dbms": "neutral",
  "q": "app_user에게서 employees 테이블의 INSERT 권한을 회수하는 SQL을 작성하시오.",
  "accept": ["revoke insert on employees from app_user"],
  "explain": "REVOKE INSERT ON employees FROM app_user;"
}
```

- `accept` — 허용 정답 목록. 사용자 입력을 **정규화**한 뒤 이 목록의 정규화 결과와 하나라도 일치하면 정답.
- 정규화 규칙(`normalize_answer`): 대소문자 무시, 앞뒤 공백 제거, 내부 연속 공백 1칸 축소, 끝 세미콜론 제거. 따라서 `GRANT SELECT ON emp TO app;`과 `grant  select on emp to app`는 같게 취급된다.
- 표기 변형이 여럿이면 모두 `accept`에 넣는다(예: `["1nf", "제1정규형", "first normal form"]`).
- psql 메타 명령의 백슬래시는 JSON에서 이스케이프한다: `\l` → `"\\l"`.

**`essay` — 서술형.** 개념 서술 검증에 사용. **자동 채점 불가 → 자기채점.**

```json
{
  "id": "rdbms-normalization-why", "type": "essay", "dbms": "neutral",
  "q": "정규화가 데이터 이상 현상을 줄이는 원리를 설명하시오.",
  "reference": "정규화는 하나의 사실을 한 곳에만 저장하도록 데이터를 분리한다...",
  "keywords": ["중복", "분리", "이상 현상"]
}
```

- `reference` — 모범답안(필수). 사용자가 답을 적은 뒤 이 답안을 보여 주고 스스로 `y`/`n`으로 채점한다(`s`로 건너뛰면 채점에서 제외).
- `keywords` — 자기채점을 돕는 핵심 키워드(선택).

## 문항 작성 규칙 — 정답 노출 금지

`q`(질문)에 정답을 그대로 담으면 안 된다. 특히 `short`에서 `"(예: 1NF)"`처럼 예시로 정답을 보여주는 실수를 하지 말 것. 실마리가 필요하면 `hint`에 담되, 힌트도 정답을 직접 알려주지 말고 방향만 제시한다. 회귀 방지 테스트(`AnswerLeakLintTest`)가 "정규화한 `q`에 정규화한 `accept`가 포함되면 실패"하도록 강제한다.

## 채점과 통과 기준

- `mcq`/`short`는 자동 채점되어 **정답률(score)** 과 **등급(A~F)** 을 계산한다. 등급 임계: A≥90 · B≥80 · C≥70 · D≥60 · F<60(%). 통과선 `PASS_THRESHOLD`(기본 0.7)와 정합.
- `essay`는 자기채점 결과만 별도로 집계하며 정답률 계산에는 포함하지 않는다.
- 자동채점 정답률이 `PASS_THRESHOLD` 미만이면 재학습을 권장한다.
- 시험 중 헤더에 실시간 진행상황(`채점 N/총 · 정답 · 오답 · 정답률 · 등급`)과 문항별 점 스트립이 표시된다.

## TUI 조작법

- **문항 화면(공통)**: `←`/`→` 이전·다음 문항, `h` 힌트 토글, `m` 효과(플래시·비프) 토글, `q` 결과/종료.
  - `mcq`(미제출): `↑`/`↓` 보기 이동, `Enter` 제출. 보기 순서는 매 세션 무작위(`shuffle:false`면 고정).
  - `short`/`essay`(미제출): `Enter`로 **입력 오버레이** 진입.
- **제출 확정(잠금)**: 제출은 문항당 **한 번**만 반영된다. 채점과 동시에 정답이 공개되므로, 재제출을 허용하면 정답률·연속 정답을 조작할 수 있기 때문이다. 제출된 문항은 읽기 전용이 되고(`↑↓` 비활성), `Enter`는 **다음 문항**(마지막 문항이면 **결과 화면**)으로 이동한다. 하단 키 범례가 상태에 따라 `Enter 제출` / `Enter 답 입력` / `Enter 다음 문항` / `Enter 결과 보기`로 바뀐다.
  - 서술형을 `s`로 **건너뛴 경우는 채점된 답이 아니므로 잠기지 않아** 나중에 다시 풀 수 있다.
  - 내부적으로 `is_locked(state)`가 잠금을, `record_streak(session, correct, already_counted)`와 상태의 `counted` 플래그가 **집계 멱등성**(연속 정답 중복 카운트 방지)을 담당한다.
- **입력 오버레이**: `←`/`→` 커서 이동(`Option+←/→`는 단어 단위), `Home`/`End`, `Backspace`/`Delete`로 중간 편집, `Enter` 제출, `Esc` **닫기(작성 내용은 초안으로 보존)**.
  - **서술형은 여러 줄 입력**을 지원한다: `Shift+Enter` 또는 `Option+Enter`로 개행, `↑`/`↓`로 줄 이동, `Home`/`End`는 현재 줄의 처음/끝. 단답(`short`)은 한 줄 입력이라 개행 키가 무시된다.
  - 일부 터미널은 `Shift+Enter`를 `Enter`와 구분해 보내지 않는다. 그럴 때는 **`Option+Enter`가 확실한 대체**다.
  - **항상 통하는 대체키**: `Ctrl+A` 줄 처음 · `Ctrl+E` 줄 끝 · `Alt+b`/`Alt+f` 또는 `Ctrl+←`/`Ctrl+→` 단어 이동. 터미널 설정과 무관하게 동작하므로 `Option` 조합이 안 먹을 때 쓴다.
  - 수식키 조합은 터미널에 따라 ESC 접두 시퀀스(`ESC b`, `ESC ESC[D`, `ESC[1;3D`)로 오기도 하고, ncurses가 미리 해석한 **확장 키코드**(`kLFT3`=Alt+←, `kRIT5`=Ctrl+→)로 오기도 한다. 러너는 두 경로를 모두 해석하므로 화면이 닫히거나 문항이 넘어가는 오작동은 발생하지 않는다.
  - **`Option` 조합이 전혀 반응하지 않는다면** 터미널이 Option을 수식키로 보내지 않는 설정이다. Ghostty는 `macos-option-as-alt = true`, iTerm2는 *Left Option key: `Esc+`*, Terminal.app은 *Option 키를 Meta 키로 사용*을 켜야 한다.
  - **키 진단**: `./exam --keydebug`를 실행하면 누른 키의 원시 바이트와 해석 결과를 보여준다. 단어 이동이 되려면 `alt`/`ctrl` + `KEY_LEFT`/`KEY_RIGHT`(또는 `'b'`/`'f'`)로 잡혀야 한다.
- **정답/오답 이벤트**: 제출 직후 강조 배너 + 화면 플래시(오답은 비프음)를 ~0.5초 재생하고, 연속 정답이 이어지면 `🔥 연속 N`을 헤더에 표시(3·5·10·15·20·30·50연속에서 축하 배너). `m`으로 끄거나 `--no-effects`로 무음 시작. 결과 화면에 최고 연속 정답 표시.
- 비-tty(파이프/CI)에서는 라인 모드로 순차 진행(자유 이동 없음). 힌트는 답 대신 `h`를 입력해 본다. 연속 정답은 텍스트(`🔥 연속 N`)로 표시(플래시/비프 없음).
- **이어서 진행**: 시험을 마치면 결과 화면 메뉴(다른 챕터(같은 티어) / 처음부터 다시 선택 / 종료)에서 나가지 않고 다음 챕터로 이어갈 수 있다.

## 결과 로컬 저장

각 시험 결과는 `.exam-results/results.jsonl`에 한 줄씩 append된다. **개인 학습 기록이므로 git에 커밋하지 않는다**(`.gitignore`에 `.exam-results/` 등록). 응답한 문항이 하나도 없으면 저장하지 않는다. 레코드 필드:

```json
{"ts": "2026-07-23T10:00:00", "chapter": "01-beginner/01-...md",
 "title": "...", "dbms": "postgresql" | "all",
 "auto_total": 6, "auto_correct": 5, "score": 0.8333, "grade": "B",
 "essay_total": 2, "essay_correct": 1, "best_streak": 4,
 "wrong_ids": ["rdbms-acid-d", ...]}
```

챕터 선택 화면은 이 기록을 읽어 각 챕터의 지난 최고(정답률→등급 우선)를 `[지난 최고 B·83%]`로 표시한다. 저장 실패(디스크/권한)는 시험을 막지 않고 경고만 낸다.

## 벤더(`dbms`) 필터

DBMS는 `./exam`(인자 없이) 실행 시 **TUI 첫 화면에서 선택**한다(전체 / PostgreSQL / MySQL / Oracle). 특정 벤더를 고르면 `dbms`가 `neutral` 또는 그 벤더인 문항만 출제된다(다른 벤더 전용 문항 제외). CLI로 `--dbms postgresql`을 주면 그 화면을 건너뛰고 곧바로 필터가 적용된다. 벤더별 명령어 문항에는 반드시 해당 `dbms`를 지정하고, 세 벤더 공통 개념은 `neutral`로 둔다.

`exams/**/*.json`은 브랜치 생성 시 필터링하지 않는다(생성 스크립트는 `*.md`만 처리). JSON은 모든 브랜치에 벤더 중립 슈퍼셋으로 유지되고, 걸러 내기는 런타임 `--dbms`가 담당한다. 자세한 배경은 `docs/dbms-branch-strategy.md` 참고.

## 새 챕터 문항을 추가할 때

1. `python3 scripts/seed_exam.py <챕터.md> > exams/<티어>/<챕터>.json`로 초안 생성.
2. 초안을 보정한다: 체크리스트 essay에 `reference`를 채우고, 개념 항목은 `mcq`로 바꿔 오답지를 만들고, 명령어 항목은 `short`로 바꿔 `accept`를 정리한다. `_draft`/`TODO` 흔적을 모두 제거한다.
3. 각 챕터 `## 체크리스트`의 역량을 최소 1문항 이상 커버하는지 확인한다.
4. `python3 -m unittest discover -s tests`로 스키마 검증(RealBanksTest)이 통과하는지, 실제로 `scripts/exam.py`로 풀어 보며 확인한다.
