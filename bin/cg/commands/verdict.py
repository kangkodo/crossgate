import datetime as dt
import json
import sys
from cg.core import LABEL, REVIEW, STAGES, VERDICTS, at, attempt_number, config, config_value, current_run, escalation, git, is_pm, load, open_ids, read_jsonl, read_usage, repo_dir, reviews, write_timeline


def cmd_verdict(a):
    # 원장은 고쳐 쓰지 않으므로 잘못된 값은 쓰기 전에 막는다.
    allowed = ("PASSED", "FAILED") if a.stage == "qa" else ("APPROVED", "REJECTED")
    if a.verdict in ("APPROVED", "REJECTED", "PASSED", "FAILED") and a.verdict not in allowed:
        sys.exit(f"{a.stage} 단계의 판정은 {' / '.join(allowed)} 입니다({a.verdict} 불가).")
    issues = {}
    for kv in filter(None, (x.strip() for x in (a.issues or "").split(","))):
        key, sep, state = kv.partition("=")
        if not sep or not key or state not in ("open", "resolved"):
            sys.exit(f"--issues 형식 오류: {kv!r}. <ID>=open 또는 <ID>=resolved 를 쉼표로 잇는다(예: D-1=open,D-2=resolved).")
        issues[key] = state
    cfg, run = config(), current_run(a.run, writing=True)
    recs = load(run)
    repo = repo_dir(cfg, a.repo)
    # PM 모드에서 저장소를 고르지 않은 판정(요청·기획 등)은 코드 버전에 묶지 않는다
    versioned = bool(repo) or not is_pm(cfg)
    if a.verdict in ("APPROVED", "PASSED"):
        if a.unverified:
            sys.exit("미검증 항목이 있으면 승인할 수 없습니다 (R13).")
        dirty = versioned and [line for line in git("status", "--porcelain", strip=False, cwd=repo).splitlines()
                 if line.strip() and not line[3:].strip('"').startswith("ai-log/")]
        if dirty:
            sys.exit("커밋되지 않은 변경이 있습니다. 승인은 커밋된 상태에만 기록합니다.\n" + "\n".join(dirty[:10]))
    attempt = a.attempt or attempt_number(recs, a.stage, a.verdict, a.repo)
    commit = git("rev-parse", "HEAD", cwd=repo) if versioned else None
    contrast = sorted(set(a.contrast or []))
    proofs = {}
    if a.stage == "dev" and a.verdict == "REJECTED" and a.actor != "user":
        # R15: 동작에 대한 차단 지적은 이 커밋에서 probe가 재현한 기록이 있어야 한다. 대조 지적은 --contrast로 밝힌다.
        opened = {k for k, v in issues.items() if v != "resolved"}
        for x in read_jsonl(run / "probes.jsonl"):
            if (x.get("reproduced") and x.get("finding") in opened and x.get("finding") not in contrast
                    and x.get("repo") == a.repo and x.get("commit") == commit):
                proofs[x["finding"]] = x["id"]
        missing = sorted(opened - set(contrast) - set(proofs))
        if missing:
            sys.exit(f"실행 증거가 없는 차단 지적: {', '.join(missing)} (R15). `crossgate probe --finding <ID>`로 이 커밋에서 "
                     "재현하거나, 범위·결정·문서 대조 지적이면 --contrast <ID>로 밝힌다. 재현되지 않으면 권고로 내린다.")
    model = a.model
    # 검수 판정의 모델은 그 엔진의 마지막 호출 기록에서 채운다. 손으로 적은 값이 다르면 알린다
    if a.verdict in REVIEW and a.actor in ("claude", "codex") and (run / "usage.jsonl").exists():
        used = read_usage(run)
        last = next((u["model"] for u in reversed(used) if u.get("engine", "codex") == a.actor), None)
        if last and not model:
            model = last
        elif last and model != last:
            print(f"주의: --model {model} 이 마지막 {a.actor} 호출의 모델 {last} 과 다르다. 그대로 기록한다.", file=sys.stderr)
    rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "stage": a.stage, "attempt": attempt,
           "verdict": a.verdict, "actor": a.actor, "model": model,
           "commit": commit, "repo": a.repo,
           "issues": issues, "unverified": a.unverified or [], "evidence": a.evidence or [],
           "tokens": a.tokens, "note": a.note}
    if contrast or proofs:
        rec.update(contrast=contrast, proofs=proofs)
    with open(run / "verdicts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    write_timeline(run)
    print(f"기록: {a.stage} #{attempt} {LABEL[a.verdict]} @ {at(rec)} → {run.name}")
    recs = load(run)
    # 조정 안내는 반려를 기록한 때만 낸다(ESCALATED·READY를 기록한 직후에 같은 안내를 되풀이하지 않는다).
    esc = a.verdict in ("REJECTED", "FAILED") and escalation(recs, a.stage, config_value(cfg, "cap", 3), a.repo)
    if esc:
        print(f"조정 필요: {esc}. ESCALATED를 기록하고 사용자에게 묻습니다.")
        return
    last = [x for x in reviews(recs, a.stage, a.repo) if x["verdict"] != "BLOCKED"][-2:]
    stuck = sorted(open_ids(last[0]) & open_ids(last[1])) if len(last) == 2 and a.verdict == "REJECTED" else []
    if stuck:
        print(f"전제 확인: {', '.join(stuck)}이(가) 두 회차 연속 남았습니다. 다음 지시 전에 지금까지의 수정이 기댄 전제를 "
              "한 줄로 적고, 그 전제가 틀리면 실패할 확인을 먼저 돌립니다(CROSSGATE.md 5장).")


def register(subparsers):
    p = subparsers.add_parser("verdict")
    p.add_argument("--stage", required=True, choices=STAGES)
    p.add_argument("--verdict", required=True, choices=VERDICTS)
    p.add_argument("--attempt", type=int); p.add_argument("--actor"); p.add_argument("--model")
    p.add_argument("--issues", help="<ID>=open|resolved 를 쉼표로: P-1=open,P-2=resolved"); p.add_argument("--unverified", nargs="*")
    p.add_argument("--evidence", nargs="*"); p.add_argument("--tokens", type=int); p.add_argument("--note")
    p.add_argument("--contrast", nargs="*", metavar="<ID>", help="실행이 아니라 위치·문서로 증명하는 대조 지적(R15)")
    p.add_argument("--run"); p.add_argument("--repo"); p.set_defaults(fn=cmd_verdict)
    p._crossgate_order = 6
