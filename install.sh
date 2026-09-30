#!/bin/sh
# 사용법: install.sh [--ref 태그 | --dev] <프로젝트 경로>
#         install.sh --workspace <작업공간 경로>   (git이 아닌 루트 + 저장소 여러 개, PM 모드)
#         install.sh --global   (전역 스킬 /crossgate-setup 을 ~/.claude/skills 에 설치)
# 킷을 프로젝트의 .crossgate/kit/ 에 설치하거나 업그레이드한다.
# 기본은 최신 태그에서 설치한다(원본의 작업 중 변경이 섞이지 않게). --dev 는 작업 트리를 그대로 설치한다(시험용).
# 설치 뒤 MANIFEST.sha256 으로 복사본을 봉인한다. 프로젝트 설정(.crossgate/config.json)은 없을 때만 만든다.
set -eu
KIT=$(cd "$(dirname "$0")" && pwd)
REF=""
DEV=0
WS=0
if [ "${1:-}" = "--global" ]; then
  G="$HOME/.claude/skills/crossgate-setup"
  mkdir -p "$G"
  sed "s|{{KIT}}|$KIT|g" "$KIT/claude/global/crossgate-setup/SKILL.md" > "$G/SKILL.md"
  echo "전역 스킬 설치: $G/SKILL.md (/crossgate-setup, 원본 $KIT)"
  exit 0
fi
while [ $# -gt 1 ]; do
  case "$1" in
    --ref) REF=$2; shift 2 ;;
    --dev) DEV=1; shift ;;
    --workspace) WS=1; shift ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done
DEST=$(cd "${1:?사용법: install.sh [--ref 태그 | --dev] <프로젝트 경로>}" && pwd)
[ "$WS" = 1 ] || git -C "$DEST" rev-parse --show-toplevel >/dev/null

SRC=$(mktemp -d)
trap 'rm -rf "$SRC"' EXIT
if [ "$DEV" = 1 ]; then
  cp -R "$KIT/CROSSGATE.md" "$KIT/VERSION" "$KIT/roles" "$KIT/templates" "$KIT/bin" "$KIT/claude" "$SRC/"
  SOURCE="dev (작업 트리)"
else
  [ -n "$REF" ] || REF=$(git -C "$KIT" describe --tags --abbrev=0)
  git -C "$KIT" archive "$REF" CROSSGATE.md VERSION roles templates bin claude | tar -x -C "$SRC"
  SOURCE="$REF ($(git -C "$KIT" rev-parse --short "$REF^{commit}"))"
fi
VERSION=$(cat "$SRC/VERSION")

# 0.1.x(옛 이름 ai-pipeline-kit)에서 올라오는 프로젝트: .pipeline → .crossgate 이전
if [ -d "$DEST/.pipeline" ]; then
  python3 - "$DEST" <<'PY'
import json, re, shutil, sys
from pathlib import Path
d = Path(sys.argv[1]); old, new = d / ".pipeline", d / ".crossgate"
new.mkdir(exist_ok=True)
if (old / "config.json").exists() and not (new / "config.json").exists():
    cfg = json.loads((old / "config.json").read_text(encoding="utf-8"))
    cfg["protected"] = [p.replace(".pipeline/", ".crossgate/") for p in cfg.get("protected", [])]
    (new / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if (old / "current-run").exists():
    shutil.move(str(old / "current-run"), str(new / "current-run"))
shutil.rmtree(old)
shutil.rmtree(d / ".claude/skills/pipeline", ignore_errors=True)
(d / ".claude/agents/pipeline-reviewer.md").unlink(missing_ok=True)
for name in ("AGENTS.md", "CLAUDE.md"):
    f = d / name
    if f.exists():
        t = re.sub(r"\n*<!-- ai-pipeline-kit:start -->.*?<!-- ai-pipeline-kit:end -->\n?", "\n", f.read_text(encoding="utf-8"), flags=re.S)
        t = t.replace("`pipeline deploy`", "`crossgate deploy`")
        f.write_text(t.rstrip("\n") + "\n", encoding="utf-8")
g = d / ".gitignore"
if g.exists():
    g.write_text(g.read_text(encoding="utf-8").replace("# ai-pipeline-kit", "# crossgate").replace(".pipeline/current-run", ".crossgate/current-run"), encoding="utf-8")
for wf in (d / ".github/workflows").glob("*.yml"):
    t = wf.read_text(encoding="utf-8")
    if ".pipeline/kit/bin/pipeline" in t:
        wf.write_text(t.replace(".pipeline/kit/bin/pipeline", ".crossgate/kit/bin/crossgate"), encoding="utf-8")
print("이전: .pipeline → .crossgate (설정·현재 실행·안내 단락·.gitignore·CI 경로)")
PY
fi

rm -rf "$DEST/.crossgate/kit"
mkdir -p "$DEST/.crossgate/kit"
cp -R "$SRC/CROSSGATE.md" "$SRC/VERSION" "$SRC/roles" "$SRC/templates" "$SRC/bin" "$DEST/.crossgate/kit/"
# 작업공간(PM 모드)은 절차를 작업공간 규칙이 맡는다. 엔진만 깔고 스킬·안내 단락·.gitignore·CI는 건드리지 않는다.
if [ "$WS" = 0 ]; then
  mkdir -p "$DEST/.claude/skills/crossgate" "$DEST/.claude/agents"
  cp "$SRC/claude/skills/crossgate/SKILL.md" "$DEST/.claude/skills/crossgate/SKILL.md"
  cp "$SRC/claude/agents/crossgate-reviewer.md" "$DEST/.claude/agents/crossgate-reviewer.md"
fi
# 봉인은 설치기가 직접 쓴다(옛 태그의 CLI에는 verify가 없을 수 있음). 형식은 bin/crossgate의 verify_kit과 같다.
python3 - "$DEST/.crossgate/kit" "$SOURCE" <<'PY'
import hashlib, sys
from pathlib import Path
base, source = Path(sys.argv[1]), sys.argv[2]
files = sorted(str(p.relative_to(base)) for p in base.rglob("*")
               if p.is_file() and p.name not in {"MANIFEST.sha256", ".DS_Store"} and "__pycache__" not in p.parts)
lines = [f"# source: {source}"] + [f"{hashlib.sha256((base / f).read_bytes()).hexdigest()}  {f}" for f in files]
(base / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

if [ ! -f "$DEST/.crossgate/config.json" ]; then
  if [ "$WS" = 1 ]; then
    cp "$SRC/templates/config.workspace.json" "$DEST/.crossgate/config.json"
    echo "만듦: .crossgate/config.json (PM 모드. repos 에 저장소별 gate·protected 를 적으세요)"
  else
    cp "$SRC/templates/config.json" "$DEST/.crossgate/config.json"
    echo "만듦: .crossgate/config.json (gate·forbidden·deploy를 프로젝트에 맞게 고치세요)"
  fi
fi
if [ "$WS" = 0 ]; then

for f in AGENTS.md CLAUDE.md; do
  if ! grep -q "crossgate:start" "$DEST/$f" 2>/dev/null; then
    { [ -f "$DEST/$f" ] && echo; cat "$SRC/templates/agents-snippet.md"; } >> "$DEST/$f"
    echo "추가: $f 에 파이프라인 안내"
  fi
done

touch "$DEST/.gitignore"
if ! grep -q "# crossgate" "$DEST/.gitignore"; then
  { echo; cat "$SRC/templates/gitignore-snippet"; } >> "$DEST/.gitignore"
  echo "추가: .gitignore 항목"
fi
# 이전 버전 설치본에는 local.json 줄이 없다
grep -q "^.crossgate/local.json$" "$DEST/.gitignore" || printf '.crossgate/local.json\n' >> "$DEST/.gitignore"
# 문서를 Obsidian으로 볼 때 생기는 설정 폴더가 승인 기록(커밋되지 않은 변경 검사)을 막지 않게
grep -q "^.obsidian/$" "$DEST/.gitignore" || printf '.obsidian/\n' >> "$DEST/.gitignore"

if [ ! -f "$DEST/.github/workflows/crossgate.yml" ]; then
  echo "참고: CI 검사는 templates/crossgate.yml 을 .github/workflows/ 에 복사해서 켭니다(대상 브랜치 확인 후)."
fi
fi
# 이 컴퓨터에서 원본이 어디 있는지 기록한다(업그레이드 때 씀). 컴퓨터마다 다르고 개인 경로가 들어가므로
# git이 무시하는 .crossgate/local.json 에만 둔다. 0.2.1이 config.json에 넣은 값은 여기로 옮긴다.
# config의 kit_version 은 설치한 버전으로 맞춘다(템플릿 값이나 업그레이드 전 값이 남지 않게).
python3 - "$DEST/.crossgate" "$KIT" "$(git -C "$KIT" remote get-url origin 2>/dev/null || true)" "$VERSION" <<'PY'
import json, sys
from pathlib import Path
base, kit, remote, version = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
cfg = base / "config.json"
c = json.loads(cfg.read_text(encoding="utf-8"))
c["kit_version"] = version
c.pop("kit_source", None)
c.pop("kit_remote", None)
cfg.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
local = {"kit_source": kit}
if remote:
    local["kit_remote"] = remote
(base / "local.json").write_text(json.dumps(local, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
[ "$WS" = 1 ] && echo "참고: PM 모드에는 스킬이 없습니다. 작업공간 규칙(AGENTS.md 등)에서 .crossgate/kit/CROSSGATE.md 를 가리켜야 Master가 요청 정리·재검수·읽을 것 규칙을 읽습니다."
echo "설치 완료: crossgate $VERSION [$SOURCE] → $DEST (봉인: .crossgate/kit/MANIFEST.sha256)"
