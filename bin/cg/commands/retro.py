"""실행 기록을 모아 단계별 판정·회차·재현·호출량을 보여 준다(CROSSGATE.md 16장 회고).

판정 원장(verdicts.jsonl)·호출 기록(usage.jsonl)·재현 기록(probes.jsonl)만 읽는다. 파일을 고치지 않는다.
"""
import datetime as dt
import json
from cg.core import REVIEW, STAGES, config, load, open_ids, read_jsonl, read_usage, root, run_state


def collect(days):
    cfg, r = config(), root()
    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%Y%m%d")
    runs = sorted(p.parent for p in (r / "ai-log").glob("*/*/meta.json") if p.parent.parent.name >= since)
    out = {"days": days, "runs": len(runs), "routes": {}, "states": {}, "stages": {}, "repeated": [],
           "dev_findings": {"blocking": 0, "proven": 0, "contrast": 0},
           "probes": {"total": 0, "reproduced": 0, "not_reproduced": 0, "error": 0}, "usage": {}}
    for run in runs:
        meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
        route = meta.get("route", "full")
        out["routes"][route] = out["routes"].get(route, 0) + 1
        state = run_state(run, cfg)
        out["states"][state] = out["states"].get(state, 0) + 1
        recs = load(run)
        for st in STAGES:
            for repo in sorted({x.get("repo") for x in recs if x["stage"] == st}, key=lambda x: x or ""):
                rv = [x for x in recs if x["stage"] == st and x.get("repo") == repo and x["verdict"] in REVIEW]
                judged = [x for x in rv if x["verdict"] != "BLOCKED"]
                s = out["stages"].setdefault(st, {"reviews": 0, "first_pass": 0, "rejected": 0, "blocked": 0,
                                                  "escalated": 0, "groups": 0})
                s["groups"] += 1
                s["reviews"] += len(judged)
                s["blocked"] += len(rv) - len(judged)
                s["rejected"] += sum(x["verdict"] in ("REJECTED", "FAILED") for x in judged)
                s["first_pass"] += bool(judged) and judged[0]["verdict"] in ("APPROVED", "PASSED")
                s["escalated"] += sum(x["stage"] == st and x.get("repo") == repo and x["verdict"] == "ESCALATED" for x in recs)
                for a, b in zip(judged, judged[1:]):
                    for fid in sorted(open_ids(a) & open_ids(b)):
                        out["repeated"].append(f"{run.name} {st} {fid}")
                if st == "dev":
                    for x in judged:
                        if x["verdict"] == "REJECTED":
                            opened = open_ids(x)
                            proven = opened & set(x.get("proofs", {}))
                            contrast = opened & set(x.get("contrast", [])) - proven
                            out["dev_findings"]["blocking"] += len(opened)
                            out["dev_findings"]["proven"] += len(proven)
                            out["dev_findings"]["contrast"] += len(contrast)
        for p in read_jsonl(run / "probes.jsonl"):
            outcome = p.get("outcome") or ("reproduced" if p.get("reproduced") else
                                           "error" if p.get("state") != "ran" else "not-reproduced")
            key = outcome.replace("-", "_")
            out["probes"]["total"] += 1
            out["probes"][key] += 1
        for u in read_usage(run):
            k = f"{u.get('engine', 'codex')}/{u.get('role', '?')}"
            g = out["usage"].setdefault(k, {"calls": 0, "seconds": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            g["calls"] += 1
            g["seconds"] += u.get("seconds") or 0
            g["input_tokens"] += u.get("input_tokens") or 0
            g["output_tokens"] += u.get("output_tokens") or 0
            g["cost_usd"] += u.get("cost_usd") or 0
    return out


def cmd_retro(a):
    d = collect(a.days)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    print(f"최근 {d['days']}일 실행 {d['runs']}개 · 등급 "
          + (", ".join(f"{k} {v}" for k, v in sorted(d["routes"].items())) or "-")
          + " · " + (", ".join(f"{k} {v}" for k, v in sorted(d["states"].items())) or "-"))
    print("단계별: 검수 수 · 첫 검수 통과 · 반려 · BLOCKED · 조정 요청")
    for st in STAGES:
        s = d["stages"].get(st)
        if s:
            rate = f"{s['first_pass']}/{s['groups']}"
            print(f"  {st:<8} {s['reviews']} · {rate} · {s['rejected']} · {s['blocked']} · {s['escalated']}")
    f = d["dev_findings"]
    print(f"개발 차단 지적 {f['blocking']}건: 재현 {f['proven']} · 대조 {f['contrast']} · 증거 기록 없음(0.10 전) "
          f"{f['blocking'] - f['proven'] - f['contrast']}")
    p = d["probes"]
    print(f"재현 시도 {p['total']}회: 재현 {p['reproduced']} · 재현 안 됨 {p['not_reproduced']} · 환경 실패 {p['error']}")
    print(f"두 회차 연속 남은 지적 {len(d['repeated'])}건" + (": " + "; ".join(d["repeated"][:10]) if d["repeated"] else ""))
    print("호출: 횟수 · 분 · 입력/출력 토큰 · 비용(Claude만 기록)")
    for k, g in sorted(d["usage"].items()):
        cost = f" · ${g['cost_usd']:.2f}" if g["cost_usd"] else ""
        print(f"  {k:<24} {g['calls']} · {g['seconds'] // 60} · {g['input_tokens']:,}/{g['output_tokens']:,}{cost}")


def register(subparsers):
    p = subparsers.add_parser("retro", help="실행 기록 회고: 단계별 판정·회차·재현·호출량(읽기만)")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_retro)
    p._crossgate_order = 13
