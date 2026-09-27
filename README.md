# Crossgate

**Crossgate**는 AI 교차검수 개발 파이프라인의 실행 키트다. 이름은 cross(서로 다른 모델의 교차 검수)와 gate(단계마다 승인받아야 통과하는 관문)에서 왔다. 설계 원본은 `../crossgate-spec`(명세 v1.9)이다.

- 사용자는 Master(Claude)하고만 대화한다. Master가 Codex(`gpt-6-astra`)를 역할별로 호출한다.
- 기본 배치: 기획·Wiki는 Claude가 쓰고 Codex가 검수한다. 개발은 Codex가 하고 Claude가 검수한다. QA는 Codex가 한다.
- 검수 역할은 항상 읽기 전용으로 실행된다. Codex는 `-s read-only`, Claude는 Read·Grep·Glob만 허용된다.
- 모든 판정은 `ai-log/<날짜>/<실행>/verdicts.jsonl`에 남는다. 조건을 만족하면 자동 병합하고, 배포는 사용자 지시가 있을 때만 한다.

## 구성

| 경로 | 내용 |
|---|---|
| `CROSSGATE.md` | 실행용 규칙 (판정, 루브릭, 응답 형식, 상한, 병합·배포) |
| `roles/` | 역할 카드 8장 |
| `templates/` | request, todo, 프로젝트 설정(`config.json`), CI 검사, AGENTS/CLAUDE 안내, .gitignore 항목 |
| `bin/crossgate` | CLI: `init-run`, `codex`, `gate`, `verdict`, `status`, `merge-check`, `deploy`, `clean-raw` |
| `claude/` | `/crossgate` 스킬(Master 플레이북), `crossgate-reviewer` 서브에이전트 |
| `install.sh` | 프로젝트에 설치·업그레이드 |

## 설치

```bash
sh ~/Documents/projects/crossgate/install.sh <프로젝트 경로>
```

설치하면 `.crossgate/kit/`(킷 사본, 덮어씀), `.crossgate/config.json`(없을 때만 생성), `.claude/skills/crossgate/`, `.claude/agents/crossgate-reviewer.md`가 생긴다. AGENTS.md·CLAUDE.md에는 안내 한 단락, .gitignore에는 항목이 추가된다.
그다음 `config.json`의 `gate`, `forbidden`, `deploy`를 프로젝트에 맞게 고친다. CI 검사를 켜려면 `templates/crossgate.yml`을 복사한다.

## 사용

Claude Code에서 `/crossgate <요청>`을 입력한다. 간단 수정은 "간단 수정으로"라고 명시한다.

## 규칙 바꾸기

프로젝트의 `.crossgate/kit/`는 직접 고치지 않는다. 이 저장소에서 고친 뒤 `VERSION`과 `CHANGELOG.md`를 올리고 install.sh로 다시 설치한다. 규칙을 바꿀 때는 명세도 함께 개정한다.

## 검사

```bash
python3 tests/test_crossgate.py
```
