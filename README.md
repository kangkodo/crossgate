# 크로스게이트

**크로스게이트**는 AI 교차검수 개발 파이프라인의 실행 키트다. 이름은 cross(서로 다른 모델의 교차 검수)와 gate(단계마다 승인받아야 통과하는 관문)에서 왔다. 설계 원본은 `../crossgate-spec`(설계 명세 v1.16)이다.

- 사용자는 Master(Claude)하고만 대화한다. Master가 Codex(`gpt-6-astra`)를 역할별로 호출한다.
- 기본 배치: 기획·Wiki는 Claude가 쓰고 Codex가 검수한다. 개발은 Codex가 하고 Claude가 검수한다. QA는 Codex가 한다.
- 검수 역할은 항상 읽기 전용으로 실행된다. Codex는 읽기 전용 샌드박스, Claude는 Read·Grep·Glob만 허용된다. 검수 세션을 이어 써도 같다.
- 모든 판정은 `ai-log/<날짜>/<실행>/verdicts.jsonl`에 남는다. 조건을 만족하면 자동 병합하고, 배포는 사용자 지시가 있을 때만 한다.

## 구성

| 경로 | 내용 |
|---|---|
| `CROSSGATE.md` | 실행용 규칙 (판정, 루브릭, 응답 형식, 상한, 병합·배포) |
| `roles/` | 역할 카드 10장 |
| `templates/` | request, todo, 작업 지시(task), 프로젝트 설정(`config.json`), CI 검사, AGENTS/CLAUDE 안내, .gitignore 항목 |
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
| 무거움 | 기능 추가·삭제, 여러 저장소, 데이터·API·권한 변경 | 요청·결정 검수 → 기획·검수 → 개발·검수 → 독립 QA → Wiki·검수 → Master 기록 감사 → 병합 |

요청을 정리할 때 작업 유형(기능·버그·리팩터·성능·화면·기타)도 고른다. 유형마다 먼저 할 일과 맞았다는 증거가 정해져 있다(버그는 고치기 전 실패·고친 뒤 통과, 리팩터는 고정 테스트 먼저 등). 실행해 보면 답이 나오는 결정은 사용자에게 묻지 않고 실험으로 정한다.

Master가 등급을 추천하고 사용자가 확정한다. 코드를 먼저 조사한 뒤, 사용자가 정해야 할 결정과 실행 허락을 **한 번에 묻는다**. 승인 뒤에는 새 충돌 사실·반복 상한·허락 밖의 되돌릴 수 없는 행동일 때만 다시 묻는다.

끊겼으면 `/crossgate 이어서 진행해줘`라고 한다. Master가 `resume`으로 상태를 보고 요청서를 읽어 이어간다. 이미 답한 결정은 다시 묻지 않는다. `resume --list`로 최근 열린 실행을 찾을 수 있다. 실행 지정은 `--run <폴더>` → `CROSSGATE_RUN` → 현재 실행 순이다. 최근 활동이 있는 열린 실행이 둘 이상이면 쓰기 명령에 명시가 필요하다. 마치면 `close-run`으로 닫고, 다시 기록하려면 `close-run --reopen`을 쓴다.

### 작업 도구

아래 명령은 `python3 .crossgate/kit/bin/crossgate` 뒤에 붙인다. 세부 규칙은 [CROSSGATE.md](CROSSGATE.md)를 따른다.

| 명령 | 용도 |
|---|---|
| `discuss open <slug> --topic-file <파일> [--run <실행>]` | 주제 복사, 참가자·상한 스냅샷과 토의 폴더 생성 |
| `discuss round <slug> --prompt-file <파일> [--only 이름,이름] [--sequential] [--effort E] [--extend] [--run <실행>]` | 독립 발제 또는 반박. 실패 참가자는 같은 라운드 재호출 |
| `discuss issue <slug> --id T-n --status open\|agreed\|rejected\|deferred\|experiment\|user [--title 글] [--note 글] [--evidence 경로...] [--run <실행>]` | 쟁점 이력 추가와 장부 재생성. 새 ID는 제목 필수 |
| `discuss close <slug> --decision-file <파일> [--force] [--run <실행>]` | 결정 기록과 닫기. open·experiment가 남으면 force 필요 |
| `discuss status [slug] [--run <실행>]` | 토의 상태·라운드·쟁점 개수·참가자별 마지막 발언 |
| `probe --finding <ID> --cmd <명령> --expect <실패 표시> [--patch <파일>] [--baseline <커밋>] [--run <실행>]` | 검수의 실행 지적을 일회용 워크트리에서 재현. 개발 반려는 재현 기록(또는 `verdict --contrast`의 대조 지적)이 있어야 기록된다 |
| `retro [--days <수>] [--json]` | 실행 기록 회고: 단계별 검수·첫 통과·반려·조정 요청, 두 회차 연속 남은 지적, 재현 결과, 호출량. 파일 수정 없음 |
| `parallel --task <지시 파일> --task <지시 파일> --run <실행>` | 보통·무거움의 독립 개발을 동시에 처리. 합친 뒤 전체를 한 번 검수 |
| `docs-check [<문서 폴더>] [--require-index]` | 문서 목차·상대 링크·앵커 점검. 참조형 링크는 제외. 파일 수정 없음 |
| `audit-calls [--days <수>] [--save]` | 밖 Codex 호출 보고. 판정·병합을 막지 않음. 저장은 `raw/`에만 |
| `resume [--run <실행>]` | 단계·회차·미해결 지적·중단된 호출·다음 할 일 확인 |
| `codex <역할> --prompt-file <파일> [--fresh]` / `claude <역할> --prompt-file <파일> [--fresh]` | 역할 호출. 검수는 성공한 세션을 이어 쓰며 `--fresh`로 새로 시작 |

호출은 시작·끝과 강도 출처·세션·상태를 기록한다. 기본 시간 제한은 20분(개발·QA 40분), Codex 첫 작업 이벤트 대기는 180초다. 설정 `timeouts`(재현은 `probe`, 기본 10분), `parallel`(기본 최대 3), `docs`, `resume_reviews`(기본 true), `recent_minutes`(기본 60), `probe.commands`(재현에 허용할 명령 접두어, 없으면 gate 명령), `probe.sandbox`(macOS 샌드박스, 기본 `auto`), `probe.network`(재현에 네트워크 허용, 기본 false), `probe.env`·`probe.read_allow`·`probe.write_allow`(재현 명령에 더 넘길 환경 변수, 홈 아래 더 읽을 폴더, 더 쓸 폴더), `qa.recipe`(검증 레시피 경로, 형식은 `templates/verify.md`)로 조정한다. 옛 설정은 빠진 항목의 기본값으로 동작한다. 업그레이드 PR은 태그 출처·봉인·변경 범위·버전 조건과 필수 검사가 통과하면 자동 병합한다.

### 토의

설계 선택이 갈리거나 원인을 모르는 결함, 같은 지적의 검수 왕복, 사용자 요청에 쓰는 선택 단계다. Master가 `open → round(발제) → issue 등록 → round(반박) → 실험 → close`로 진행한다. 토의 중에는 코드를 바꾸지 않고 결과를 요청·기획·TODO에 반영한 뒤 기존 단계로 구현·검수한다. 사용자 결정은 한 번에 묻는다.

기본 참가자는 Codex 1·Claude 1이며 모두 읽기 전용이다. `discussion.participants`의 `name`·`engine`은 필수, `model`·`effort`는 선택이다. 모델·강도가 없으면 엔진 설정·등급 기본값을 쓴다(`round --effort`가 우선). 같은 엔진의 다른 모델도 가능하다. 이름은 영숫자·`-`·`_`만 허용하고 대소문자가 달라도 중복될 수 없다. 엔진은 `codex|claude`다. 설정이 없어도 기본값으로 동작한다.

라운드 1은 전원 동시·새 세션이다. 첫 호출에는 `--only`·`--sequential`을 쓰지 않는다. 실패한 참가자는 라운드 1에서도 `--only`로 재호출할 수 있다. 미완료 라운드는 빠진 참가자부터 다시 부르며 성공한 발언을 덮어쓰지 않는다. 이후 반박은 참가자별 마지막 성공 토의 세션을 이어 쓰고 상대 발언 원문·쟁점 장부 경로를 전달한다. `--sequential`이면 설정 순서대로 부르며 뒤 참가자는 같은 라운드 앞 발언도 받는다. 검수 세션과는 분리되며 토의 이어 쓰기는 `resume_reviews`·Codex `ephemeral` 설정과 무관하게 유지한다.

`discussion.rounds`는 발제를 제외한 반박 상한(기본 3, 즉 R2~R4)이며 `cap`과 별개다. 초과 호출은 `--extend`가 필요하다. 참가자와 상한은 열 때 고정한다. 상한을 명시 연장한 라운드의 실패 재호출에는 다시 `--extend`를 붙이지 않아도 된다. 열린 쟁점은 `open`·`experiment`를 세며 `user`는 닫을 때 질문 대상으로 알린다.

토의는 `<실행>/05-discussion/<NN_slug>/`에 주제·발언·쟁점 이력·장부·결정·메타 정보를 남긴다. slug는 경로 구분자와 공백을 `-`로 정리하며 실행 안에서 중복할 수 없다. `open`이 출력하는 경로는 설치 루트 기준 상대 경로이고, 이후 명령은 slug 또는 `NN_slug` 폴더 이름을 받는다. 시간 제한은 `timeouts.call_minutes.discussant`, 없으면 `default`를 따른다. 실패해도 다른 참가자의 결과를 저장하고, 일반 실패는 종료 코드 1, 시간 초과·멈춤뿐이면 3이다. `status`·`resume`은 열린 토의를 알리고 `merge-check`는 통과시키지 않는다(PM 모드는 표시만).

## 규칙 바꾸기

프로젝트의 `.crossgate/kit/`는 직접 고치지 않는다. 이 저장소에서 고친 뒤 `VERSION`과 `CHANGELOG.md`를 올리고 install.sh로 다시 설치한다. 규칙을 바꿀 때는 명세도 함께 개정한다. 태그를 단 뒤 발견한 결함은 태그를 옮기지 않고 0.8.1 같은 수정 버전으로 낸다.

## 검사

```bash
python3 tests/test_crossgate.py
```
