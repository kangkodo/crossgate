import json
import sys
from cg.core import LABEL, config, git, is_pm, load, match, require_sealed, reviews, root, route_conf


def cmd_merge_check(a):
    require_sealed()
    cfg = config()
    if is_pm(cfg):
        sys.exit("PM 모드에는 merge-check 가 없습니다. 저장소별 병합은 작업공간 규칙(AGENTS.md 등)을 따릅니다.")
    base = git("merge-base", a.base, "HEAD")
    changed = git("diff", "--name-only", base, "HEAD").splitlines()
    runs = sorted({"/".join(f.split("/")[:3]) for f in changed if f.startswith("ai-log/") and f.count("/") >= 3})
    if len(runs) != 1:
        sys.exit(f"✗ 이 변경에 연결된 실행 기록이 {len(runs)}개입니다. 1개여야 합니다: {runs}")
    run = root() / runs[0]
    recs = load(run)
    meta = json.loads((run / "meta.json").read_text(encoding="utf-8"))
    stages = route_conf(cfg, meta["route"])["stages"]
    problems = []
    hit = [f for f in changed if not f.startswith("ai-log/") and match(f, cfg.get("forbidden", []))]
    if hit:
        problems.append("금지 경로 변경: " + ", ".join(hit))
    if any(x["verdict"] == "ESCALATED" for x in recs):
        problems.append("사람 조정 요청(ESCALATED) 기록이 있음")
    for st in stages:
        rv = reviews(recs, st)
        want = "PASSED" if st == "qa" else "APPROVED"
        if not rv or rv[-1]["verdict"] != want:
            problems.append(f"{st}: 최종 판정이 {LABEL[want]}이 아님 ({LABEL[rv[-1]['verdict']] if rv else '없음'})")
            continue
        if rv[-1].get("unverified"):
            problems.append(f"{st}: 미검증 항목 {rv[-1]['unverified']}")
        # ponytail: request·plan은 버전 대조를 하지 않는다. 기획 문서가 승인 뒤 바뀌는 사고가 나오면 대조 대상에 넣는다.
        if st in ("dev", "qa", "wiki"):
            skip = ["ai-log/*"] + (cfg.get("doc_paths", []) if st != "wiki" else [])
            moved = [f for f in git("diff", "--name-only", rv[-1]["commit"], "HEAD").splitlines() if not match(f, skip)]
            if moved:
                problems.append(f"{st} 승인 뒤 바뀐 파일: " + ", ".join(moved[:10]))
    if problems:
        print(f"✗ 자동 병합 불가 ({runs[0]})")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print(f"✓ 자동 병합 조건 충족 ({runs[0]}, 단계 {', '.join(stages)})")


def register(subparsers):
    p = subparsers.add_parser("merge-check"); p.add_argument("--base", required=True); p.set_defaults(fn=cmd_merge_check)
    p._crossgate_order = 8
