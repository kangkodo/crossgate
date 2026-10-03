import json
import tempfile
from pathlib import Path
from helpers import P, commit, fake_executable, fresh_repo, sh


def test_routes_and_effort():
    """등급별 필수 단계와 추론 강도. 가벼움은 request·dev만 보고, 개발·검수를 낮은 강도로 부른다."""
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        fake = Path(d) / "fakebin"
        fake.mkdir()
        log = Path(d) / "args.log"
        fake_executable(fake, "claude", f"""#!/bin/sh
echo "$@" >> {log}
cat >/dev/null
echo '{{"result":"판정: 승인","usage":{{"input_tokens":1,"output_tokens":1}}}}'
""")
        fake_executable(fake, "codex", f"""#!/bin/sh
echo "$@" >> {log}
cat >/dev/null
""")
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
        sh(cli + f"codex plan-reviewer --prompt-file q.md --run {run}", repo)
        assert "model_reasoning_effort=medium" in log.read_text().splitlines()[-1]
        sh(cli + f"codex wiki-reviewer --prompt-file q.md --run {run}", repo)
        assert "model_reasoning_effort=low" in log.read_text().splitlines()[-1], "문서 대조는 낮은 강도"
        sh(cli + f"claude dev-reviewer --prompt-file q.md --run {run}", repo)
        assert "--effort" not in log.read_text().splitlines()[-1]

    from cg import core as mod
    # 0.4 이하 설정과 옛 실행("quick")도 그대로 읽는다
    old = {"required_stages": ["request", "dev"], "quick_stages": ["request", "dev", "qa"]}
    assert mod.route_conf(old, "quick")["stages"] == ["request", "dev", "qa"]
    assert mod.route_conf(old, "full")["stages"] == ["request", "dev"]
    assert mod.route_conf({}, "full")["stages"] == ["request", "plan", "dev", "qa", "wiki"]
    assert mod.route_conf({"routes": {"light": {"effort": {}}}}, "light")["stages"] == ["request", "dev"]

