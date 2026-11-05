"""0.10: 실행으로 증명한 차단 지적(probe·verdict R15), 전제 확인, 감사 단계, 회고 명령."""
import json
import tempfile
from pathlib import Path
from helpers import P, commit, fresh_repo, sh

CHECK = 'test "$(cat value.txt)" = ok || { echo "BUG: value is $(cat value.txt)"; exit 1; }\n'


def setup(d):
    repo = fresh_repo(d)
    cfg = json.loads((repo / ".crossgate/config.json").read_text())
    cfg["probe"] = {"commands": ["sh check.sh"]}
    (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
    (repo / "check.sh").write_text(CHECK)
    (repo / "value.txt").write_text("ok\n")
    commit(repo, "check")
    run = P("init-run 증거 --route standard", repo).stdout.strip()
    commit(repo, "run")
    return repo, repo / run


def probes(run):
    f = run / "probes.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_allowed_prefixes_reject_shell_tricks():
    from cg.commands.probe import allowed
    ok = ["npm test", "cd web && pnpm vitest run"]
    assert allowed(ok, "npm test")
    assert allowed(ok, 'cd web && pnpm vitest run tests/a.test.ts -t "returns unknown"')
    for bad in ("npm test; rm -rf ~", "npm test && curl x", "npm test $(id)", "npm test `id`", "npm test | sh",
                "npm test > out", "npm testx", "rm -rf /", "npm test\nrm x", "npm test (x)", "npm test /etc/passwd",
                "npm test ~/x", "npm test ../x", "npm test a/../../b", "npm test --config=/tmp/e.js",
                "npm test -c/etc/passwd", "npm test x=/etc/p", 'npm test "/x"', 'npm test "unclosed'):
        assert not allowed(ok, bad), bad


def test_probe_reproduces_and_verdict_requires_proof():
    with tempfile.TemporaryDirectory() as d:
        repo, run = setup(d)
        (repo / "value.txt").write_text("bug\n")
        commit(repo, "개발: 버그가 들어간 커밋")
        # 재현 기록이 없는 실행 지적은 기록되지 않는다
        r = P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open", repo, ok=False)
        assert r.returncode and "R15" in r.stderr and not (run / "verdicts.jsonl").exists()
        # 허용되지 않은 명령은 실행하지 않고 기록도 남기지 않는다
        r = P('probe --finding D-1 --cmd "sh check.sh; echo hi" --expect BUG', repo, ok=False)
        assert r.returncode and "허용되지 않은" in r.stderr and not probes(run)
        # 실패 표시가 없는 실패는 재현이 아니다
        r = P('probe --finding D-1 --cmd "sh check.sh" --expect "없는 표시"', repo, ok=False)
        assert r.returncode == 1 and "재현 안 됨" in r.stdout and "실패 표시가 없습니다" in r.stdout
        r = P('probe --finding D-1 --cmd "sh check.sh" --expect BUG', repo)
        assert "재현됨" in r.stdout
        rec = probes(run)[-1]
        assert rec["reproduced"] and rec["finding"] == "D-1" and rec["exit"] == 1
        assert rec["commit"] == sh("git rev-parse HEAD", repo).stdout.strip()
        assert "BUG: value is bug" in (repo / rec["log"]).read_text()
        # 본 작업 트리와 워크트리 목록은 그대로다
        assert sh("git worktree list", repo).stdout.strip().count("\n") == 0
        assert (repo / "value.txt").read_text() == "bug\n"
        # D-2는 대조 지적으로 밝혀야 함께 기록된다
        r = P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open,D-2=open", repo, ok=False)
        assert r.returncode and "D-2" in r.stderr
        P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open,D-2=open,D-3=resolved --contrast D-2", repo)
        v = [json.loads(x) for x in (run / "verdicts.jsonl").read_text().splitlines()][-1]
        assert v["proofs"] == {"D-1": rec["id"]} and v["contrast"] == ["D-2"]
        # 커밋이 바뀌면 옛 재현은 증거가 아니다
        (repo / "other.txt").write_text("x\n")
        commit(repo, "다른 수정")
        r = P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open --contrast D-2", repo, ok=False)
        assert r.returncode and "D-1" in r.stderr
        # 주체를 빼도 검사한다. 사용자 판정과 다른 단계는 검사하지 않는다
        assert P("verdict --stage dev --verdict REJECTED --issues D-9=open", repo, ok=False).returncode
        P("verdict --stage dev --verdict REJECTED --actor user --issues D-9=open", repo)
        P("verdict --stage request --verdict REJECTED --actor codex --issues I-1=open", repo)


def test_probe_patch_and_baseline():
    with tempfile.TemporaryDirectory() as d:
        repo, run = setup(d)
        good = sh("git rev-parse HEAD", repo).stdout.strip()
        (repo / "value.txt").write_text("bug\n")
        patch = run / "02-development" / "probe-D-1.patch"
        patch.write_text(sh("git diff", repo).stdout)
        sh("git checkout -- value.txt", repo)
        # 깨끗한 커밋에 재현 패치를 일회용 복사본에만 적용한다
        r = P(f'probe --finding D-1 --cmd "sh check.sh" --expect BUG --patch {patch}', repo)
        assert "재현됨" in r.stdout and (repo / "value.txt").read_text() == "ok\n"
        assert probes(run)[-1]["patch"]
        # 회귀: 기준선에서 통과하고 검수 커밋에서 실패해야 재현이다
        (repo / "value.txt").write_text("bug\n")
        commit(repo, "회귀")
        assert "재현됨" in P(f'probe --finding D-2 --cmd "sh check.sh" --expect BUG --baseline {good}', repo).stdout
        bad = sh("git rev-parse HEAD", repo).stdout.strip()
        r = P(f'probe --finding D-3 --cmd "sh check.sh" --expect BUG --baseline {bad}', repo, ok=False)
        assert r.returncode == 1 and not probes(run)[-1]["reproduced"] and probes(run)[-1]["baseline_exit"] == 1
        # 패치가 맞지 않으면 환경 실패(종료 코드 3)이고 재현이 아니다
        patch.write_text("--- a/없음\n+++ b/없음\n@@ -1 +1 @@\n-a\n+b\n")
        r = P(f'probe --finding D-4 --cmd "sh check.sh" --expect BUG --patch {patch}', repo, ok=False)
        assert r.returncode == 3 and probes(run)[-1]["state"] == "patch-failed"
        assert sh("git worktree list", repo).stdout.strip().count("\n") == 0


def test_premise_hint_after_two_rounds():
    with tempfile.TemporaryDirectory() as d:
        repo, run = setup(d)
        first = P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open --contrast D-1", repo).stdout
        assert "전제 확인" not in first
        second = P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open,D-2=open --contrast D-1 D-2", repo).stdout
        assert "전제 확인: D-1" in second and "D-2" not in second.split("전제 확인:")[1].split("이(가)")[0]
        third = P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open --contrast D-1", repo).stdout
        assert "조정 필요" in third and "전제 확인" not in third


def test_audit_stage_only_for_new_full_runs():
    from cg import core
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = repo / P("init-run 감사 --route full", repo).stdout.strip()
        meta = json.loads((run / "meta.json").read_text())
        assert meta["kit"]
        cfg = json.loads((repo / ".crossgate/config.json").read_text())
        assert core.required_stages(cfg, meta)[-1] == "audit"
        assert core.required_stages(cfg, {"route": "full"})[-1] == "wiki", "0.10 전 실행은 감사 없이 끝난다"
        assert core.required_stages({"routes": {"full": {"stages": ["request", "dev"]}}}, meta) == ["request", "dev"]
        assert "audit" not in core.required_stages(cfg, {**meta, "route": "standard"})
        for st in ("request", "plan", "dev", "wiki"):
            P(f"verdict --stage {st} --verdict APPROVED --actor user", repo)
        P("verdict --stage qa --verdict PASSED --actor codex", repo)
        assert "열림" in P(f"status --run {run}", repo).stdout
        P("verdict --stage audit --verdict APPROVED --actor codex", repo)
        assert "끝남" in P(f"status --run {run}", repo).stdout
        assert "audit" in P(f"status --run {run}", repo).stdout


def test_retro_summarizes_ledgers_read_only():
    with tempfile.TemporaryDirectory() as d:
        repo, run = setup(d)
        (repo / "value.txt").write_text("bug\n")
        commit(repo, "버그")
        P('probe --finding D-1 --cmd "sh check.sh" --expect BUG', repo)
        P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open,D-2=open --contrast D-2", repo)
        P("verdict --stage dev --verdict REJECTED --actor claude --issues D-2=open --contrast D-2", repo)
        P("verdict --stage dev --verdict APPROVED --actor claude", repo, ok=False)
        with (run / "usage.jsonl").open("a") as f:
            f.write(json.dumps({"engine": "claude", "role": "dev-reviewer", "event": "end", "seconds": 120,
                                "input_tokens": 1000, "output_tokens": 50, "cost_usd": 0.5}) + "\n")
        files = {p: p.read_bytes() for p in (repo / "ai-log").rglob("*") if p.is_file()}
        out = P("retro --days 3", repo).stdout
        assert "실행 1개" in out and "개발 차단 지적 3건: 재현 1 · 대조 2" in out
        assert "재현 시도 1회: 재현 1" in out and "두 회차 연속 남은 지적 1건" in out
        assert "claude/dev-reviewer" in out and "$0.50" in out
        data = json.loads(P("retro --json", repo).stdout)
        assert data["stages"]["dev"]["rejected"] == 2 and data["probes"]["reproduced"] == 1
        assert files == {p: p.read_bytes() for p in (repo / "ai-log").rglob("*") if p.is_file()}


def test_probe_timeouts_kill_children_and_env_failures_exit_3():
    with tempfile.TemporaryDirectory() as d:
        repo, run = setup(d)
        cfg = json.loads((repo / ".crossgate/config.json").read_text())
        cfg["probe"]["commands"] = ["sh check.sh", "sh slow.sh"]
        cfg["probe"]["env"] = ["PIDFILE"]  # 허용 목록 밖 환경 변수는 재현 명령에 가지 않는다
        cfg["probe"]["write_allow"] = [d]  # 샌드박스는 일회용 폴더 밖 쓰기를 막는다
        cfg.setdefault("timeouts", {}).setdefault("call_minutes", {})["probe"] = 0.03
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
        (repo / "slow.sh").write_text('sleep 60 & echo $! > "$PIDFILE"\nsleep 60\n')
        commit(repo, "slow")
        pidfile = Path(d) / "child.pid"
        r = sh(f'PIDFILE={pidfile} python3 .crossgate/kit/bin/crossgate probe --finding D-1 --cmd "sh slow.sh" --expect X',
               repo, ok=False)
        assert r.returncode == 3 and probes(run)[-1]["outcome"] == "error" and probes(run)[-1]["state"] == "timeout"
        pid = int(pidfile.read_text())
        alive = sh(f"kill -0 {pid}", repo, ok=False).returncode == 0
        assert not alive, "시간 초과 때 재현 명령의 자식 프로세스도 끝낸다"
        assert sh("git worktree list", repo).stdout.strip().count("\n") == 0
        # 준비 명령에도 시간 제한이 걸린다
        cfg["parallel"] = {"setup": "sleep 60"}
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
        commit(repo, "setup")
        r = P('probe --finding D-2 --cmd "sh check.sh" --expect BUG', repo, ok=False)
        assert r.returncode == 3 and "parallel.setup" in (repo / probes(run)[-1]["log"]).read_text()
        # 기준선만 환경 실패해도 결과는 환경 실패이고, 회고도 그렇게 센다
        cfg.pop("parallel")
        cfg["timeouts"]["call_minutes"]["probe"] = 10
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
        commit(repo, "no setup")
        good = sh("git rev-parse HEAD", repo).stdout.strip()
        (repo / "added.txt").write_text("y\n")
        commit(repo, "기준선 후보: added.txt 있음")
        clash = sh("git rev-parse HEAD", repo).stdout.strip()
        sh("git rm -q added.txt", repo)
        (repo / "value.txt").write_text("bug\n")
        commit(repo, "회귀")
        (repo / "added.txt").write_text("x\n")
        sh("git add -N added.txt", repo)
        patch = run / "02-development" / "add-file.patch"
        patch.write_text(sh("git diff -- added.txt", repo).stdout)
        sh("git rm -q --cached added.txt && rm added.txt", repo)
        P(f'probe --finding D-3 --cmd "sh check.sh" --expect BUG --patch {patch} --baseline {good}', repo)
        assert probes(run)[-1]["outcome"] == "reproduced"
        r = P(f'probe --finding D-4 --cmd "sh check.sh" --expect BUG --baseline HEAD', repo, ok=False)
        assert r.returncode == 1 and "기준선에서도 실패" in r.stdout
        r = P(f'probe --finding D-5 --cmd "sh check.sh" --expect BUG --patch {patch} --baseline {clash}', repo, ok=False)
        last = probes(run)[-1]
        assert r.returncode == 3 and last["state"] == "ran" and last["baseline_state"] == "patch-failed"
        assert last["outcome"] == "error"
        assert json.loads(P("retro --json", repo).stdout)["probes"]["error"] == 3
        # 워크트리를 만들 수 없으면 환경 실패(3)이고 기록이 남는다. 인자 오류는 2다
        assert P('probe --finding D-6 --cmd "sh check.sh" --expect BUG --commit 없는커밋', repo, ok=False).returncode == 2
        sh("chmod a-w .git", repo)
        try:
            r = P('probe --finding D-7 --cmd "sh check.sh" --expect BUG', repo, ok=False)
        finally:
            sh("chmod u+w .git", repo)
        assert r.returncode == 3 and probes(run)[-1]["state"] == "worktree-failed"


def test_proof_and_contrast_are_exclusive():
    with tempfile.TemporaryDirectory() as d:
        repo, run = setup(d)
        (repo / "value.txt").write_text("bug\n")
        commit(repo, "bug")
        P('probe --finding D-1 --cmd "sh check.sh" --expect BUG', repo)
        P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open --contrast D-1", repo)
        v = [json.loads(x) for x in (run / "verdicts.jsonl").read_text().splitlines()][-1]
        assert v.get("proofs", {}) == {} and v["contrast"] == ["D-1"]
        f = json.loads(P("retro --json", repo).stdout)["dev_findings"]
        assert f == {"blocking": 1, "proven": 0, "contrast": 1}


def test_probe_sandbox_confines_writes_network_and_secrets():
    import sys
    import uuid
    if sys.platform != "darwin":
        return
    with tempfile.TemporaryDirectory() as d:
        repo, run = setup(d)
        tag = uuid.uuid4().hex[:8]
        escape = Path.home() / f".crossgate-probe-escape-{tag}"
        secret = Path.home() / f".crossgate-probe-secret-{tag}"
        secret.write_text("TOPSECRET\n")
        (repo / "check.sh").write_text(
            f'echo pwned > "{escape}" 2>/dev/null || echo write-denied\n'
            f'cat "{secret}" 2>/dev/null || echo read-denied\n'
            'open -a Calculator 2>/dev/null || echo open-denied\n'
            'osascript -e 1 2>/dev/null || echo osascript-denied\n'
            'echo "keys=$(env | grep -c -E \'FAKE_API_KEY|DATABASE_URL\')"\n'
            'case "$HOME" in "$TMPDIR"|*crossgate-probe-*) echo home-boxed;; esac\n'
            "python3 -c \"import socket\ntry:\n socket.create_connection(('1.1.1.1', 443), timeout=3); print('net-open')\n"
            "except Exception: print('net-blocked')\"\n"
            'git log --oneline -1 >/dev/null && echo git-ok\n'
            'echo BUG; exit 1\n')
        patch = run / "02-development" / "escape.patch"
        patch.write_text(sh("git diff -- check.sh", repo).stdout)
        sh("git checkout -- check.sh", repo)
        try:
            sh(f'FAKE_API_KEY=s3cr3t DATABASE_URL=postgres://u:p@h/db python3 .crossgate/kit/bin/crossgate probe '
               f'--finding D-1 --cmd "sh check.sh" --expect BUG --patch {patch}', repo)
            log = (repo / probes(run)[-1]["log"]).read_text()
            assert not escape.exists(), "샌드박스는 일회용 폴더 밖 쓰기를 막는다"
            for word in ("write-denied", "read-denied", "open-denied", "osascript-denied", "keys=0", "home-boxed",
                         "net-blocked", "git-ok"):
                assert word in log, (word, log)
            assert "TOPSECRET" not in log and "net-open" not in log
            assert probes(run)[-1]["sandbox"] == "macos"
        finally:
            escape.unlink(missing_ok=True)
            secret.unlink(missing_ok=True)
        cfg = json.loads((repo / ".crossgate/config.json").read_text())
        cfg["probe"]["sandbox"] = False
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
        commit(repo, "sandbox off")
        P('probe --finding D-2 --cmd "sh check.sh" --expect "없는 표시"', repo, ok=False)
        assert probes(run)[-1]["sandbox"] == "none"
