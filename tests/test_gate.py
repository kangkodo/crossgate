import json
import tempfile
from pathlib import Path
from helpers import KIT, P, commit, fresh_repo, sh


def test_gate_todo_checks():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
        (repo / "a.js").write_text("1\n")
        commit(repo, "init")
        sh(f"sh {KIT}/install.sh --dev .", repo)
        cfg = json.loads((repo / ".crossgate/config.json").read_text())
        cfg.update(gate=[{"name": "ok", "run": "true"}])
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
        run = P("init-run t", repo).stdout.strip()
        todo = repo / run / "01-planning/todo.md"
        todo.write_text("- [ ] DEV-001 한다\n  확인 방법: AC-1\n- [ ] DEV-002 또 한다\n")
        commit(repo, "plan")
        base = sh("git rev-parse HEAD", repo).stdout.strip()
        # 체크와 완료 근거만: 허용
        todo.write_text("- [x] DEV-001 한다\n  완료 근거: a.js · 무엇\n  확인 방법: AC-1\n- [ ] DEV-002 또 한다\n")
        assert "gate 통과" in P(f"gate --protect-since {base}", repo).stdout
        # 문구 변경: 위반
        todo.write_text("- [x] DEV-001 다르게 한다\n  확인 방법: AC-1\n- [ ] DEV-002 또 한다\n")
        assert P(f"gate --protect-since {base}", repo, ok=False).returncode
        # 다른 ai-log 파일: 위반
        todo.write_text("- [x] DEV-001 한다\n  완료 근거: a.js · 무엇\n  확인 방법: AC-1\n- [ ] DEV-002 또 한다\n")
        (repo / run / "verdicts.jsonl").write_text("{}\n")
        assert P(f"gate --protect-since {base}", repo, ok=False).returncode


def test_usage_append_only():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = P("init-run u", repo).stdout.strip()
        usage = repo / run / "usage.jsonl"
        usage.write_text('{"a": 1}\n')
        commit(repo, "usage")
        base = sh("git rev-parse HEAD", repo).stdout.strip()
        usage.write_text('{"a": 1}\n{"b": 2}\n')
        assert "gate 통과" in P(f"gate --protect-since {base}", repo).stdout
        usage.write_text('{"a": 9}\n{"b": 2}\n')
        assert P(f"gate --protect-since {base}", repo, ok=False).returncode

