#!/usr/bin/env python3
"""킷 자체 검사. 임시 git 저장소에 설치해서 판정 기록·병합 조건·조정 감지·마스킹을 확인한다.

  python3 tests/test_pipeline.py
"""
import importlib.machinery
import json
import subprocess
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent


def sh(cmd, cwd, ok=True):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
    if ok and r.returncode:
        raise AssertionError(f"{cmd}\n{r.stdout}\n{r.stderr}")
    return r


def P(args, cwd, ok=True):
    return sh(f"python3 .pipeline/kit/bin/pipeline {args}", cwd, ok)


def commit(cwd, msg):
    sh(f"git add -A && git commit -qm '{msg}'", cwd)


def test_mask():
    mod = importlib.machinery.SourceFileLoader("pipeline", str(KIT / "bin" / "pipeline")).load_module()
    assert mod.mask("token=abcdef123456") == "token=***"
    assert mod.mask('"api_key": "zzzzzzzz"') == '"api_key": "***"'
    assert mod.mask("Authorization: Bearer abcdefghij") == "Authorization: Bearer ***"
    assert mod.mask("ghp_" + "a" * 30) == "***"
    assert mod.mask("평범한 문장 token 없음") == "평범한 문장 token 없음"


def test_flow():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
        (repo / "app.js").write_text("console.log(1)\n")
        commit(repo, "init")
        sh(f"sh {KIT}/install.sh --dev .", repo)
        cfg = json.loads((repo / ".pipeline/config.json").read_text())
        cfg.update(gate=[{"name": "ok", "run": "true"}], forbidden=["wrangler.toml"])
        (repo / ".pipeline/config.json").write_text(json.dumps(cfg))
        commit(repo, "install kit")
        assert "ai-pipeline-kit:start" in (repo / "CLAUDE.md").read_text()

        sh("git switch -qc pipe/demo", repo)
        run = P("init-run '데모 작업'", repo).stdout.strip()
        assert (repo / run / "00-request/request.md").exists()
        commit(repo, "run")

        # 개발 AI가 보호 경로를 건드리면 gate가 막는다
        base = sh("git rev-parse HEAD", repo).stdout.strip()
        (repo / ".pipeline/config.json").write_text(json.dumps(cfg) + " ")
        assert P(f"gate --protect-since {base}", repo, ok=False).returncode
        sh("git checkout -- .pipeline/config.json", repo)
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


def test_usage():
    mod = importlib.machinery.SourceFileLoader("pipeline", str(KIT / "bin" / "pipeline")).load_module()
    out = "\n".join([
        '{"type":"thread.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":7,"reasoning_output_tokens":3}}',
        "not json",
        '{"type":"turn.completed","usage":{"input_tokens":20,"output_tokens":1}}',
    ])
    assert mod.sum_usage(out) == {"input_tokens": 120, "cached_input_tokens": 40, "output_tokens": 8, "reasoning_output_tokens": 3}
    assert mod.sum_usage("") == dict.fromkeys(mod.USAGE_KEYS, 0)


def test_gate_todo_checks():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
        (repo / "a.js").write_text("1\n")
        commit(repo, "init")
        sh(f"sh {KIT}/install.sh --dev .", repo)
        cfg = json.loads((repo / ".pipeline/config.json").read_text())
        cfg.update(gate=[{"name": "ok", "run": "true"}])
        (repo / ".pipeline/config.json").write_text(json.dumps(cfg))
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


def fresh_repo(d):
    repo = Path(d)
    sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
    (repo / "a.js").write_text("1\n")
    commit(repo, "init")
    sh(f"sh {KIT}/install.sh --dev .", repo)
    cfg = json.loads((repo / ".pipeline/config.json").read_text())
    cfg.update(gate=[{"name": "ok", "run": "true"}])
    (repo / ".pipeline/config.json").write_text(json.dumps(cfg))
    commit(repo, "kit")
    return repo


def test_seal_and_tag_install():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        assert "봉인 유지" in P("verify", repo).stdout
        assert "# source: dev" in (repo / ".pipeline/kit/MANIFEST.sha256").read_text()
        card = repo / ".pipeline/kit/roles/qa.md"
        card.write_text(card.read_text() + "\n몰래 고침\n")
        out = P("verify", repo, ok=False)
        assert out.returncode and "변경됨: roles/qa.md" in out.stderr, out.stderr
        assert "킷 복사본이 원본과 다릅니다" in P("gate", repo, ok=False).stderr
        (repo / ".pipeline/kit/roles/extra.md").write_text("x")
        assert "추가됨: roles/extra.md" in P("verify", repo, ok=False).stderr
        # 태그에서 설치하면 그 태그의 내용과 출처가 봉인된다
        sh(f"sh {KIT}/install.sh --ref v0.1.2 .", repo)
        assert (repo / ".pipeline/kit/VERSION").read_text().strip() == "0.1.2"
        assert "# source: v0.1.2" in (repo / ".pipeline/kit/MANIFEST.sha256").read_text()
        assert not (repo / ".pipeline/kit/roles/extra.md").exists()


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


def test_reviewers_are_forced_read_only():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = P("init-run r", repo).stdout.strip()
        fake = Path(d) / "fakebin"
        fake.mkdir()
        log = Path(d) / "args.log"
        (fake / "claude").write_text(f"""#!/bin/sh
echo "$@" >> {log}
cat >/dev/null
echo '{{"result":"판정: 승인","total_cost_usd":0.5,"usage":{{"input_tokens":3,"cache_read_input_tokens":7,"output_tokens":2}}}}'
""")
        (fake / "codex").write_text(f"""#!/bin/sh
echo "$@" >> {log}
cat >/dev/null
echo '{{"type":"turn.completed","usage":{{"input_tokens":5,"output_tokens":1}}}}'
""")
        for f in fake.iterdir():
            f.chmod(0o755)
        (repo / "q.md").write_text("검수")
        env = f"PATH={fake}:$PATH "
        sh(env + "python3 .pipeline/kit/bin/pipeline claude dev-reviewer --prompt-file q.md", repo)
        args = log.read_text()
        for flag in ("--tools Read,Grep,Glob", "--permission-mode dontAsk", "--strict-mcp-config"):
            assert flag in args, (flag, args)
        assert sh(env + "python3 .pipeline/kit/bin/pipeline claude developer --prompt-file q.md", repo, ok=False).returncode
        sh(env + "python3 .pipeline/kit/bin/pipeline codex plan-reviewer --prompt-file q.md", repo)
        assert "-s read-only" in log.read_text().splitlines()[-1]
        recs = [json.loads(line) for line in (repo / run / "usage.jsonl").read_text().splitlines()]
        assert [r["engine"] for r in recs] == ["claude", "codex"]
        assert recs[0]["input_tokens"] == 10 and recs[0]["cost_usd"] == 0.5
        assert "claude 호출 1회" in P("status", repo).stdout


if __name__ == "__main__":
    test_mask()
    test_usage()
    test_gate_todo_checks()
    test_seal_and_tag_install()
    test_usage_append_only()
    test_reviewers_are_forced_read_only()
    test_flow()
    print("ok: pipeline kit checks passed")
