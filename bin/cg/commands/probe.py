"""차단 지적을 일회용 워크트리에서 실행으로 재현한다(CROSSGATE.md R15).

검수자는 읽기 전용이라 재현 명령과 패치를 글로만 낸다. 이 명령이 검수한 커밋을 일회용 복사본(공유 clone)으로 꺼내
패치를 적용하고 허용된 명령을 돌린다. 종료 코드가 0이 아니고 출력에 실패 표시가 있으면 재현이다.
결과는 실행 기록의 probes.jsonl과 raw/에 남고, verdict가 개발 반려를 기록할 때 이것을 확인한다.
"""
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from cg.core import config, config_value, current_run, is_pm, mask, probe_conf, repo_dir, root, setup_command

# 검수자가 붙이는 인자에 셸 연결·치환이 들어가면 허용 목록이 무의미해진다.
UNSAFE = re.compile(r"[;&|<>$`\\\n()]")
# 재현 명령에 넘기는 환경 변수(허용 목록). 나머지는 config.probe.env에 이름을 더한다.
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "USER", "LOGNAME", "SHELL", "CI", "NODE_ENV")
# 샌드박스에서 실행을 막는 프로그램: 샌드박스 밖에서 앱·작업을 띄우거나 클립보드로 내보내는 길.
NO_EXEC = ("/usr/bin/open", "/usr/bin/osascript", "/usr/bin/osacompile", "/bin/launchctl", "/usr/bin/at",
           "/usr/bin/crontab", "/usr/bin/pbcopy", "/usr/bin/pbpaste")
NO_MACH = ("com.apple.coreservices.launchservicesd", "com.apple.lsd.mapdb", "com.apple.lsd.modifydb",
           "com.apple.coreservices.appleevents", "com.apple.pasteboard.1")
# 홈 아래에서 기본으로 읽기를 허용하는 도구 폴더(PATH에서 찾은 것 말고).
TOOL_DIRS = ("Library/Caches/ms-playwright", ".cache/ms-playwright")


def outside_arg(rest):
    """검수자가 붙인 인자가 작업 폴더 밖 경로를 가리키면 참. 셸과 같은 규칙으로 쪼개서 본다."""
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return True
    for token in tokens:
        if token.startswith("-"):
            if "=" in token:
                token = token.split("=", 1)[1]
            elif "/" in token or "~" in token:
                return True  # -c/etc/passwd 처럼 옵션에 붙여 쓴 값
            else:
                continue
        if token.startswith(("/", "~")) or "~" in token or ".." in Path(token).parts or re.search(r"[=:,]/", token):
            return True
    return False


def allowed(prefixes, cmd):
    """허용되면 실제로 돌릴 명령, 아니면 None. 인자는 검증한 토큰을 하나씩 따옴표로 감싸 셸 확장(*, {}, ?)을 막는다."""
    cmd = cmd.strip()
    for prefix in prefixes:
        prefix = prefix.strip()
        if cmd == prefix:
            return prefix
        rest = cmd[len(prefix):]
        if cmd.startswith(prefix + " ") and not UNSAFE.search(rest) and not outside_arg(rest):
            return prefix + "".join(" " + shlex.quote(t) for t in shlex.split(rest))
    return None


def tool_roots(home):
    """PATH에서 홈 아래 도구 설치 폴더를 찾는다. 홈 바로 아래 폴더(Library, .config 등)는 통째로 열지 않는다."""
    roots = set()

    def add(path):
        try:
            rel = path.relative_to(home)
        except ValueError:
            return
        if len(rel.parts) >= 2:
            roots.add(path)
            if path.name == "bin" and len(rel.parts) >= 3:
                roots.add(path.parent)

    for entry in os.environ.get("PATH", "").split(os.pathsep):
        d = Path(entry).expanduser()
        # 상대 항목(`.`, `bin`)은 이 프로세스의 작업 폴더, 곧 본 저장소로 풀린다. 재현 안에서는 복사본 기준이라 열 필요가 없다.
        if not entry or not d.is_absolute() or not d.is_dir():
            continue
        for cand in (d, d.resolve()):
            add(cand)
        try:
            inside = d.resolve().is_relative_to(home)
        except AttributeError:  # 3.8
            inside = str(d.resolve()).startswith(str(home) + os.sep)
        if inside:
            try:
                for f in d.iterdir():
                    if f.is_symlink():
                        add(f.resolve().parent)
            except OSError:
                pass
    for rel in TOOL_DIRS:
        add(home / rel)
    return roots


def within(path, folder):
    """path가 folder와 같거나 그 안에 있다."""
    return path == folder or folder in path.parents


def safe_reads(reads, home, guarded):
    """읽기 허용 후보에서 막을 곳을 다시 여는 항목을 뺀다.

    SBPL은 뒤 규칙이 이기므로, 막은 폴더(본 저장소·git 공통 폴더)와 같거나 그 안이나 그 위인 폴더를 열면 막은 것이 풀린다.
    홈은 도구 폴더가 그 안에 있으므로 홈과 같거나 그 위인 것만 뺀다.
    """
    return {r for r in reads
            if not within(home, r) and not any(within(r, g) or within(g, r) for g in guarded)}


def sandbox(cfg, base, target):
    """(명령 앞에 붙일 인자 또는 None, 기록할 이름). macOS는 sandbox-exec로 가둔다.

    쓰기는 이번 재현의 일회용 폴더와 probe.write_allow만, 홈 아래 파일 내용 읽기는 도구 폴더·이 저장소의 .git·probe.read_allow만,
    샌드박스 밖에서 앱을 띄우는 길(AppleEvents·LaunchServices·open·osascript·launchctl)과 클립보드는 막는다.
    네트워크는 probe.network가 참일 때만.
    ponytail: allow default 위에 막을 것을 쌓는 방식이라 완전한 경계가 아니다(모르는 서비스 경로가 남을 수 있다).
    그래서 Master가 재현 패치를 읽은 뒤 돌리는 절차를 함께 둔다. 더 막아야 하면 deny default 프로필로 바꾼다.
    """
    mode = config_value(cfg, "probe.sandbox", "auto")
    if mode is False:
        return None, "none"
    exe = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
    if not exe:
        # 샌드박스가 없으면 돌리지 않는다. 가두지 않고 돌리려면 설정에서 명시적으로 끈다.
        refuse("이 컴퓨터에서 쓸 수 있는 샌드박스(macOS sandbox-exec)가 없어 재현을 돌리지 않습니다. 재현 패치를 읽은 뒤 "
               "가두지 않고 돌리려면 .crossgate/config.json 의 probe.sandbox 를 false 로 두세요(CROSSGATE.md 6장).")

    def q(path):
        return str(path).replace("\\", "\\\\").replace('"', '\\"')

    def paths(items):
        return "".join(f'(subpath "{q(x)}")' for x in sorted(set(items), key=str))

    home = Path.home().resolve()
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=target,
                            capture_output=True, text=True).stdout.strip()
    # 본 저장소와 git 공통 폴더(저장소가 홈 밖이고 대상이 연결된 워크트리일 때 따로 떨어져 있다)는 읽지 못하게 한다.
    guarded = [Path(target).resolve()] + ([Path(common).resolve()] if common else [])
    reads = safe_reads(tool_roots(home), home, guarded)
    if common:
        reads.add(guarded[-1] / "objects")  # 공유 clone이 빌려 쓰는 객체만. config·hooks는 읽지 않는다
    reads |= {Path(os.path.expanduser(str(x))).resolve() for x in config_value(cfg, "probe.read_allow", []) or []}
    network = bool(config_value(cfg, "probe.network", False))
    rules = ["(version 1)", "(allow default)", "(deny appleevent-send)",
             "(deny process-exec " + "".join(f'(literal "{x}")' for x in NO_EXEC)
             + f'(subpath "{q((base / "home").resolve())}")(subpath "{q((base / "tmp").resolve())}"))',
             "(deny mach-lookup " + "".join(f'(global-name "{x}")' for x in NO_MACH)
             + '(global-name-regex #"^com\\.apple\\.xpc\\.launchd"))']
    if not network:
        rules.append("(deny network*)")
    # 본 저장소는 홈 밖에 있어도 읽지 못하게 한다(.git/config의 토큰, 추적하지 않는 .env). 객체만 빌려 쓴다.
    rules.append("(deny file-read-data " + paths([home] + guarded) + ")")
    if reads:
        rules.append("(allow file-read-data " + paths(reads) + ")")
    rules.append(f'(allow file-read-data (subpath "{q(base.resolve())}"))')
    writes = {base.resolve()} | {Path(os.path.expanduser(str(x))).resolve()
                                 for x in config_value(cfg, "probe.write_allow", []) or []}
    rules.append("(deny file-write*)")
    rules.append("(allow file-write* " + paths(writes) + '(subpath "/dev") '
                 '(regex #"^/private/var/folders/[^/]+/[^/]+/T/xcrun_db(-[A-Za-z0-9]+)?$"))')
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


def attempt(target, commit, patch, cmd, cfg, seconds, setup=(None, None)):
    """(상태, 종료 코드, 출력, 샌드박스). 상태: ran | copy-failed | setup-failed | patch-failed | timeout.

    복사본은 객체만 빌려 쓰는 공유 clone이다(본 저장소의 config·hooks·워크트리 목록을 건드리지 않는다).
    """
    base = Path(tempfile.mkdtemp(prefix="crossgate-probe-"))
    tree = base / "tree"
    (base / "home").mkdir()
    (base / "tmp").mkdir()
    try:
        r = subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(target), str(tree)],
                           capture_output=True, text=True)
        if not r.returncode:
            r = subprocess.run(["git", "-C", str(tree), "checkout", "-q", "--detach", commit], capture_output=True, text=True)
        if r.returncode:
            return "copy-failed", r.returncode, r.stdout + r.stderr, None
        # 준비 명령은 설정(보호 경로)이 정한 것이고 패치 전에 돈다. gate와 같은 신뢰 수준이라 가두지 않는다.
        if setup[0]:
            code, out = shell(setup[0], tree, seconds)
            if code is None:
                return "timeout", None, f"준비 명령({setup[1]}) 시간 초과\n" + out, None
            if code:
                return "setup-failed", code, out, None
        if patch:
            r = subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=tree, capture_output=True, text=True)
            if r.returncode:
                return "patch-failed", r.returncode, r.stdout + r.stderr, None
        prefix, boxed = sandbox(cfg, base, target)
        keep = set(ENV_KEEP) | set(config_value(cfg, "probe.env", []) or [])
        env = {k: v for k, v in os.environ.items() if k in keep}
        env.update(HOME=str(base / "home"), TMPDIR=str(base / "tmp"))
        code, out = shell(cmd, tree, seconds, prefix, env)
        return ("timeout" if code is None else "ran"), code, out, boxed
    finally:
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
    # 저장소별 재현 설정(repos.<이름>.probe)을 전역 probe 위에 키 단위로 덮는다. 아래는 모두 이 값을 읽는다.
    cfg = {**cfg, "probe": probe_conf(cfg, a.repo)}
    setup = setup_command(cfg, a.repo)
    prefixes = config_value(cfg, "probe.commands", None) or [g["run"] for g in rc.get("gate", [])]
    run_cmd = allowed(prefixes, a.cmd)
    if not run_cmd:
        refuse("허용되지 않은 재현 명령입니다. config.probe.commands(없으면 gate 명령)로 시작하고, 뒤에 붙는 인자에는 "
                 "; & | < > $ ` ( ) 를 쓰지 않는다.\n허용: " + " / ".join(prefixes))
    patch = Path(a.patch).resolve() if a.patch else None
    if patch and not patch.is_file():
        refuse(f"패치 파일이 없습니다: {a.patch}")
    commit = resolve(target, a.commit or "HEAD")
    baseline = resolve(target, a.baseline) if a.baseline else None
    seconds = float(config_value(cfg, "timeouts.call_minutes.probe", 10)) * 60
    head = attempt(target, commit, patch, run_cmd, cfg, seconds, setup)
    base = None
    if baseline:
        base = attempt(target, baseline, patch, run_cmd, cfg, seconds, setup)
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
           "commit": commit, "cmd": run_cmd, "expect": a.expect,
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
                 " 샌드박스가 네트워크를 막았을 수 있습니다(로컬 DB·서버가 필요하면 probe.network).")
              + ("" if setup[0] else " 복사본은 새 clone이라 의존성(node_modules 등)이 없습니다. 필요하면 준비 명령"
                 "(parallel.setup, PM 모드는 repos.<이름>.setup)을 설정하세요(CROSSGATE.md 17장)."))
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
