from cg.calls import call_claude


def cmd_claude(a):
    return call_claude(a)


def register(subparsers):
    p = subparsers.add_parser("claude"); p.add_argument("role"); p.add_argument("--prompt-file", required=True)
    p.add_argument("--run"); p.add_argument("--effort"); p.set_defaults(fn=cmd_claude)
    p._crossgate_order = 2
