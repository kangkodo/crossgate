#!/usr/bin/env python3
"""킷 자체 검사. 임시 git 저장소에 설치해서 판정 기록·병합 조건·조정 감지·마스킹을 확인한다.

  python3 tests/test_crossgate.py
"""
import importlib.machinery
import json
import shutil
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
    return sh(f"python3 .crossgate/kit/bin/crossgate {args}", cwd, ok)


def commit(cwd, msg):
    sh(f"git add -A && git commit -qm '{msg}'", cwd)


def test_mask():
    mod = importlib.machinery.SourceFileLoader("crossgate", str(KIT / "bin" / "crossgate")).load_module()
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


def test_usage():
    mod = importlib.machinery.SourceFileLoader("crossgate", str(KIT / "bin" / "crossgate")).load_module()
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


def test_seal_and_tag_install():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        assert "봉인 유지" in P("verify", repo).stdout
        assert "# source: dev" in (repo / ".crossgate/kit/MANIFEST.sha256").read_text()
        want = (KIT / "VERSION").read_text().strip()
        assert json.loads((repo / ".crossgate/config.json").read_text())["kit_version"] == want, "설치기가 kit_version을 맞춘다"
        assert "경고: config kit_version" not in P("gate", repo).stderr
        card = repo / ".crossgate/kit/roles/qa.md"
        card.write_text(card.read_text() + "\n몰래 고침\n")
        out = P("verify", repo, ok=False)
        assert out.returncode and "변경됨: roles/qa.md" in out.stderr, out.stderr
        assert "킷 복사본이 원본과 다릅니다" in P("gate", repo, ok=False).stderr
        (repo / ".crossgate/kit/roles/extra.md").write_text("x")
        assert "추가됨: roles/extra.md" in P("verify", repo, ok=False).stderr
        # 태그에서 설치하면 그 태그의 내용과 출처가 봉인된다(작업 트리를 임시 저장소에 커밋·태그해 시험)
        kit = Path(d) / "kitcopy"
        shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns(".git"))
        sh("git init -q -b main && git config user.email t@t && git config user.name t && git add -A && git commit -qm k && git tag v9.9.9", kit)
        (kit / "VERSION").write_text("dirty-not-installed\n")  # 작업 중 변경은 태그 설치에 섞이면 안 된다
        sh(f"sh {kit}/install.sh --ref v9.9.9 .", repo)
        assert (repo / ".crossgate/kit/VERSION").read_text().strip() == (KIT / "VERSION").read_text().strip()
        assert "# source: v9.9.9" in (repo / ".crossgate/kit/MANIFEST.sha256").read_text()
        assert not (repo / ".crossgate/kit/roles/extra.md").exists()


def test_migrate_from_0_1():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / "proj"
        repo.mkdir()
        sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
        (repo / "CLAUDE.md").write_text("# 프로젝트\n\n배포는 `pipeline deploy`로 한다.\n")
        (repo / ".github/workflows").mkdir(parents=True)
        (repo / ".github/workflows/pipeline-check.yml").write_text("run: python3 .pipeline/kit/bin/pipeline merge-check\n")
        commit(repo, "init")
        old = Path(d) / "old"
        old.mkdir()
        sh(f"git -C {KIT} archive v0.1.3 | tar -x -C {old}", repo)
        sh(f"sh {old}/install.sh --dev .", repo)
        cfg = json.loads((repo / ".pipeline/config.json").read_text())
        cfg["gate"] = [{"name": "mine", "run": "true"}]
        (repo / ".pipeline/config.json").write_text(json.dumps(cfg))
        (repo / ".pipeline/current-run").write_text("ai-log/x")
        sh(f"sh {KIT}/install.sh --dev .", repo)
        assert not (repo / ".pipeline").exists()
        new_cfg = json.loads((repo / ".crossgate/config.json").read_text())
        assert new_cfg["gate"] == [{"name": "mine", "run": "true"}], "프로젝트 설정 보존"
        assert ".crossgate/*" in new_cfg["protected"] and not any(".pipeline" in x for x in new_cfg["protected"])
        assert (repo / ".crossgate/current-run").read_text() == "ai-log/x"
        assert not (repo / ".claude/skills/pipeline").exists() and not (repo / ".claude/agents/pipeline-reviewer.md").exists()
        assert (repo / ".claude/skills/crossgate/SKILL.md").exists()
        for name in ("AGENTS.md", "CLAUDE.md"):
            t = (repo / name).read_text()
            assert "ai-pipeline-kit" not in t and t.count("crossgate:start") == 1, name
        assert "`crossgate deploy`" in (repo / "CLAUDE.md").read_text()
        gi = (repo / ".gitignore").read_text()
        assert ".crossgate/current-run" in gi and ".pipeline" not in gi
        assert ".crossgate/kit/bin/crossgate" in (repo / ".github/workflows/pipeline-check.yml").read_text()
        assert "봉인 유지" in P("verify", repo).stdout


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
        sh(env + "python3 .crossgate/kit/bin/crossgate claude dev-reviewer --prompt-file q.md", repo)
        args = log.read_text()
        for flag in ("--tools Read,Grep,Glob", "--permission-mode dontAsk", "--strict-mcp-config"):
            assert flag in args, (flag, args)
        assert sh(env + "python3 .crossgate/kit/bin/crossgate claude developer --prompt-file q.md", repo, ok=False).returncode
        sh(env + "python3 .crossgate/kit/bin/crossgate codex plan-reviewer --prompt-file q.md", repo)
        last = log.read_text().splitlines()[-1]
        assert "-s read-only" in last and "--ephemeral" not in last, "세션 기록은 기본으로 남긴다"
        recs = [json.loads(line) for line in (repo / run / "usage.jsonl").read_text().splitlines()]
        assert [r["engine"] for r in recs] == ["claude", "codex"]
        assert recs[0]["input_tokens"] == 10 and recs[0]["cost_usd"] == 0.5
        assert "claude 호출 1회" in P("status", repo).stdout


def test_setup_skill_and_kit_source():
    with tempfile.TemporaryDirectory() as d:
        home = Path(d) / "home"
        home.mkdir()
        sh(f"HOME={home} sh {KIT}/install.sh --global", Path(d))
        skill = (home / ".claude/skills/crossgate-setup/SKILL.md").read_text()
        assert "{{KIT}}" not in skill and str(KIT) in skill and "name: crossgate-setup" in skill
        repo = fresh_repo(d)
        cfg = json.loads((repo / ".crossgate/config.json").read_text())
        assert "kit_source" not in cfg and "kit_remote" not in cfg, "개인 경로는 커밋 대상 설정에 넣지 않는다"
        assert json.loads((repo / ".crossgate/local.json").read_text())["kit_source"] == str(KIT)
        assert ".obsidian/" in (repo / ".gitignore").read_text().splitlines(), "편집기 설정 폴더는 git에서 뺀다"
        assert sh("git check-ignore -q .crossgate/local.json && echo ignored", repo).stdout.strip() == "ignored"
        # 0.2.1이 config.json에 넣은 값은 재설치 때 local.json으로 옮겨진다
        cfg["kit_source"] = "/old/path"; cfg["kit_remote"] = "x"
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
        sh(f"sh {KIT}/install.sh --dev .", repo)
        cfg2 = json.loads((repo / ".crossgate/config.json").read_text())
        assert "kit_source" not in cfg2 and "kit_remote" not in cfg2
        assert (repo / ".claude/skills/crossgate/SKILL.md").read_text().count("## 8. 업그레이드") == 1


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
        (fake / "codex").write_text(f"""#!/bin/sh
echo "$@" >> {log}
cat > {Path(d) / 'prompt.txt'}
echo '{{"type":"turn.completed","usage":{{"input_tokens":5,"output_tokens":1}}}}'
""")
        (fake / "codex").chmod(0o755)
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


def test_routes_and_effort():
    """등급별 필수 단계와 추론 강도. 가벼움은 request·dev만 보고, 개발·검수를 낮은 강도로 부른다."""
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        fake = Path(d) / "fakebin"
        fake.mkdir()
        log = Path(d) / "args.log"
        (fake / "claude").write_text(f"""#!/bin/sh
echo "$@" >> {log}
cat >/dev/null
echo '{{"result":"판정: 승인","usage":{{"input_tokens":1,"output_tokens":1}}}}'
""")
        (fake / "codex").write_text(f"""#!/bin/sh
echo "$@" >> {log}
cat >/dev/null
""")
        for f in fake.iterdir():
            f.chmod(0o755)
        (repo / "q.md").write_text("x")
        env = f"PATH={fake}:$PATH "
        cli = env + "python3 .crossgate/kit/bin/crossgate "

        sh("git switch -qc pipe/light", repo)
        run = P("init-run 여백 --route light", repo).stdout.strip()
        assert json.loads((repo / run / "meta.json").read_text())["route"] == "light"
        # 지시 파일(실행 기록)을 커밋하지 않고 개발 역할을 부르면 멈춘다(gate 오탐 방지)
        out = sh(cli + "codex developer --prompt-file q.md", repo, ok=False)
        assert out.returncode and "커밋하지 않은 변경" in out.stderr, out.stderr
        commit(repo, "run")
        sh(cli + "codex developer --prompt-file q.md", repo)
        assert "model_reasoning_effort=low" in log.read_text().splitlines()[-1]
        sh(cli + "claude dev-reviewer --prompt-file q.md", repo)
        assert "--effort low" in log.read_text().splitlines()[-1]
        # 검수 판정의 모델은 마지막 호출 기록에서 채우고, 손으로 다르게 적으면 알린다
        P("verdict --stage dev --verdict REJECTED --actor claude --issues D-1=open", repo)
        assert json.loads((repo / run / "verdicts.jsonl").read_text().splitlines()[-1])["model"] == "claude-opus-5-5"
        assert "주의: --model" in P("verdict --stage dev --verdict REJECTED --actor claude --model claude-opus-5 --issues D-1=open", repo).stderr
        commit(repo, "verdicts")
        sh(cli + "codex developer --effort medium --prompt-file q.md", repo)
        assert "model_reasoning_effort=medium" in log.read_text().splitlines()[-1], "--effort가 등급보다 우선"
        (repo / "a.js").write_text("2\n")
        commit(repo, "light")
        P("verdict --stage request --verdict APPROVED", repo)
        assert Path(run).name in P("verdict --stage dev --verdict APPROVED", repo).stdout, "판정이 들어간 실행을 보여 준다"
        commit(repo, "log")
        assert "request, dev" in P("merge-check --base main", repo).stdout
        # 승격은 올리기만. 올리면 필수 단계가 늘어 병합 조건도 바뀐다
        assert P("route light", repo, ok=False).returncode
        assert "standard → full" not in P("route standard", repo).stdout
        P("route full", repo)
        meta = json.loads((repo / run / "meta.json").read_text())
        assert meta["route"] == "full" and [x["to"] for x in meta["promoted"]] == ["standard", "full"]
        commit(repo, "promote")
        assert "plan: 최종 판정" in P("merge-check --base main", repo, ok=False).stdout

        out = P("init-run 옛날식 --quick", repo)
        run_q = out.stdout.strip()
        assert "주의: 현재 실행" in out.stderr and "--run" in out.stderr, "최근에 쓰인 실행을 바꿀 때 알린다"
        assert json.loads((repo / run_q / "meta.json").read_text())["route"] == "standard", "--quick = standard"

        # 무거움(기본)은 설정의 역할별 강도를 쓰고, Claude 검수에는 강도를 붙이지 않는다
        run = P("init-run 기능", repo).stdout.strip()
        sh(cli + "codex plan-reviewer --prompt-file q.md", repo)
        assert "model_reasoning_effort=xhigh" in log.read_text().splitlines()[-1]
        sh(cli + "claude dev-reviewer --prompt-file q.md", repo)
        assert "--effort" not in log.read_text().splitlines()[-1]

    mod = importlib.machinery.SourceFileLoader("crossgate", str(KIT / "bin" / "crossgate")).load_module()
    # 0.4 이하 설정과 옛 실행("quick")도 그대로 읽는다
    old = {"required_stages": ["request", "dev"], "quick_stages": ["request", "dev", "qa"]}
    assert mod.route_conf(old, "quick")["stages"] == ["request", "dev", "qa"]
    assert mod.route_conf(old, "full")["stages"] == ["request", "dev"]
    assert mod.route_conf({}, "full")["stages"] == ["request", "plan", "dev", "qa", "wiki"]
    assert mod.route_conf({"routes": {"light": {"effort": {}}}}, "light")["stages"] == ["request", "dev"]


if __name__ == "__main__":
    test_mask()
    test_usage()
    test_gate_todo_checks()
    test_seal_and_tag_install()
    test_migrate_from_0_1()
    test_setup_skill_and_kit_source()
    test_usage_append_only()
    test_reviewers_are_forced_read_only()
    test_workspace_pm_mode()
    test_routes_and_effort()
    test_flow()
    print("ok: crossgate checks passed")
