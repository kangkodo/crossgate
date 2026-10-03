from cg.calls import call_codex


def cmd_codex(a):
    return call_codex(a)


def register(subparsers):
    p = subparsers.add_parser("codex"); p.add_argument("role"); p.add_argument("--prompt-file", required=True)
    p.add_argument("--run"); p.add_argument("--repo"); p.add_argument("--effort"); p.set_defaults(fn=cmd_codex)
    p._crossgate_order = 1
