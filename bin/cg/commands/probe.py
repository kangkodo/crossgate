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
# 작업 폴더 밖 파일을 설정·모듈로 불러오게 하는 인자(절대 경로, 홈, 상위 폴더)도 막는다.
OUTSIDE = re.compile(r"""(^|[\s='"])[/~]|(^|[\s='"/])\.\.([/\s'"]|$)""")
# 재현 명령의 환경에서 뺄 변수 이름. 패치된 코드가 열쇠를 읽지 못하게 한다.
SECRET_ENV = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)")
# 샌드박스에서 읽기를 막는 홈 아래 비밀 폴더.
SENSITIVE = (".ssh", ".aws", ".gnupg", ".config/gh", ".netrc", ".docker", "Library/Keychains")


def allowed(prefixes, cmd):
    cmd = cmd.strip()
    for prefix in prefixes:
        prefix = prefix.strip()
        if cmd == prefix:
            return True
        rest = cmd[len(prefix):]
        if cmd.startswith(prefix + " ") and not UNSAFE.search(rest) and not OUTSIDE.search(rest):
            return True
    return False


def sandbox(cfg, tree):
    """(명령 앞에 붙일 인자 또는 None, 기록할 이름). macOS는 sandbox-exec로 가둔다.

    쓰기는 일회용 워크트리·임시 폴더만, 홈의 비밀 폴더 읽기 금지, 네트워크는 probe.network가 참일 때만.
    ponytail: macOS만 가둔다. 다른 운영체제는 경고하고 기록만 남긴다. 필요해지면 bwrap 등을 같은 자리에 붙인다.
    """
    mode = config_value(cfg, "probe.sandbox", "auto")
    if mode is False:
        return None, "none"
    exe = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
    if not exe:
        if mode is True:
            refuse("probe.sandbox 가 켜져 있지만 이 컴퓨터에서 쓸 수 있는 샌드박스(macOS sandbox-exec)가 없습니다.")
        print("주의: 샌드박스 없이 재현합니다. 재현 패치를 먼저 읽고 돌리세요(CROSSGATE.md 6장).", file=sys.stderr)
        return None, "none"

    def q(path):
        return str(path).replace("\\", "\\\\").replace('"', '\\"')

    network = bool(config_value(cfg, "probe.network", False))
    home = Path.home()
    rules = ["(version 1)", "(allow default)"]
    if not network:
        rules.append("(deny network*)")
    rules.append("(deny file-read* " + "".join(f'(subpath "{q(home / d)}")' for d in SENSITIVE) + ")")
    rules.append("(deny file-write*)")
    rules.append(f'(allow file-write* (subpath "{q(tree.resolve())}") (subpath "/private/tmp") '
                 '(subpath "/private/var/folders") (subpath "/dev"))')
    return [exe, "-p", "".join(rules)], "macos" + ("+network" if network else "")


def shell(cmd, cwd, seconds, prefix=None, env=None):
    """(종료 코드 또는 None(시간 초과), 출력). 자기 프로세스 그룹에서 돌리고, 끝나면 남은 자식까지 정리한다."""
    args = prefix + ["/bin/sh", "-c", cmd] if prefix else cmd
    p = subprocess.Popen(args, shell=not prefix, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, text=True, errors="replace", start_new_session=True, env=env)
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
    """(상태, 종료 코드, 출력, 샌드박스). 상태: ran | worktree-failed | setup-failed | patch-failed | timeout."""
    base = Path(tempfile.mkdtemp(prefix="crossgate-probe-"))
    tree = base / "tree"
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", "worktree", "add", "--detach", "-q", str(tree), commit],
                           cwd=target, capture_output=True, text=True)
        if r.returncode:
            return "worktree-failed", r.returncode, r.stdout + r.stderr, None
        # 준비 명령은 설정(보호 경로)이 정한 것이고 패치 전에 돈다. gate와 같은 신뢰 수준이라 가두지 않는다.
        setup = config_value(cfg, "parallel.setup", None)
        if setup:
            code, out = shell(setup, tree, seconds)
            if code is None:
                return "timeout", None, "준비 명령(parallel.setup) 시간 초과\n" + out, None
            if code:
                return "setup-failed", code, out, None
        if patch:
            r = subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=tree, capture_output=True, text=True)
            if r.returncode:
                return "patch-failed", r.returncode, r.stdout + r.stderr, None
        prefix, boxed = sandbox(cfg, tree)
        env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
        code, out = shell(cmd, tree, seconds, prefix, env)
        return ("timeout" if code is None else "ran"), code, out, boxed
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
           "baseline_state": base[0] if base else None, "outcome": state, "sandbox": head[3],
           "reproduced": state == "reproduced", "log": str(log.relative_to(r))}
    with open(run / "probes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    label = {"reproduced": "재현됨", "not-reproduced": "재현 안 됨", "error": "실행 실패(환경)"}[state]
    print(f"{a.finding}: {label} @ {commit[:7]} · 종료 코드 {head[1]}"
          + (f" · 기준선 종료 코드 {base[1]}" if base else "") + f" · 기록 {rec['log']}")
    if state == "not-reproduced" and head[1] != 0 and a.expect not in head[2]:
        print("  실패는 했지만 출력에 실패 표시가 없습니다. 다른 이유의 실패일 수 있습니다(패치·명령 오류)."
              + ("" if head[3] in (None, "none") or "network" in head[3] else
                 " 샌드박스가 네트워크를 막았을 수 있습니다(로컬 DB·서버가 필요하면 probe.network)."))
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
