"""저장소별 재현 설정(repos.<이름>.probe·setup): 적은 키는 전역을 대신하고, 없으면 전역 값을 쓴다."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from helpers import KIT, commit, sh


def test_probe_conf_overrides_by_key():
    from cg import core
    cfg = {"probe": {"network": False, "write_allow": ["/a"]}, "parallel": {"setup": "전역"},
           "repos": {"x": {"probe": {"write_allow": ["/b"]}, "setup": "저장소"}, "y": {"setup": ""}, "z": {}}}
    assert core.probe_conf(cfg) == cfg["probe"] and core.probe_conf(cfg, "z") == cfg["probe"]
    assert core.probe_conf(cfg, "x") == {"network": False, "write_allow": ["/b"]}, "목록은 합치지 않고 대신한다"
    assert core.probe_conf({"repos": {"x": {}}}, "x") == {}
    assert core.setup_command(cfg) == ("전역", "parallel.setup") == core.setup_command(cfg, "z")
    assert core.setup_command(cfg, "x") == ("저장소", "repos.x.setup")
    assert core.setup_command(cfg, "y") == (None, "repos.y.setup"), "빈 값은 그 저장소만 준비 없이 돈다"


def test_probe_uses_repository_settings_then_global():
    boxed = sys.platform == "darwin"  # 쓰기 허용은 샌드박스가 있을 때만 확인한다
    with tempfile.TemporaryDirectory() as d:
        ws, out = (Path(d) / "ws").resolve(), (Path(d) / "out").resolve()
        out.mkdir()
        for name in ("api", "web"):
            r = ws / name
            r.mkdir(parents=True)
            sh("git init -q -b main && git config user.email t@t && git config user.name t", r)
            (r / "check.sh").write_text(
                'echo "setup=$(cat setup.txt 2>/dev/null || echo none) mark=${MARK:-none}"\n'
                f'echo x > "{out}/{name}.txt" 2>/dev/null && echo wrote || echo write-denied\n'
                'echo BUG; exit 1\n')
            (r / "extra.sh").write_text("echo EXTRA; exit 1\n")
            commit(r, "init")
        sh(f"sh {KIT}/install.sh --dev --workspace .", ws)
        path = ws / ".crossgate/config.json"
        cfg = json.loads(path.read_text())
        run = ws / sh("python3 .crossgate/kit/bin/crossgate init-run 재현 --route standard", ws).stdout.strip()

        def probe(repo, cmd="sh check.sh", expect="BUG"):
            p = subprocess.run(["python3", ".crossgate/kit/bin/crossgate", "probe", "--finding", "D-1", "--repo", repo,
                                "--cmd", cmd, "--expect", expect], cwd=ws, capture_output=True, text=True,
                               env={**os.environ, "MARK": "1"})
            rows = (run / "probes.jsonl").read_text().splitlines() if (run / "probes.jsonl").exists() else []
            return p, (ws / json.loads(rows[-1])["log"]).read_text() if rows else ""

        def clear():
            for f in out.iterdir():
                f.unlink()

        # 저장소별 구성: api만 준비 명령·환경 변수·쓰기 허용·명령을 따로 가진다
        cfg["probe"] = {"commands": ["sh check.sh"], "sandbox": "auto" if boxed else False}
        cfg["repos"] = {"api": {"setup": "echo api > setup.txt",
                                "probe": {"commands": ["sh check.sh", "sh extra.sh"], "env": ["MARK"], "write_allow": [str(out)]}},
                        "web": {}}
        path.write_text(json.dumps(cfg))
        p, log = probe("api")
        assert p.returncode == 0 and "setup=api mark=1" in log, p.stdout + p.stderr + log
        assert not boxed or (out / "api.txt").exists()
        p, log = probe("web")
        assert p.returncode == 0 and "setup=none mark=none" in log, p.stdout + p.stderr + log
        assert not boxed or ("write-denied" in log and not (out / "web.txt").exists()), "한 저장소의 허용이 다른 저장소에 새지 않는다"
        assert probe("api", "sh extra.sh", "EXTRA")[0].returncode == 0
        assert probe("web", "sh extra.sh", "EXTRA")[0].returncode == 2, "저장소에 적은 명령 목록은 그 저장소에만 적용된다"
        # 준비 명령이 없는 저장소에서 실패 표시 없이 실패하면 의존성 안내를 낸다
        assert "의존성" in probe("web", expect="없는 표시")[0].stdout
        assert "의존성" not in probe("api", expect="없는 표시")[0].stdout

        # 전역 구성만: 기존 설정은 그대로 모든 저장소에 적용된다
        clear()
        cfg["probe"].update(env=["MARK"], write_allow=[str(out)])
        cfg["parallel"] = {"setup": "echo global > setup.txt"}
        cfg["repos"] = {"api": {}, "web": {}}
        path.write_text(json.dumps(cfg))
        for name in ("api", "web"):
            p, log = probe(name)
            assert p.returncode == 0 and "setup=global mark=1" in log, p.stdout + p.stderr + log
            assert not boxed or (out / f"{name}.txt").exists()
        # 저장소가 전역보다 좁게 닫을 수도 있다
        clear()
        cfg["repos"]["web"] = {"setup": "", "probe": {"write_allow": [], "env": []}}
        path.write_text(json.dumps(cfg))
        p, log = probe("web")
        assert "setup=none mark=none" in log and (not boxed or not (out / "web.txt").exists())
