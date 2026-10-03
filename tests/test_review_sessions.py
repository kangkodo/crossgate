"""재검수 이어 쓰기를 임시 저장소와 가짜 엔진으로 검사한다."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from helpers import P, fake_executable, fresh_repo


def setup(d):
    repo = fresh_repo(d).resolve()
    run = repo / P('init-run 검수', repo).stdout.strip()
    cfg = json.loads((repo / '.crossgate/config.json').read_text())
    cfg.pop('resume_reviews', None)  # 옛 설정도 기본으로 이어 쓴다.
    cfg['repos'] = {'가': {'path': '.'}, '나': {'path': '.'}}
    (repo / '.crossgate/config.json').write_text(json.dumps(cfg))
    (repo / 'q.md').write_text('이번 검수 지시')
    for engine in ('codex', 'claude'):
        fake_executable(repo / 'fakebin', engine, f'''#!{sys.executable}
import json, os, pathlib, sys
args = sys.argv[1:]
base = pathlib.Path({str(repo)!r})
prompt = sys.stdin.read()
with (base / 'calls.jsonl').open('a') as f:
    f.write(json.dumps({{'engine': {engine!r}, 'args': args, 'cwd': os.getcwd(), 'prompt': prompt}}) + '\\n')
resuming = 'resume' in args or '--resume' in args
mode = (base / 'failure').read_text() if (base / 'failure').exists() else ''
if resuming and mode == 'exit':
    sys.exit(7)
session = 'continued' if resuming else 'new'
if resuming and mode == 'missing':
    session = None
if {engine!r} == 'codex':
    pathlib.Path(args[args.index('-o') + 1]).write_text('승인')
    print(json.dumps({{'type': 'thread.started', 'thread_id': session}}))
    print(json.dumps({{'type': 'item.completed'}}))
else:
    print(json.dumps({{'result': '승인', 'session_id': session, 'is_error': resuming and mode == 'error'}}))
''')
    return repo, run, cfg


def cli(repo, engine, role='dev-reviewer', *extra):
    p = subprocess.run(['python3', '.crossgate/kit/bin/crossgate', engine, role,
                        '--prompt-file', 'q.md', *extra], cwd=repo,
                       env={**os.environ, 'PATH': str(repo / 'fakebin') + ':' + os.environ['PATH']},
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr


def logs(repo, run):
    calls = [json.loads(x) for x in (repo / 'calls.jsonl').read_text().splitlines()]
    rows = [json.loads(x) for x in (run / 'usage.jsonl').read_text().splitlines()]
    return calls, [x for x in rows if x.get('event', 'end') == 'end']


def test_review_resume_commands_and_switches():
    for engine in ('codex', 'claude'):
        with tempfile.TemporaryDirectory() as d:
            repo, run, cfg = setup(d)
            cli(repo, engine)
            cli(repo, engine)
            calls, ends = logs(repo, run)
            assert [x['resumed'] for x in ends] == [False, True]
            assert calls[1]['prompt'] == '이번 검수 지시'
            assert len(calls[0]['prompt']) > len(calls[1]['prompt'])
            args = calls[1]['args']
            if engine == 'codex':
                assert args[:3] == ['exec', 'resume', 'new']
                assert 'sandbox_mode="read-only"' in args and '-s' not in args and '-C' not in args
                assert calls[1]['cwd'] == str(repo)
                assert calls[0]['args'][calls[0]['args'].index('-s') + 1] == 'read-only'
            else:
                assert args[args.index('--resume') + 1] == 'new'
                for call in calls:
                    assert '--no-session-persistence' not in call['args']
                    assert call['args'][call['args'].index('--tools') + 1] == 'Read,Grep,Glob'
                    assert '--strict-mcp-config' in call['args'] and 'dontAsk' in call['args']
            cli(repo, engine, 'dev-reviewer', '--fresh')
            cfg['resume_reviews'] = False
            (repo / '.crossgate/config.json').write_text(json.dumps(cfg))
            cli(repo, engine)
            calls, ends = logs(repo, run)
            assert all(not x['resumed'] for x in ends[-2:])
            assert all('resume' not in x['args'] and '--resume' not in x['args'] for x in calls[-2:])


def test_review_resume_fallback():
    for engine, mode in [('codex', 'exit'), ('claude', 'exit'), ('codex', 'missing'),
                         ('claude', 'missing'), ('claude', 'error')]:
        with tempfile.TemporaryDirectory() as d:
            repo, run, cfg = setup(d)
            cli(repo, engine)
            (repo / 'failure').write_text(mode)
            cli(repo, engine)
            calls, ends = logs(repo, run)
            assert len(calls) == 3
            assert [x['state'] for x in ends] == ['ok', 'failed', 'ok']
            assert all(not x['resumed'] for x in ends)
            assert calls[2]['prompt'] == calls[0]['prompt']
            assert 'resume' not in calls[2]['args'] and '--resume' not in calls[2]['args']
            assert len({x['id'] for x in ends}) == 3


def test_review_session_scope_and_legacy_records():
    from cg.core import READ_ONLY_ROLES
    for engine in ('codex', 'claude'):
        with tempfile.TemporaryDirectory() as d:
            repo, run, cfg = setup(d)
            usage = run / 'usage.jsonl'
            usage.write_text('')
            for role in sorted(READ_ONLY_ROLES):
                cli(repo, engine, role, '--repo', '가')
                cli(repo, engine, role, '--repo', '나')
            calls, ends = logs(repo, run)
            assert all(not x['resumed'] for x in ends)
            legacy = {'engine': engine, 'role': 'dev-reviewer', 'repo': '가', 'state': 'ok', 'session': 'legacy'}
            with usage.open('a') as f:
                for row in [legacy, {**legacy, 'event': 'start', 'session': 'unfinished'},
                            {**legacy, 'state': 'failed', 'session': 'failed'}, {**legacy, 'session': None}]:
                    f.write(json.dumps(row) + '\n')
            cli(repo, engine, 'dev-reviewer', '--repo', '가')
            calls, ends = logs(repo, run)
            assert ends[-1]['resumed'] and 'legacy' in calls[-1]['args']
            other = repo / P('init-run 다음', repo).stdout.strip()
            cli(repo, engine, 'dev-reviewer', '--repo', '가', '--run', str(other))
            assert not logs(repo, other)[1][-1]['resumed']


def test_review_resume_api_cwd_guidance_and_write_roles():
    from cg import calls
    with tempfile.TemporaryDirectory() as d:
        repo, run, cfg = setup(d)
        cfg['mode'] = 'pm'
        workdir = repo / '작업'
        workdir.mkdir()
        with patch('cg.calls.config', return_value=cfg), patch('cg.calls.root', return_value=repo), \
                patch('cg.prompt.root', return_value=repo), patch('cg.prompt.config', return_value=cfg), \
                patch('cg.core.config', return_value=cfg), patch('cg.core.root', return_value=repo), \
                patch.dict(os.environ, {'PATH': str(repo / 'fakebin') + ':' + os.environ['PATH']}):
            for _ in range(2):
                calls.invoke('codex', 'dev-reviewer', '지시', run=run, workdir=workdir, extra_guidance='추가 안내')
            entries, ends = logs(repo, run)
            assert entries[-1]['cwd'] == str(workdir)
            assert entries[0]['args'][entries[0]['args'].index('-C') + 1] == str(workdir)
            assert entries[-1]['prompt'] == '지시\n\n추가 안내'
            result = calls.invoke('codex', 'dev-reviewer', '지시', run=run, fresh=True)
            assert not result['record']['resumed']
            for role in ('developer', 'qa'):
                for _ in range(2):
                    result = calls.invoke('codex', role, '지시', run=run, repo='가')
                    assert not result['record']['resumed'] and result['sandbox'] == 'workspace-write'
            entries, ends = logs(repo, run)
            assert all('resume' not in x['args'] for x in entries[-4:])
