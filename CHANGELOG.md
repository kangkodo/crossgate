# 변경 이력

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
