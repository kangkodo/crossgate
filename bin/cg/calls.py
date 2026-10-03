import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from cg.core import READ_ONLY_ROLES, WRITE_ROLES, config, current_run, effort_for, git, is_pm, mask, repo_dir, request_approved, root
from cg.prompt import build_prompt


USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
CLAUDE_TOOLS = "Read,Grep,Glob"


def sum_usage(jsonl):
    """codex exec --json 출력에서 turn.completed 이벤트의 토큰을 합친다."""
    total = dict.fromkeys(USAGE_KEYS, 0)
    for line in jsonl.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "turn.completed":
            for k in USAGE_KEYS:
                total[k] += e.get("usage", {}).get(k) or 0
    return total


def call_codex(a):
    cfg, r = config(), root()
    run = current_run(a.run, writing=True)
    if a.role not in READ_ONLY_ROLES | WRITE_ROLES:
        sys.exit(f"Codex가 맡을 수 없는 역할: {a.role}")
    sandbox = "read-only" if a.role in READ_ONLY_ROLES else "workspace-write"
    repo = repo_dir(cfg, a.repo)
    if is_pm(cfg) and sandbox == "workspace-write" and not repo:
        sys.exit("PM 모드의 개발·QA 역할은 --repo 가 필요합니다(쓰기를 저장소 하나로 제한).")
    if not is_pm(cfg) and sandbox == "workspace-write":
        dirty = [l for l in git("status", "--porcelain", "--", "ai-log", strip=False, cwd=r).splitlines()
                 if l.strip() and "/raw/" not in l and "/03-qa/captures/" not in l and not l.rstrip().endswith("usage.jsonl")]
        if dirty:
            sys.exit("ai-log에 커밋하지 않은 변경이 있습니다. 지시 파일까지 커밋한 뒤 부르고, 그 커밋을 gate --protect-since 에 줍니다"
                     "(아니면 gate가 Master의 파일을 보호 경로 위반으로 잡는다).\n" + "\n".join(dirty[:5]))
    if a.role in ("plan-reviewer", "developer") and not request_approved(run):
        print("주의: 이 실행에는 요청 단계 승인이 없다. 결정 목록을 한 번에 묻고 승인한 뒤 진행한다(CROSSGATE.md 1장).", file=sys.stderr)
    c = cfg.get("codex", {})
    effort = effort_for(cfg, run, a.role, "codex", a.effort) or "medium"
    task = Path(a.prompt_file).read_text(encoding="utf-8")
    prompt = build_prompt(a.role, run, task, repo, sandbox == "workspace-write")
    stamp = dt.datetime.now().strftime("%H%M%S")
    out = run / "raw" / f"{stamp}_{a.role}_reply.md"
    model = c.get("model", "gpt-6-astra")
    cmd = ["codex", "exec", "-m", model, "-c", f"model_reasoning_effort={effort}",
           "-s", sandbox, "-C", str(repo or r), "--skip-git-repo-check", "--json", "-o", str(out), "-"]
    if c.get("ephemeral"):
        cmd.insert(-1, "--ephemeral")
    if sandbox == "workspace-write" and c.get("network"):
        cmd[1:1] = ["-c", "sandbox_workspace_write.network_access=true"]
    started = time.time()
    p = subprocess.run(cmd, input=prompt, text=True, capture_output=True, cwd=r)
    (run / "raw" / f"{stamp}_{a.role}_task.md").write_text(mask(task), encoding="utf-8")
    reply = mask(out.read_text(encoding="utf-8")) if out.exists() else ""
    if out.exists():
        out.write_text(reply, encoding="utf-8")
    usage = sum_usage(p.stdout)
    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "engine": "codex", "role": a.role, "model": model, "effort": effort,
           "seconds": round(time.time() - started), "exit": p.returncode, **usage}
    with open(run / "usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(reply)
    print(f"\n[codex {a.role} · {sandbox} · {effort} · {rec['seconds']}s · "
          f"in {usage['input_tokens']:,} / out {usage['output_tokens']:,} tok · exit {p.returncode}]", file=sys.stderr)
    if p.returncode:
        print(mask(p.stderr[-2000:]), file=sys.stderr)
        sys.exit(p.returncode)


def call_claude(a):
    """Claude 검수를 읽기 도구만 켠 헤드리스 세션으로 돌린다. MCP 서버도 싣지 않는다."""
    cfg, r = config(), root()
    run = current_run(a.run, writing=True)
    if a.role not in READ_ONLY_ROLES:
        sys.exit(f"crossgate claude는 검수 역할만 맡는다: {sorted(READ_ONLY_ROLES)}")
    model = cfg.get("claude", {}).get("model", "claude-opus-5-5")
    task = Path(a.prompt_file).read_text(encoding="utf-8")
    prompt = build_prompt(a.role, run, task)
    cmd = ["claude", "-p", "--model", model, "--tools", CLAUDE_TOOLS, "--permission-mode", "dontAsk",
           "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"]
    effort = effort_for(cfg, run, a.role, "claude", a.effort)
    if effort:
        cmd += ["--effort", effort]
    started = time.time()
    p = subprocess.run(cmd, input=prompt, text=True, capture_output=True, cwd=r)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {}
    u = out.get("usage", {})
    usage = {"input_tokens": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0),
             "cached_input_tokens": u.get("cache_read_input_tokens") or 0, "output_tokens": u.get("output_tokens") or 0,
             "reasoning_output_tokens": 0, "cost_usd": out.get("total_cost_usd")}
    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "engine": "claude", "role": a.role, "model": model,
           "effort": effort, "seconds": round(time.time() - started), "exit": p.returncode, **usage}
    with open(run / "usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stamp = dt.datetime.now().strftime("%H%M%S")
    reply = mask(out.get("result") or "")
    (run / "raw" / f"{stamp}_{a.role}_task.md").write_text(mask(task), encoding="utf-8")
    (run / "raw" / f"{stamp}_{a.role}_reply.md").write_text(reply, encoding="utf-8")
    print(reply)
    print(f"\n[claude {a.role} · 읽기 전용({CLAUDE_TOOLS}) · {rec['seconds']}s · in {usage['input_tokens']:,} / "
          f"out {usage['output_tokens']:,} tok · ${usage['cost_usd'] or 0:.2f} · exit {p.returncode}]", file=sys.stderr)
    if p.returncode or not out:
        print(mask(p.stderr[-2000:]), file=sys.stderr)
        sys.exit(p.returncode or 1)
