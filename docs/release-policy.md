# 릴리스 정책

이 문서는 이 저장소의 버전을 어떻게 매기고, 태그와 릴리스를 어떤 절차로 내는지 설명한다. 대상 독자는 릴리스를 발행하는 사람이다.

## 무엇에 버전을 붙이는가

이 저장소에는 콘텐츠(학습서 챕터)와 도구(`./exam`, `./shoot`)가 한 트리에 함께 있다. 버전은 **그 둘을 묶은 저장소 전체의 스냅샷**을 가리킨다. 도구에만 따로 버전을 두지 않는다 — 스테이지가 챕터를 인용하고 문제은행이 챕터를 따라가는 구조라, 어차피 같이 움직이는 것을 둘로 쪼개면 "챕터 v1.3에 맞는 shoot 버전이 뭐냐"는 질문만 새로 생긴다.

## 버전 규칙

`v` 접두어를 붙인 SemVer를 쓴다(`v1.0.0`). 자리마다 올리는 기준은 다음과 같다.

| 자리 | 올리는 경우 |
|---|---|
| **MAJOR** | 이전 버전을 쓰던 사람의 손이 필요한 변경. 스테이지 JSON·문제은행 JSON 스키마의 비호환 변경, `./exam`·`./shoot` CLI 인자의 제거나 의미 변경, 진행 기록(`.shooting-progress`)·결과 기록(`.exam-results`) 형식의 비호환 변경, 티어 재편처럼 챕터 경로가 대량으로 깨지는 개편 |
| **MINOR** | 더해지는 것. 챕터 추가, 스테이지 추가, 문제은행 추가, 새 DBMS 지원, 하위 호환되는 필드·옵션 추가 |
| **PATCH** | 고치는 것. 오탈자와 설명 오류, 판정 버그, 깨진 링크, 기존 문항 교정 |

애매하면 MINOR로 올린다. 콘텐츠 저장소에서 MAJOR는 "내용이 많이 바뀌었는가"가 아니라 **"바깥에서 이 저장소에 기대고 있던 것이 깨지는가"** 로만 판단한다. 챕터를 스무 개 추가해도 기존 경로가 그대로면 MINOR다.

사전 배포가 필요하면 `v1.1.0-rc.1` 형식을 쓴다.

## 태그는 `main`에만 찍는다

`postgresql`/`mysql`/`oracle` 브랜치에는 **태그를 찍지 않는다.** 이 브랜치들은 `main`에서 매번 새로 재생성되는 파생 뷰라(`docs/dbms-branch-strategy.md` 참고), 재생성 시점마다 히스토리가 갈린다. 거기에 태그를 찍으면 다음 재생성 때 어느 브랜치에서도 닿지 않는 고아 커밋을 가리키게 된다.

특정 릴리스의 단일 벤더 뷰가 필요하면 그 태그를 체크아웃해 생성 스크립트를 돌린다.

```bash
git switch --detach v1.0.0
./scripts/generate-branch.sh mysql
```

## 릴리스 전 점검

**CI 자동**이라고 적힌 항목은 스위트가 막아 준다(아래 참고). 나머지는 사람이 직접 실행한다.

- [ ] `git status --short`가 비어 있다
- [ ] `python3 -m unittest discover -s tests`가 전부 통과한다 — **CI 자동.** 태그는 tests 워크플로가 초록인 커밋에 찍는다
- [ ] `./shoot doctor`가 통과한다 — 스테이지 정의 파싱까지 여기서 걸린다. CI에는 없다(docker와 DB 클라이언트가 필요해서)
- [ ] 챕터를 추가·이동·삭제했다면 `README.md`의 링크가 맞다 — **CI 자동** (`scripts/check_content.py`의 links·orphans)
- [ ] 챕터를 추가·개정했다면 네 절 구조가 유지된다 — **CI 자동** (structure, 챕터만 본다)
- [ ] 벤더 마커를 건드렸다면 세 단일 벤더 뷰에서도 네 절 구조가 유지된다 — **CI 자동** (`ShippedContentTest`). 손으로 보려면:
  ```bash
  for v in postgresql mysql oracle; do python3 scripts/check_content.py --dbms "$v"; done
  ```
- [ ] 마크다운 파일 어디든(챕터뿐 아니라 `docs/`·`appendix/`·README 포함) 벤더 마커를 건드렸다면 세 벤더 모두에서 균형이 맞는다 — **CI 자동** (`scripts/check_content.py`의 markers). `generate-branch.sh`는 챕터가 아니라 트리의 모든 `*.md`를 필터하므로, 챕터로 범위를 좁히는 structure와 달리 이 검사는 범위를 좁히지 않는다
- [ ] 새 명령어를 넣었다면 해당 티어의 `*-commands-cheatsheet.md`에 행이 있다 — 판정 불가, 사람 몫
- [ ] 새 용어를 넣었다면 `appendix/glossary.md`·`appendix/dbms-comparison-matrix.md` 반영 여부를 판단했다 — 판정 불가, 사람 몫
- [ ] 챕터를 추가·개정했다면 대응하는 `exams/**/*.json`이 함께 갱신됐다 — 파일 **존재**만 CI 자동(banks). 내용이 챕터와 맞는지는 사람 몫
- [ ] `docs/dbms-branch-strategy.md`의 마킹 진행 표가 최신이다
- [ ] 벤더 브랜치 3개를 `main` 최신 시점에서 재생성해 push했다
- [ ] 릴리스 발행 **후** `curl … | bash`를 한 번 실제로 돌려 새 태그를 집는지 확인했다

## 절차

```bash
# 1. main 최신화
git switch main && git pull

# 2. 점검 (위 목록)
python3 -m unittest discover -s tests
./shoot doctor

# 3. 벤더 브랜치 재생성 — postgresql / mysql / oracle 각각
./scripts/generate-branch.sh postgresql
git push --force-with-lease origin postgresql

# 4. 초안 릴리스 생성 — 이 시점에는 태그가 생기지 않는다
gh release create vX.Y.Z --draft --target "$(git rev-parse main)" \
  --title "vX.Y.Z — <한 줄 요약>" --notes-file <노트 파일>

# 5. 웹에서 렌더링을 확인하고 발행 — 태그는 이때 생긴다
gh release edit vX.Y.Z --draft=false
```

**초안을 반드시 거친다.** GitHub 초안 릴리스는 발행 전까지 태그 ref를 만들지 않는다. 내용이 마음에 들지 않으면 흔적 없이 지우고 다시 쓸 수 있다. 반대로 태그를 먼저 찍고 릴리스를 만들면, 되돌릴 때 원격 태그 삭제까지 따라와 이미 fetch한 사람과 어긋난다.

`--target`에 브랜치 이름 대신 `git rev-parse main`의 결과를 넣는 이유도 같다. 초안이 떠 있는 동안 `main`에 커밋이 더 쌓이면, 브랜치 이름으로 잡아 둔 초안은 발행 시점의 최신 커밋에 태그를 찍는다 — 노트를 쓸 때 보던 트리가 아니다.

### 인스톨러와 태그의 순서

`install.sh`는 **최신 정식 태그**를 설치한다. 따라서 인스톨러 관련 변경을
`main`에 머지해도 태그를 발행하기 전까지는 아무도 그 변경을 설치하지 못한다.
스크립트 자체는 `raw.githubusercontent.com/.../main/install.sh`에서 받으므로
버그 수정은 즉시 반영되지만, **설치되는 내용은 태그를 기다린다.**

그래서 발행 직후 한 번은 실제로 돌려 봐야 한다. CI는 저장소 안에서 `install.sh`를
실행할 뿐 발행된 태그를 집어 오지는 않으므로, 이것도 사람 몫이다.

## 릴리스 노트 작성

- **`--generate-notes`에 맡기지 않는다.** 머지된 PR 제목을 시간순으로 늘어놓을 뿐이라 "무엇을 쓸 수 있게 됐는가"를 전하지 못한다. 게다가 PR 없이 직접 올라간 커밋은 통째로 빠진다 — 이 저장소의 초기 커리큘럼 본문이 그렇다.
- 커밋 순서가 아니라 **기능 축으로 재편성한다**: 학습서 / `./exam` / `./shoot` / 벤더 브랜치.
- **「알려진 한계」 절을 반드시 둔다.** 판정이 놓치는 자리, 벤더 커버리지 공백, 자동 검증이 닿지 않는 점검 항목 같은 것을 먼저 적어 두면, 나중에 누가 발견해도 버그 리포트가 아니라 이미 합의된 경계가 된다. 재료를 지어낼 필요는 없다 — `docs/shooting-game.md`의 「알려진 한계」와 「판정의 두 구멍」에 실측 기반으로 정리돼 있다.
- 끝에 숫자 요약과 전체 변경 이력 링크(`.../commits/vX.Y.Z`)를 붙인다.
- **`CHANGELOG.md`를 따로 두지 않는다.** 릴리스 본문이 정본이다. 파일로 이중화하면 동기화 비용만 늘고, 이력은 `gh release view vX.Y.Z`로 언제든 읽을 수 있다.

## CI가 하는 일과 하지 않는 일

워크플로는 `.github/workflows/tests.yml` 하나뿐이다. main 푸시와 PR에서 `python3 -m unittest discover -s tests`를 돌린다 — ubuntu에서 Python 3.9(`install.sh`가 약속한 하한)와 3.13으로, macOS에서 bash 3.2로. `workflow_dispatch`도 열어 뒀으니 태그 직전에 손으로 한 번 돌려도 된다.

파이프라인을 이 이상 키우지 않는 이유는 브랜치 전략 문서와 같다 — 학습서이지 소프트웨어 프로젝트가 아니고, 실행 코드는 외부 의존성 없는 Python 표준 라이브러리와 Bash뿐이다. **CI도 PyPI에서 아무것도 설치하지 않는다.** 린터를 붙이려면 그 원칙을 먼저 다시 논의한다.

`./shoot doctor`는 일부러 빼 뒀다. docker와 mysql/psql 클라이언트가 있어야 결과가 나오는데, 점검 목록이 doctor에 기대한 「스테이지 정의 파싱」은 `tests/test_shooting.py`의 `ShippedStagesTest`가, 문제은행 스키마는 `tests/test_exam.py`가 이미 검증한다.

트리거가 `push: branches: [main]`으로 좁혀져 있는 것은 실수가 아니다. 벤더 브랜치는 `scripts/generate-branch.sh`가 main의 트리를 통째로 복제해 만들므로 이 워크플로 파일도 함께 실려 간다. 브랜치를 한정하지 않으면 뷰를 재생성할 때마다 같은 테스트가 3번 더 돈다.

본문 정합성 검사도 **별도 스텝이 아니라 스위트 안에** 있다. `scripts/check_content.py`가 링크·고아 문서·챕터 네 절 구조·마커 균형·문제은행 존재를 판정하고, `tests/test_check_content.py`의 `ShippedContentTest`가 실제 저장소를 상대로 그것을 돌린다. 릴리스 전에 손으로 보고 싶으면 `python3 scripts/check_content.py`를 직접 실행하면 된다 — 어디가 틀렸는지 한 줄씩 찍어 준다.

구조 검사는 `main`의 트리뿐 아니라 **세 단일 벤더 뷰에도** 돌아간다. 절 하나가 통째로 한 벤더의 마커 안에 들어가면 나머지 두 브랜치에서 그 절이 사라지는데, `main`에서만 보면 정상으로 보인다 — 이슈 #90이 그 모양으로 v1.4.0까지 살아남았다. 필터는 `generate-branch.sh`가 쓰는 `filter_dbms.filter_lines`와 같은 함수라, 검사와 실제 브랜치가 갈라질 수 없다. 다만 구조 검사는 챕터만 본다. `generate-branch.sh`는 `find … -name '*.md'`로 트리의 모든 마크다운을 필터하므로, 마커 균형은 `markers` 검사가 챕터가 아닌 파일까지 포함해 따로 본다.

검사기는 **판정 가능한 것만** 본다. 무엇이 *새* 명령어인지, 어떤 용어가 용어집에 들어가야 하는지는 본문의 의미를 읽어야 하는 판단이라 자동화하지 않았다. 코드 블록에서 토큰을 긁어 치트시트와 대조하는 방식은 오탐이 압도적이어서, 결국 검사기를 끄게 만든다.

그래서 남는 사람 몫은 이것이다: **치트시트 행, 용어집·비교 매트릭스 반영, 문제은행 내용의 최신성, 벤더 브랜치 재생성.** 태그를 찍기 전에 위 목록을 직접 훑는다.
