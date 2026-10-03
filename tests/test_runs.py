import datetime as dt
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from helpers import P, fresh_repo


def cli(repo, args, env_run=None):
    env = dict(os.environ)
    env.pop('CROSSGATE_RUN', None)
    if env_run is not None:
        env['CROSSGATE_RUN'] = env_run
    return subprocess.run(['python3', '.crossgate/kit/bin/crossgate', *args],
                          cwd=repo, env=env, capture_output=True, text=True)


def test_run_binding_and_close():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        first = P('init-run 첫째', repo).stdout.strip()
        second = P('init-run 둘째', repo)
        assert '주의: 현재 실행' in second.stderr
        second = second.stdout.strip()
        args = ['verdict', '--stage', 'dev', '--verdict', 'REJECTED']
        before = {p: p.read_bytes() for p in (repo / 'ai-log').rglob('*') if p.is_file()}
        for command in (args, ['route', 'light'], ['close-run']):
            result = cli(repo, command)
            assert result.returncode and '--run' in result.stderr
            assert Path(first).name in result.stderr and Path(second).name in result.stderr
        assert before == {p: p.read_bytes() for p in (repo / 'ai-log').rglob('*') if p.is_file()}
        assert '주의:' in cli(repo, ['status']).stderr
        assert cli(repo, args, first).returncode == 0
        assert cli(repo, args + ['--run', second], str(repo / first)).returncode == 0
        assert len((repo / first / 'verdicts.jsonl').read_text().splitlines()) == 1
        assert len((repo / second / 'verdicts.jsonl').read_text().splitlines()) == 1
        assert cli(repo, ['close-run', '--run', first, '--note', '작업 완료']).returncode == 0
        meta = json.loads((repo / first / 'meta.json').read_text())
        dt.datetime.fromisoformat(meta['closed'])
        assert meta['close_note'] == '작업 완료'
        assert '이미 닫힌' in cli(repo, ['close-run', '--run', first]).stdout
        assert cli(repo, args).returncode == 0
        assert cli(repo, args + ['--run', first]).returncode != 0
        assert cli(repo, ['close-run']).returncode == 0
        result = cli(repo, args)
        assert result.returncode and 'init-run' in result.stderr and '--run' in result.stderr
        assert cli(repo, ['close-run', '--reopen']).returncode == 0
        assert 'closed' not in json.loads((repo / second / 'meta.json').read_text())
        assert cli(repo, args).returncode == 0


def test_run_state_and_recent_activity():
    from cg import core
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        run = base / 'ai-log/20260101/실행'
        run.mkdir(parents=True)
        (run / 'meta.json').write_text(json.dumps({'route': 'quick'}))
        records = [{'stage': 'request', 'verdict': 'APPROVED'},
                   {'stage': 'dev', 'repo': '가', 'verdict': 'APPROVED'},
                   {'stage': 'dev', 'repo': '나', 'verdict': 'REJECTED'}]
        def save():
            (run / 'verdicts.jsonl').write_text('\n'.join(json.dumps(x) for x in records))
        save()
        assert core.run_state(run, {}) == '열림'
        records[-1]['verdict'] = 'APPROVED'
        save()
        assert core.run_state(run, {}) == '끝남'
        records.append({'stage': 'dev', 'repo': '나', 'verdict': 'READY'})
        save()
        assert core.run_state(run, {}) == '열림'
        (run / 'meta.json').write_text(json.dumps({'route': 'quick', 'closed': '2026-01-01T00:00:00'}))
        assert core.run_state(run, {}) == '닫힘'
        for p in run.iterdir():
            os.utime(p, (time.time() - 120, time.time() - 120))
        (run / 'timeline.md').write_text('최근 기록 아님')
        assert not core.recent_activity(run, {'recent_minutes': 1})
        assert core.recent_activity(run, {})
        (run / 'usage.jsonl').write_text('')
        assert core.recent_activity(run, {'recent_minutes': 1})


def test_rounds_and_caps_by_repository():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        cfgpath = repo / '.crossgate/config.json'
        cfg = json.loads(cfgpath.read_text())
        cfg.update(mode='pm', repos={'가': {'path': '.'}, '나': {'path': '.'}}, cap=4)
        cfgpath.write_text(json.dumps(cfg))
        run = P('init-run 회차', repo).stdout.strip()
        def verdict(v, name=None):
            return P('verdict --stage dev --verdict ' + v +
                     (' --repo ' + name if name else '') + ' --issues D-1=open', repo).stdout
        verdict('REJECTED', '가')
        verdict('REJECTED', '나')
        verdict('BLOCKED', '가')
        assert '조정 필요' not in verdict('REJECTED', '가')
        assert '조정 필요' not in verdict('REJECTED', '나')
        assert 'D-1' in verdict('REJECTED', '가')
        verdict('REJECTED')
        records = [json.loads(x) for x in (repo / run / 'verdicts.jsonl').read_text().splitlines()]
        assert [x['attempt'] for x in records] == [1, 1, 2, 2, 2, 3, 1]
        verdict('READY', '나')
        verdict('REJECTED', '나')
        verdict('READY', '나')
        verdict('REJECTED', '나')
        records = [json.loads(x) for x in (repo / run / 'verdicts.jsonl').read_text().splitlines()]
        assert [x['attempt'] for x in records[-4:]] == [1, 1, 2, 2]
        status = P('status', repo).stdout
        assert 'dev [가]' in status and 'dev [나]' in status
        assert '검수 3/4' in status and '검수 4/4' in status
    from cg import core
    records = [{'stage': 'dev', 'repo': name, 'verdict': 'REJECTED'} for name in ['가', '가', '나']]
    assert core.escalation(records, 'dev', 3, '가') is None
    records.append({'stage': 'dev', 'repo': '가', 'verdict': 'REJECTED'})
    assert '상한(3회)' in core.escalation(records, 'dev', 3, '가')
    assert core.escalation(records, 'dev', 3, '나') is None
