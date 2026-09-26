# 변경 이력

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
