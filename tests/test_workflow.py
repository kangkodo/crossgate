import json
import tempfile
from pathlib import Path
from helpers import KIT, P, commit, sh


def test_flow():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
        (repo / "app.js").write_text("console.log(1)\n")
        commit(repo, "init")
        sh(f"sh {KIT}/install.sh --dev .", repo)
        cfg = json.loads((repo / ".crossgate/config.json").read_text())
        cfg.update(gate=[{"name": "ok", "run": "true"}], forbidden=["wrangler.toml"])
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
        commit(repo, "install kit")
        assert "crossgate:start" in (repo / "CLAUDE.md").read_text()

        sh("git switch -qc pipe/demo", repo)
        run = P("init-run '데모 작업'", repo).stdout.strip()
        assert (repo / run / "00-request/request.md").exists()
        commit(repo, "run")

        # 개발 AI가 보호 경로를 건드리면 gate가 막는다
        base = sh("git rev-parse HEAD", repo).stdout.strip()
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg) + " ")
        assert P(f"gate --protect-since {base}", repo, ok=False).returncode
        sh("git checkout -- .crossgate/config.json", repo)
        assert "gate 통과" in P(f"gate --protect-since {base}", repo).stdout

        (repo / "app.js").write_text("console.log(2)\n")
        # 커밋 전 승인은 거부된다
        assert P("verdict --stage dev --verdict APPROVED", repo, ok=False).returncode
        commit(repo, "dev")
        # 미검증이 있으면 승인 불가
        assert P("verdict --stage dev --verdict APPROVED --unverified 부작용", repo, ok=False).returncode
        for st, v in [("request", "APPROVED"), ("plan", "APPROVED"), ("dev", "APPROVED"),
                      ("qa", "PASSED"), ("wiki", "APPROVED")]:
            P(f"verdict --stage {st} --verdict {v}", repo)
        assert "승인" in (repo / run / "timeline.md").read_text()
        commit(repo, "log")
        assert "✓" in P("merge-check --base main", repo).stdout

        # 문서만 바뀌면 개발·QA 승인은 유지, Wiki 승인은 깨진다
        (repo / "README.md").write_text("doc\n")
        commit(repo, "doc")
        out = P("merge-check --base main", repo, ok=False).stdout
        assert "wiki 승인 뒤 바뀐 파일" in out and "dev 승인 뒤" not in out
        sh("git reset -q --hard HEAD~1", repo)

        # 승인 뒤 코드가 바뀌면 자동 병합 불가
        (repo / "app.js").write_text("console.log(3)\n")
        commit(repo, "late change")
        assert "dev 승인 뒤 바뀐 파일" in P("merge-check --base main", repo, ok=False).stdout
        sh("git reset -q --hard HEAD~1", repo)

        # 금지 경로
        (repo / "wrangler.toml").write_text("x\n")
        commit(repo, "forbidden")
        assert "금지 경로" in P("merge-check --base main", repo, ok=False).stdout
        sh("git reset -q --hard HEAD~1", repo)

        # 같은 지적이 3회 연속 남으면 조정 필요, ESCALATED가 있으면 자동 병합 불가
        P("verdict --stage wiki --verdict REJECTED --issues W-1=open", repo)
        P("verdict --stage wiki --verdict REJECTED --issues W-1=open", repo)
        out = P("verdict --stage wiki --verdict REJECTED --issues W-1=open,W-2=open", repo).stdout
        assert "조정 필요" in out and "W-1" in out, out
        assert "⚠" in P("status", repo).stdout
        P("verdict --stage wiki --verdict ESCALATED --note '사용자에게 질문'", repo)
        P("verdict --stage wiki --verdict APPROVED", repo)
        commit(repo, "escalated")
        assert "ESCALATED" in P("merge-check --base main", repo, ok=False).stdout

