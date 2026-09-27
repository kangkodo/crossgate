#!/bin/sh
# 사용법: install.sh [--ref 태그 | --dev] <프로젝트 경로>
# 킷을 프로젝트의 .pipeline/kit/ 에 설치하거나 업그레이드한다.
# 기본은 최신 태그에서 설치한다(원본의 작업 중 변경이 섞이지 않게). --dev 는 작업 트리를 그대로 설치한다(시험용).
# 설치 뒤 MANIFEST.sha256 으로 복사본을 봉인한다. 프로젝트 설정(.pipeline/config.json)은 없을 때만 만든다.
set -eu
KIT=$(cd "$(dirname "$0")" && pwd)
REF=""
DEV=0
while [ $# -gt 1 ]; do
  case "$1" in
    --ref) REF=$2; shift 2 ;;
    --dev) DEV=1; shift ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done
DEST=$(cd "${1:?사용법: install.sh [--ref 태그 | --dev] <프로젝트 경로>}" && pwd)
git -C "$DEST" rev-parse --show-toplevel >/dev/null

SRC=$(mktemp -d)
trap 'rm -rf "$SRC"' EXIT
if [ "$DEV" = 1 ]; then
  cp -R "$KIT/PIPELINE.md" "$KIT/VERSION" "$KIT/roles" "$KIT/templates" "$KIT/bin" "$KIT/claude" "$SRC/"
  SOURCE="dev (작업 트리)"
else
  [ -n "$REF" ] || REF=$(git -C "$KIT" describe --tags --abbrev=0)
  git -C "$KIT" archive "$REF" PIPELINE.md VERSION roles templates bin claude | tar -x -C "$SRC"
  SOURCE="$REF ($(git -C "$KIT" rev-parse --short "$REF^{commit}"))"
fi
VERSION=$(cat "$SRC/VERSION")

rm -rf "$DEST/.pipeline/kit"
mkdir -p "$DEST/.pipeline/kit" "$DEST/.claude/skills/pipeline" "$DEST/.claude/agents"
cp -R "$SRC/PIPELINE.md" "$SRC/VERSION" "$SRC/roles" "$SRC/templates" "$SRC/bin" "$DEST/.pipeline/kit/"
cp "$SRC/claude/skills/pipeline/SKILL.md" "$DEST/.claude/skills/pipeline/SKILL.md"
cp "$SRC/claude/agents/pipeline-reviewer.md" "$DEST/.claude/agents/pipeline-reviewer.md"
# 봉인은 설치기가 직접 쓴다(옛 태그의 CLI에는 verify가 없을 수 있음). 형식은 bin/pipeline의 verify_kit과 같다.
python3 - "$DEST/.pipeline/kit" "$SOURCE" <<'PY'
import hashlib, sys
from pathlib import Path
base, source = Path(sys.argv[1]), sys.argv[2]
files = sorted(str(p.relative_to(base)) for p in base.rglob("*")
               if p.is_file() and p.name not in {"MANIFEST.sha256", ".DS_Store"} and "__pycache__" not in p.parts)
lines = [f"# source: {source}"] + [f"{hashlib.sha256((base / f).read_bytes()).hexdigest()}  {f}" for f in files]
(base / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

if [ ! -f "$DEST/.pipeline/config.json" ]; then
  cp "$SRC/templates/config.json" "$DEST/.pipeline/config.json"
  echo "만듦: .pipeline/config.json (gate·forbidden·deploy를 프로젝트에 맞게 고치세요)"
fi

for f in AGENTS.md CLAUDE.md; do
  if ! grep -q "ai-pipeline-kit:start" "$DEST/$f" 2>/dev/null; then
    { [ -f "$DEST/$f" ] && echo; cat "$SRC/templates/agents-snippet.md"; } >> "$DEST/$f"
    echo "추가: $f 에 파이프라인 안내"
  fi
done

touch "$DEST/.gitignore"
if ! grep -q "# ai-pipeline-kit" "$DEST/.gitignore"; then
  { echo; cat "$SRC/templates/gitignore-snippet"; } >> "$DEST/.gitignore"
  echo "추가: .gitignore 항목"
fi

if [ ! -f "$DEST/.github/workflows/pipeline-check.yml" ]; then
  echo "참고: CI 검사는 templates/pipeline-check.yml 을 .github/workflows/ 에 복사해서 켭니다(대상 브랜치 확인 후)."
fi
echo "설치 완료: ai-pipeline-kit $VERSION [$SOURCE] → $DEST (봉인: .pipeline/kit/MANIFEST.sha256)"
