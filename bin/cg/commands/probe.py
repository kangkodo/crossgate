"""차단 지적을 일회용 워크트리에서 실행으로 재현한다(CROSSGATE.md R15).

검수자는 읽기 전용이라 재현 명령과 패치를 글로만 낸다. 이 명령이 검수한 커밋을 일회용 워크트리로 꺼내
패치를 적용하고 허용된 명령을 돌린다. 종료 코드가 0이 아니고 출력에 실패 표시가 있으면 재현이다.
결과는 실행 기록의 probes.jsonl과 raw/에 남고, verdict가 개발 반려를 기록할 때 이것을 확인한다.
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from cg.core import config, config_value, current_run, git, is_pm, mask, repo_dir, root

# 검수자가 붙이는 인자에 셸 연결·치환이 들어가면 허용 목록이 무의미해진다.
UNSAFE = re.compile(r"[;&|<>$`\\\n()]")


def allowed(prefixes, cmd):
    cmd = cmd.strip()
    for prefix in prefixes:
        prefix = prefix.strip()
        if cmd == prefix:
            return True
        if cmd.startswith(prefix + " ") and not UNSAFE.search(cmd[len(prefix):]):
            return True
    return False


def shell(cmd, cwd, seconds):
    """(종료 코드 또는 None(시간 초과), 출력). 자기 프로세스 그룹에서 돌리고, 끝나면 남은 자식까지 정리한다."""
    p = subprocess.Popen(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, errors="replace", start_new_session=True)
    try:
        out, _ = p.communicate(timeout=seconds)
        code = p.returncode
    except subprocess.TimeoutExpired:
        code = None
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if code is None:
        out, _ = p.communicate()
    return code, out or ""


def attempt(target, commit, patch, cmd, cfg, seconds):
    """(상태, 종료 코드, 출력). 상태: ran | worktree-failed | setup-failed | patch-failed | timeout."""
    base = Path(tempfile.mkdtemp(prefix="crossgate-probe-"))
    tree = base / "tree"
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", "worktree", "add", "--detach", "-q", str(tree), commit],
                           cwd=target, capture_output=True, text=True)
        if r.returncode:
            return "worktree-failed", r.returncode, r.stdout + r.stderr
        setup = config_value(cfg, "parallel.setup", None)
        if setup:
            code, out = shell(setup, tree, seconds)
            if code is None:
                return "timeout", None, "준비 명령(parallel.setup) 시간 초과\n" + out
            if code:
                return "setup-failed", code, out
        if patch:
            r = subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=tree, capture_output=True, text=True)
            if r.returncode:
                return "patch-failed", r.returncode, r.stdout + r.stderr
        code, out = shell(cmd, tree, seconds)
        return ("timeout" if code is None else "ran"), code, out
    finally:
        git("worktree", "remove", "--force", str(tree), check=False, cwd=target)
        git("worktree", "prune", check=False, cwd=target)
        shutil.rmtree(base, ignore_errors=True)


def refuse(message):
    """인자·설정 오류. 재현 안 됨(1)·환경 실패(3)와 구분하려고 종료 코드 2를 쓴다."""
    print(message, file=sys.stderr)
    sys.exit(2)


def resolve(target, ref):
    r = subprocess.run(["git", "rev-parse", "--verify", "-q", ref + "^{commit}"], cwd=target, capture_output=True, text=True)
    if r.returncode:
        refuse(f"커밋을 찾을 수 없습니다: {ref}")
    return r.stdout.strip()


def cmd_probe(a):
    cfg, r = config(), root()
    run = current_run(a.run, writing=True)
    repo = repo_dir(cfg, a.repo)
    if is_pm(cfg) and not repo:
        refuse("PM 모드의 probe 는 --repo 가 필요합니다.")
    target = repo or r
    rc = cfg["repos"][a.repo] if repo else cfg
    prefixes = config_value(cfg, "probe.commands", None) or [g["run"] for g in rc.get("gate", [])]
    if not allowed(prefixes, a.cmd):
        refuse("허용되지 않은 재현 명령입니다. config.probe.commands(없으면 gate 명령)로 시작하고, 뒤에 붙는 인자에는 "
                 "; & | < > $ ` ( ) 를 쓰지 않는다.\n허용: " + " / ".join(prefixes))
    patch = Path(a.patch).resolve() if a.patch else None
    if patch and not patch.is_file():
        refuse(f"패치 파일이 없습니다: {a.patch}")
    commit = resolve(target, a.commit or "HEAD")
    baseline = resolve(target, a.baseline) if a.baseline else None
    seconds = float(config_value(cfg, "timeouts.call_minutes.probe", 10)) * 60
    head = attempt(target, commit, patch, a.cmd, cfg, seconds)
    base = None
    if baseline:
        base = attempt(target, baseline, patch, a.cmd, cfg, seconds)
    failed_here = head[0] == "ran" and head[1] != 0 and a.expect in head[2]
    clean_before = base is None or (base[0] == "ran" and base[1] == 0)
    if head[0] != "ran" or (base and base[0] != "ran"):
        state = "error"
    elif failed_here and clean_before:
        state = "reproduced"
    else:
        state = "not-reproduced"
    pid = uuid.uuid4().hex[:8]
    log = run / "raw" / f"probe_{pid}_{a.finding}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    parts = [f"# {a.finding} @ {commit[:7]}: {a.cmd}", f"상태 {head[0]} · 종료 코드 {head[1]}", mask(head[2])]
    if base:
        parts += [f"# 기준선 @ {baseline[:7]}", f"상태 {base[0]} · 종료 코드 {base[1]}", mask(base[2])]
    log.write_text("\n".join(parts) + "\n", encoding="utf-8")
    rec = {"id": pid, "ts": dt.datetime.now().isoformat(timespec="seconds"), "finding": a.finding, "repo": a.repo,
           "commit": commit, "cmd": a.cmd, "expect": a.expect,
           "patch": hashlib.sha256(patch.read_bytes()).hexdigest()[:12] if patch else None,
           "exit": head[1], "state": head[0], "baseline": baseline, "baseline_exit": base[1] if base else None,
           "baseline_state": base[0] if base else None, "outcome": state,
           "reproduced": state == "reproduced", "log": str(log.relative_to(r))}
    with open(run / "probes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    label = {"reproduced": "재현됨", "not-reproduced": "재현 안 됨", "error": "실행 실패(환경)"}[state]
    print(f"{a.finding}: {label} @ {commit[:7]} · 종료 코드 {head[1]}"
          + (f" · 기준선 종료 코드 {base[1]}" if base else "") + f" · 기록 {rec['log']}")
    if state == "not-reproduced" and head[1] != 0 and a.expect not in head[2]:
        print("  실패는 했지만 출력에 실패 표시가 없습니다. 다른 이유의 실패일 수 있습니다(패치·명령 오류).")
    elif state == "not-reproduced" and failed_here and not clean_before:
        print("  기준선에서도 실패합니다. 이번 변경이 만든 회귀가 아닙니다.")
    sys.exit({"reproduced": 0, "not-reproduced": 1, "error": 3}[state])


def register(subparsers):
    p = subparsers.add_parser("probe", help="차단 지적을 일회용 워크트리에서 실행으로 재현(R15)")
    p.add_argument("--finding", required=True, metavar="<ID>", help="지적 ID(예: D-1)")
    p.add_argument("--cmd", required=True, help="재현 명령(config.probe.commands로 시작)")
    p.add_argument("--expect", required=True, help="버그가 있을 때 출력에 나오는 실패 표시")
    p.add_argument("--patch", help="일회용 워크트리에만 적용할 재현 패치(git diff 형식)")
    p.add_argument("--commit", help="재현할 커밋(기본 HEAD)")
    p.add_argument("--baseline", help="회귀 지적: 이 커밋에서는 통과해야 재현으로 친다")
    p.add_argument("--repo"); p.add_argument("--run"); p.set_defaults(fn=cmd_probe)
    p._crossgate_order = 6.5
