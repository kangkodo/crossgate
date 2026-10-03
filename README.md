# 크로스게이트

**크로스게이트**는 AI 교차검수 개발 파이프라인의 실행 키트다. 이름은 cross(서로 다른 모델의 교차 검수)와 gate(단계마다 승인받아야 통과하는 관문)에서 왔다. 설계 원본은 `../crossgate-spec`(명세 v1.15)이다.

- 사용자는 Master(Claude)하고만 대화한다. Master가 Codex(`gpt-6-astra`)를 역할별로 호출한다.
- 기본 배치: 기획·Wiki는 Claude가 쓰고 Codex가 검수한다. 개발은 Codex가 하고 Claude가 검수한다. QA는 Codex가 한다.
- 검수 역할은 항상 읽기 전용으로 실행된다. Codex는 읽기 전용 샌드박스, Claude는 Read·Grep·Glob만 허용된다. 검수 세션을 이어 써도 같다.
- 모든 판정은 `ai-log/<날짜>/<실행>/verdicts.jsonl`에 남는다. 조건을 만족하면 자동 병합하고, 배포는 사용자 지시가 있을 때만 한다.

## 구성

| 경로 | 내용 |
|---|---|
| `CROSSGATE.md` | 실행용 규칙 (판정, 루브릭, 응답 형식, 상한, 병합·배포) |
| `roles/` | 역할 카드 9장 |
| `templates/` | request, todo, 프로젝트 설정(`config.json`), CI 검사, AGENTS/CLAUDE 안내, .gitignore 항목 |
| `bin/crossgate` | 명령 진입점. 구현은 `bin/cg/`의 명령·호출·검사 모듈 |
| `claude/` | `/crossgate` 스킬(Master 플레이북), `crossgate-reviewer` 서브에이전트 |
| `install.sh` | 프로젝트에 설치·업그레이드 |

## 설치

```bash
sh <킷 원본 경로>/install.sh <프로젝트 경로>
```

설치하면 `.crossgate/kit/`(킷 사본, 덮어씀), `.crossgate/config.json`(없을 때만 생성), `.claude/skills/crossgate/`, `.claude/agents/crossgate-reviewer.md`가 생긴다. AGENTS.md·CLAUDE.md에는 안내 한 단락, .gitignore에는 항목이 추가된다.
그다음 `config.json`의 `gate`, `forbidden`, `deploy`를 프로젝트에 맞게 고친다. CI 검사를 켜려면 `templates/crossgate.yml`을 복사한다.

### 작업공간(PM 모드)

git이 아닌 루트 아래 저장소가 여러 개이고 자체 작업 절차가 있으면 PM 모드로 설치한다. 엔진과 `/crossgate` 스킬을 깔고, 작업공간 규칙·`.gitignore`·CI는 건드리지 않는다.

```bash
sh <킷 원본 경로>/install.sh --workspace <작업공간 루트>
```

`config.json`의 `repos`에 저장소별 `gate`·`protected`를 적고, `codex`·`gate`·`verdict`에 `--repo`를 준다(CROSSGATE.md 11절). 루트에서 연 세션에서 `/crossgate`를 부르면 Master가 된다. 작업공간 규칙이 우선하고, 규칙이 정하지 않은 요청 정리·등급·재검수는 스킬을 따른다. 작업공간 규칙(AGENTS.md 등)에서 `/crossgate`와 `.crossgate/kit/CROSSGATE.md`를 가리킨다.

## 전역 스킬 (한 번만)

```bash
sh <킷 원본 경로>/install.sh --global
```

어느 폴더에서든 `/crossgate-setup`으로 설치·업그레이드할 수 있게 된다.

## 사용

프로젝트 폴더에서 연 Claude Code에 `/crossgate <할 일>`을 입력한다. 예: `/crossgate 가벼움으로 버튼 문구를 바꿔줘`. 설치는 `/crossgate-setup`, 버전 올리기는 `/crossgate 업그레이드`다.

| 등급 | 쓰는 경우 | 순서 |
|---|---|---|
| 가벼움 | 문구·스타일·배치, 저장소 하나 | 요청 → 개발·화면 전후 확인 → 개발 검수 → 병합 |
| 보통 | 계약·권한을 바꾸지 않는 기존 동작 조정 | 요청·결정 검수 → TODO → 개발·문서 갱신 → 개발 검수 → 병합 |
| 무거움 | 기능 추가·삭제, 여러 저장소, 데이터·API·권한 변경 | 요청·결정 검수 → 기획·검수 → 개발·검수 → 독립 QA → Wiki·검수 → 병합 |

Master가 등급을 추천하고 사용자가 확정한다. 코드를 먼저 조사한 뒤, 사용자가 정해야 할 결정과 실행 허락을 **한 번에 묻는다**. 승인 뒤에는 새 충돌 사실·반복 상한·허락 밖의 되돌릴 수 없는 행동일 때만 다시 묻는다.

끊겼으면 `/crossgate 이어서 진행해줘`라고 한다. Master가 `resume`으로 상태를 보고 요청서를 읽어 이어간다. 이미 답한 결정은 다시 묻지 않는다. `resume --list`로 최근 열린 실행을 찾을 수 있다. 실행 지정은 `--run <폴더>` → `CROSSGATE_RUN` → 현재 실행 순이다. 최근 활동이 있는 열린 실행이 둘 이상이면 쓰기 명령에 명시가 필요하다. 마치면 `close-run`으로 닫고, 다시 기록하려면 `close-run --reopen`을 쓴다.

### 0.7.0의 작업 도구

아래 명령은 `python3 .crossgate/kit/bin/crossgate` 뒤에 붙인다. 세부 규칙은 [CROSSGATE.md](CROSSGATE.md)를 따른다.

| 명령 | 용도 |
|---|---|
| `parallel --task <지시 파일> --task <지시 파일> --run <실행>` | 보통·무거움의 독립 개발을 동시에 처리. 합친 뒤 전체를 한 번 검수 |
| `docs-check [<문서 폴더>] [--require-index]` | 문서 목차·상대 링크·앵커 점검. 참조형 링크는 제외. 파일 수정 없음 |
| `audit-calls [--days <수>] [--save]` | 밖 Codex 호출 보고. 판정·병합을 막지 않음. 저장은 `raw/`에만 |
| `resume [--run <실행>]` | 단계·회차·미해결 지적·중단된 호출·다음 할 일 확인 |
| `codex <역할> --prompt-file <파일> [--fresh]` / `claude <역할> --prompt-file <파일> [--fresh]` | 역할 호출. 검수는 성공한 세션을 이어 쓰며 `--fresh`로 새로 시작 |

호출은 시작·끝과 강도 출처·세션·상태를 기록한다. 기본 시간 제한은 20분(개발·QA 40분), Codex 첫 작업 이벤트 대기는 180초다. 설정 `timeouts`, `parallel`(기본 최대 3), `docs`, `resume_reviews`(기본 true), `recent_minutes`(기본 60)로 조정한다. 옛 설정은 빠진 항목의 기본값으로 동작한다. 업그레이드 PR은 태그 출처·봉인·변경 범위·버전 조건과 필수 검사가 통과하면 자동 병합한다.

## 규칙 바꾸기

프로젝트의 `.crossgate/kit/`는 직접 고치지 않는다. 이 저장소에서 고친 뒤 `VERSION`과 `CHANGELOG.md`를 올리고 install.sh로 다시 설치한다. 규칙을 바꿀 때는 명세도 함께 개정한다. 태그를 단 뒤 발견한 결함은 태그를 옮기지 않고 0.7.1 같은 수정 버전으로 낸다.

## 검사

```bash
python3 tests/test_crossgate.py
```
