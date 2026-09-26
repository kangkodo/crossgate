#!/bin/sh
# 사용법: install.sh <프로젝트 경로>
# 킷을 프로젝트의 .pipeline/kit/ 에 설치하거나 업그레이드한다. 프로젝트 설정(.pipeline/config.json)은 없을 때만 만든다.
set -eu
KIT=$(cd "$(dirname "$0")" && pwd)
DEST=$(cd "${1:?사용법: install.sh <프로젝트 경로>}" && pwd)
VERSION=$(cat "$KIT/VERSION")
git -C "$DEST" rev-parse --show-toplevel >/dev/null

rm -rf "$DEST/.pipeline/kit"
mkdir -p "$DEST/.pipeline/kit" "$DEST/.claude/skills/pipeline" "$DEST/.claude/agents"
cp -R "$KIT/PIPELINE.md" "$KIT/VERSION" "$KIT/roles" "$KIT/templates" "$KIT/bin" "$DEST/.pipeline/kit/"
cp "$KIT/claude/skills/pipeline/SKILL.md" "$DEST/.claude/skills/pipeline/SKILL.md"
cp "$KIT/claude/agents/pipeline-reviewer.md" "$DEST/.claude/agents/pipeline-reviewer.md"

if [ ! -f "$DEST/.pipeline/config.json" ]; then
  cp "$KIT/templates/config.json" "$DEST/.pipeline/config.json"
  echo "만듦: .pipeline/config.json (gate·forbidden·deploy를 프로젝트에 맞게 고치세요)"
fi

for f in AGENTS.md CLAUDE.md; do
  if ! grep -q "ai-pipeline-kit:start" "$DEST/$f" 2>/dev/null; then
    { [ -f "$DEST/$f" ] && echo; cat "$KIT/templates/agents-snippet.md"; } >> "$DEST/$f"
    echo "추가: $f 에 파이프라인 안내"
  fi
done

touch "$DEST/.gitignore"
if ! grep -q "# ai-pipeline-kit" "$DEST/.gitignore"; then
  { echo; cat "$KIT/templates/gitignore-snippet"; } >> "$DEST/.gitignore"
  echo "추가: .gitignore 항목"
fi

if [ ! -f "$DEST/.github/workflows/pipeline-check.yml" ]; then
  echo "참고: CI 검사는 templates/pipeline-check.yml 을 .github/workflows/ 에 복사해서 켭니다(대상 브랜치 확인 후)."
fi
echo "설치 완료: ai-pipeline-kit $VERSION → $DEST"
