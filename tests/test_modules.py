"""모듈 연결부와 설치된 진입점 검사."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_core_seams():
    from cg import core
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve()
        (root / '.crossgate').mkdir()
        (root / '.crossgate/current-run').write_text('current')
        (root / 'current').mkdir()
        (root / 'current/meta.json').write_text(json.dumps({'route': 'full'}))
        with patch.object(core, 'root', return_value=root), patch.object(core, 'config', return_value={}), patch.dict(os.environ, {}, clear=True):
            assert core.current_run(writing=True) == root / 'current'
            os.environ['CROSSGATE_RUN'] = 'environment'
            assert core.current_run() == root / 'environment'
            os.environ['CROSSGATE_RUN'] = str(root / 'absolute')
            assert core.current_run() == root / 'absolute'
            assert core.current_run(str(root / 'explicit')) == root / 'explicit'
        assert core.read_usage(root) == []
        usage = root / 'usage.jsonl'
        usage.write_text('')
        assert core.read_usage(root) == []
        records = [{'event': 'start'}, {'model': 'old'}, {'event': 'end', 'model': 'new'}]
        usage.write_text('\n'.join(json.dumps(r) for r in records) + '\n\n')
        assert core.read_usage(root) == records[1:]
    cfg = {'timeouts': {'stall_seconds': 0}, 'resume_reviews': False}
    assert core.config_value(cfg, 'timeouts.stall_seconds', 180) == 0
    assert core.config_value(cfg, 'timeouts.call_minutes.default', 20) == 20
    assert core.config_value(cfg, 'resume_reviews', True) is False


def test_installed_modules():
    from helpers import fresh_repo, P
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        kit = repo / '.crossgate/kit'
        manifest = (kit / 'MANIFEST.sha256').read_text()
        assert 'bin/cg/core.py' in manifest
        assert 'bin/cg/commands/gate.py' in manifest
        P('init-run modules', repo)
        P('status', repo)
        P('gate', repo)
        P('verify', repo)
        assert not list(kit.rglob('__pycache__'))
        module = kit / 'bin/cg/core.py'
        module.write_text(module.read_text() + '\n# 봉인 검사\n')
        assert '변경됨: bin/cg/core.py' in P('verify', repo, ok=False).stderr


def test_command_discovery():
    from helpers import fresh_repo, P
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        commands = repo / '.crossgate/kit/bin/cg/commands'
        (commands / 'probe.py').write_text(
            'def register(subparsers):\n'
            '    parser = subparsers.add_parser("probe")\n'
            '    parser.set_defaults(fn=lambda args: print("자동 등록 확인"))\n')
        assert P('probe', repo).stdout.strip() == '자동 등록 확인'
