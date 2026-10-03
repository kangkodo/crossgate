import json
import tempfile
from pathlib import Path
from helpers import KIT, P, commit, fake_executable, sh


def test_workspace_pm_mode():
    """git이 아닌 작업공간 루트 + 저장소 2개. 개발 쓰기는 고른 저장소로 제한되고, 판정은 저장소 커밋에 묶인다."""
    with tempfile.TemporaryDirectory() as d:
        ws = (Path(d) / "ws").resolve()
        for name in ("api", "web"):
            r = ws / name
            r.mkdir(parents=True)
            sh("git init -q -b main && git config user.email t@t && git config user.name t", r)
            (r / "a.js").write_text("1\n")
            commit(r, "init")
        (ws / "AGENTS.md").write_text("기존 규칙\n")
        sh(f"sh {KIT}/install.sh --dev --workspace .", ws)
        assert (ws / "AGENTS.md").read_text() == "기존 규칙\n", "작업공간 규칙은 건드리지 않는다"
        assert (ws / ".claude/skills/crossgate/SKILL.md").exists(), "PM 모드에도 /crossgate 스킬을 깐다"
        assert not (ws / ".gitignore").exists()
        assert (ws / ".crossgate/local.json").exists()
        cfg = json.loads((ws / ".crossgate/config.json").read_text())
        assert cfg["mode"] == "pm"
        cfg["repos"] = {"api": {"gate": [{"name": "ok", "run": "test -f a.js"}], "protected": ["tests/acceptance/*"]},
                        "web": {"gate": [{"name": "no", "run": "false"}]}}
        (ws / ".crossgate/config.json").write_text(json.dumps(cfg))

        # 하위 저장소 안에서 실행해도 작업공간 루트를 쓴다
        run = sh(f"python3 {ws}/.crossgate/kit/bin/crossgate init-run pm", ws / "api").stdout.strip()
        assert (ws / run / "meta.json").exists() and json.loads((ws / run / "meta.json").read_text())["branch"] is None

        P("gate --repo api", ws)
        assert P("gate --repo web", ws, ok=False).returncode
        assert P("gate", ws, ok=False).returncode, "PM 모드 gate는 저장소를 골라야 한다"
        assert "config.repos" in P("gate --repo nope", ws, ok=False).stderr

        P("verdict --stage plan --verdict APPROVED --actor codex", ws)
        (ws / "api/a.js").write_text("2\n")
        assert P("verdict --stage dev --verdict APPROVED --repo api", ws, ok=False).returncode, "커밋 안 된 변경"
        commit(ws / "api", "dev")
        head = sh("git rev-parse HEAD", ws / "api").stdout.strip()
        P("verdict --stage dev --verdict APPROVED --repo api", ws)
        recs = [json.loads(x) for x in (ws / run / "verdicts.jsonl").read_text().splitlines()]
        assert recs[0]["commit"] is None and recs[1]["commit"] == head and recs[1]["repo"] == "api"
        assert f"api@{head[:7]}" in (ws / run / "timeline.md").read_text()
        assert f"api@{head[:7]}" in P("status", ws).stdout
        assert "PM 모드" in P("merge-check --base main", ws, ok=False).stderr

        fake = Path(d) / "fakebin"
        fake.mkdir()
        log = Path(d) / "args.log"
        fake_executable(fake, "codex", f"""#!/bin/sh
echo "$@" >> {log}
cat > {Path(d) / 'prompt.txt'}
echo '{{"type":"turn.completed","usage":{{"input_tokens":5,"output_tokens":1}}}}'
""")
        (ws / "q.md").write_text("구현")
        env = f"PATH={fake}:$PATH "
        assert sh(env + "python3 .crossgate/kit/bin/crossgate codex developer --prompt-file q.md", ws, ok=False).returncode
        sh(env + "python3 .crossgate/kit/bin/crossgate codex developer --repo web --prompt-file q.md", ws)
        last = log.read_text().splitlines()[-1]
        assert "-s workspace-write" in last and f"-C {ws / 'web'}" in last, last
        assert "작업 저장소: `web`" in (Path(d) / "prompt.txt").read_text()
        out = sh(env + "python3 .crossgate/kit/bin/crossgate codex plan-reviewer --prompt-file q.md", ws)
        last = log.read_text().splitlines()[-1]
        assert "-s read-only" in last and f"-C {ws}" in last, last
        assert "요청 단계 승인이 없다" in out.stderr, "요청 승인 없이 기획 검수를 부르면 알린다"
        sh(env + "python3 .crossgate/kit/bin/crossgate codex request-reviewer --prompt-file q.md", ws)
        assert "-s read-only" in log.read_text().splitlines()[-1], "요청 검수는 읽기 전용"
        P("verdict --stage request --verdict APPROVED --actor user", ws)
        assert "요청 단계 승인이 없다" not in sh(env + "python3 .crossgate/kit/bin/crossgate codex plan-reviewer --prompt-file q.md", ws).stderr

