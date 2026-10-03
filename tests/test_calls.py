import json
import tempfile
from pathlib import Path
from helpers import P, fake_executable, fresh_repo, sh


def test_usage():
    from cg import calls as mod
    out = "\n".join([
        '{"type":"thread.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":7,"reasoning_output_tokens":3}}',
        "not json",
        '{"type":"turn.completed","usage":{"input_tokens":20,"output_tokens":1}}',
    ])
    assert mod.sum_usage(out) == {"input_tokens": 120, "cached_input_tokens": 40, "output_tokens": 8, "reasoning_output_tokens": 3}
    assert mod.sum_usage("") == dict.fromkeys(mod.USAGE_KEYS, 0)


def test_reviewers_are_forced_read_only():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = P("init-run r", repo).stdout.strip()
        fake = Path(d) / "fakebin"
        fake.mkdir()
        log = Path(d) / "args.log"
        fake_executable(fake, "claude", f"""#!/bin/sh
echo "$@" >> {log}
cat >/dev/null
echo '{{"result":"판정: 승인","total_cost_usd":0.5,"usage":{{"input_tokens":3,"cache_read_input_tokens":7,"output_tokens":2}}}}'
""")
        fake_executable(fake, "codex", f"""#!/bin/sh
echo "$@" >> {log}
cat >/dev/null
echo '{{"type":"turn.completed","usage":{{"input_tokens":5,"output_tokens":1}}}}'
""")
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
        recs = [r for r in recs if r.get("event", "end") == "end"]
        assert [r["engine"] for r in recs] == ["claude", "codex"]
        assert recs[0]["input_tokens"] == 10 and recs[0]["cost_usd"] == 0.5
        assert "claude 호출 1회" in P("status", repo).stdout

