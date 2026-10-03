import json
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent


def sh(cmd, cwd, ok=True):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(f"{cmd}\n{r.stdout}\n{r.stderr}")
    return r


def P(args, cwd, ok=True):
    return sh(f"python3 .crossgate/kit/bin/crossgate {args}", cwd, ok)


def commit(cwd, msg):
    sh(f"git add -A && git commit -qm '{msg}'", cwd)


def fresh_repo(d):
    repo = Path(d)
    sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
    (repo / "a.js").write_text("1\n")
    commit(repo, "init")
    sh(f"sh {KIT}/install.sh --dev .", repo)
    cfg = json.loads((repo / ".crossgate/config.json").read_text())
    cfg.update(gate=[{"name": "ok", "run": "true"}])
    (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
    commit(repo, "kit")
    return repo


def fake_executable(directory, name, source):
    """실제 AI 대신 호출할 가짜 실행 파일을 만든다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path
