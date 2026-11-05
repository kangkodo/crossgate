import json
import subprocess
import tempfile
from pathlib import Path
from helpers import KIT


DEFAULTS = {
    "recent_minutes": 60,
    "timeouts": {"call_minutes": {"default": 20, "developer": 40, "qa": 40, "probe": 10}, "stall_seconds": 180},
    "parallel": {"max": 3},
    "resume_reviews": True,
    "docs": {"root": "docs", "index": "INDEX.md", "exclude": []},
}
NOTICE = "설정에 없는 새 항목(기본값으로 동작): "


def install(repo, workspace=False):
    args = ["sh", str(KIT / "install.sh"), "--dev"]
    if workspace:
        args.append("--workspace")
    result = subprocess.run(args + [str(repo)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_installer_defaults_and_append():
    for workspace in (False, True):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            if not workspace:
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
                (repo / "AGENTS.md").write_text("# 기존 규칙\n", encoding="utf-8")
            out = install(repo, workspace)
            cfg = json.loads((repo / ".crossgate/config.json").read_text())
            for key, value in DEFAULTS.items():
                assert cfg[key] == value, key
            assert NOTICE not in out
            if workspace:
                assert all(not (repo / name).exists() for name in ("AGENTS.md", "CLAUDE.md", ".gitignore"))
            else:
                snippet = (KIT / "templates/agents-snippet.md").read_text()
                assert (repo / "AGENTS.md").read_text() == "# 기존 규칙\n\n" + snippet
                assert (repo / "CLAUDE.md").read_text() == snippet
                assert (repo / ".gitignore").read_text().splitlines().count(".crossgate/worktrees/") == 1


def test_installer_refresh_and_preserve():
    for workspace in (False, True):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            if not workspace:
                subprocess.run(["git", "init", "-q", str(repo)], check=True)
            template = "config.workspace.json" if workspace else "config.json"
            cfg = json.loads((KIT / "templates" / template).read_text())
            for key in DEFAULTS:
                cfg.pop(key, None)
            # 최상위 키만 비교한다. 중첩 값과 사용자 항목은 그대로 둔다.
            cfg["parallel"] = {"max": 1, "setup": "echo 준비"}
            cfg["docs"] = {"root": "문서"}
            cfg["custom"] = {"keep": [1, False]}
            cfg["kit_version"] = "이전 버전"
            mode_key = "repos" if workspace else "gate"
            cfg.pop(mode_key)
            (repo / ".crossgate").mkdir()
            path = repo / ".crossgate/config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            before = "# 앞 규칙\n\n<!-- crossgate:start -->\n옛 안내\n<!-- crossgate:end -->\n\n# 뒤 규칙\n"
            for name in ("AGENTS.md", "CLAUDE.md"):
                (repo / name).write_text(before, encoding="utf-8")
            ignore = "# crossgate\n.crossgate/local.json\n.obsidian/\nx crossgate/worktrees/\n"
            (repo / ".gitignore").write_text(ignore, encoding="utf-8")
            out = install(repo, workspace)
            missing = {"recent_minutes", "timeouts", "resume_reviews", mode_key}
            notices = [line[len(NOTICE):] for line in out.splitlines() if line.startswith(NOTICE)]
            assert len(notices) == 1 and set(notices[0].split(", ")) == missing, out
            cfg["kit_version"] = (KIT / "VERSION").read_text().strip()
            assert json.loads(path.read_text()) == cfg, "설정은 kit_version 외에 바꾸지 않는다"
            snippet = (KIT / "templates/agents-snippet.md").read_text().rstrip("\n")
            expected = before if workspace else "# 앞 규칙\n\n" + snippet + "\n\n# 뒤 규칙\n"
            for name in ("AGENTS.md", "CLAUDE.md"):
                assert (repo / name).read_text() == expected
            expected_ignore = ignore if workspace else ignore + ".crossgate/worktrees/\n"
            assert (repo / ".gitignore").read_text() == expected_ignore
            install(repo, workspace)
            assert (repo / ".gitignore").read_text() == expected_ignore
            for name in ("AGENTS.md", "CLAUDE.md"):
                assert (repo / name).read_text() == expected
