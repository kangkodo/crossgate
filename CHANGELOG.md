# 변경 이력

## 0.2.2 (2026-09-27)
- 원본 위치(`kit_source`·`kit_remote`)를 커밋되는 `config.json`이 아니라 git이 무시하는 `.crossgate/local.json`에 기록. 0.2.1은 개인 경로와 비공개 원본 주소를 커밋 대상에 넣었다(공개 저장소면 노출). 설치기가 옛 값을 옮기고 `.gitignore`에 줄을 추가한다

## 0.2.1 (2026-09-27)
- 프로젝트 스킬에 `/crossgate 업그레이드` 절차 추가: 원본 최신 태그 확인 → 재설치 PR → 테스트 통과 시 병합 → 보고
- 전역 스킬 `/crossgate-setup`(`install.sh --global`): 설치되지 않은 폴더에서 적용 전 점검·설치·설정·CI까지. 원본 경로는 설치 때 기록하고 원본 저장소에는 남기지 않는다
- 설치 때 원본 위치를 프로젝트 `config.json`의 `kit_source`·`kit_remote`에 기록
- `codex` 호출이 세션 기록을 기본으로 남긴다(Codex 앱에서 사용 내역이 보이게). `config.codex.ephemeral: true`로 끌 수 있다

## 0.2.0 (2026-09-27) — 이름을 Crossgate로
이 시스템의 이름을 **Crossgate**(교차 검수 + 단계마다 관문)로 정했다. 옛 이름 `ai-pipeline-kit`과 일반적인 명령어 이름 `pipeline`이 다른 도구와 겹칠 위험을 없앤다.
- 명령 `pipeline` → `crossgate`, 스킬 `/pipeline` → `/crossgate`, 검수 에이전트 `pipeline-reviewer` → `crossgate-reviewer`
- 프로젝트 폴더 `.pipeline/` → `.crossgate/`, 규칙 문서 `PIPELINE.md` → `CROSSGATE.md`, CI 템플릿 `crossgate.yml`
- `install.sh`가 0.1.x 프로젝트를 자동 이전: 설정(보호 경로 포함)·현재 실행 보존, 옛 킷·스킬·에이전트·안내 단락 제거, `.gitignore`와 CI 워크플로의 경로 갱신
- 새 설치기는 새 구조의 태그(0.2.0 이상)만 설치한다. 0.1.x가 필요하면 그 태그의 `install.sh`를 쓴다
- 동작 규칙은 0.1.3과 같다

## 0.1.3 (2026-09-27)
파일럿 2회차(기존 프로젝트, 지도 표현 개선)와 원본·프로젝트 분리 점검에서 나온 수정.
- 설치: 기본으로 최신 태그에서 설치(`--ref`로 지정, `--dev`는 작업 트리). 설치된 복사본을 `MANIFEST.sha256`으로 봉인하고 `gate`·`merge-check`·`verify`가 검사
- `claude` 명령: Claude 검수를 읽기 도구(Read·Grep·Glob)만 켜고 MCP 없이 실행해 읽기 전용을 기계로 강제, 토큰·비용 기록
- `gate`: `usage.jsonl`에 줄이 덧붙기만 한 변경은 보호 경로 위반에서 제외
- `status`: 엔진별(codex·claude) 사용량 합계

## 0.1.2 (2026-09-26)
파일럿 1회차(기존 프로젝트, 프론트엔드 기능 추가)에서 나온 문제 수정.
- `gate`: 개발 역할의 `todo.md` 체크·완료 근거 변경을 보호 경로 위반에서 제외(문구 변경은 계속 위반)
- `codex`: `--json`으로 토큰 사용량을 받아 실행별 `usage.jsonl`에 기록, `status`에 합계 표시
- 스킬: 검수 서브에이전트가 등록되지 않은 세션의 대체 절차

## 0.1.1 (2026-09-26)
- AGENTS/CLAUDE 안내 단락: 기존 작업 절차와 겹칠 때 `/pipeline`으로 시작한 작업만 파이프라인 절차를 따른다고 명시

## 0.1.0 (2026-09-26)
- 최초 버전. 명세 `ai-crossreview-pipeline-spec` v1.5 기준.
- `bin/pipeline`: init-run, codex, gate, verdict, status, merge-check, deploy, clean-raw
- 역할 카드 8장, 템플릿, `/pipeline` 스킬, 읽기 전용 검수 서브에이전트, install.sh
