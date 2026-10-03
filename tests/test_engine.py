"""호출 기록과 제한은 임시 저장소의 가짜 실행 파일로 검사한다."""
import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from helpers import P, fake_executable, fresh_repo


def setup(d, engine, source, timeouts=None):
    repo = fresh_repo(d).resolve()
    run = repo / P('init-run 호출', repo).stdout.strip()
    cfg_path = repo / '.crossgate/config.json'
    cfg = json.loads(cfg_path.read_text())
    cfg['timeouts'] = timeouts or {'call_minutes': {'default': 0.05}, 'stall_seconds': 1}
    cfg_path.write_text(json.dumps(cfg))
    fake_executable(repo / 'fakebin', engine, '#!/bin/sh\n' + source)
    (repo / 'q.md').write_text('검수')
    return repo, run, cfg


def cli(repo, engine='codex', *extra):
    return subprocess.run(['python3', '.crossgate/kit/bin/crossgate', engine,
                           'dev-reviewer', '--prompt-file', 'q.md', *extra], cwd=repo,
                          env={**os.environ, 'PATH': str(repo / 'fakebin') + ':' + os.environ['PATH']},
                          capture_output=True, text=True, timeout=8)


def records(run):
    return [json.loads(line) for line in (run / 'usage.jsonl').read_text().splitlines()]


def test_engine_records_and_concurrency():
    with tempfile.TemporaryDirectory() as d:
        repo, run, cfg = setup(d, 'codex', '''cat >/dev/null
echo '{"type":"thread.started","thread_id":"session-a"}'
echo '{"type":"item.started"}'
echo '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}'
''')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: cli(repo), range(4)))
        assert all(p.returncode == 0 for p in results), [p.stderr for p in results]
        rows = records(run)
        assert len(rows) == 8
        starts = {r['id']: r for r in rows if r['event'] == 'start'}
        ends = [r for r in rows if r['event'] == 'end']
        assert len(starts) == len(ends) == 4
        for end in ends:
            assert re.fullmatch('[0-9a-f]{8}', end['id'])
            assert starts[end['id']]['effort_source'] == end['effort_source'] == 'config'
            assert end['session'] == 'session-a' and end['state'] == 'ok'
            assert end['repo'] is None and end['resumed'] is False
            assert end['input_tokens'] == 5
        assert len(list((run / 'raw').glob('*_task.md'))) == 4
        assert len(list((run / 'raw').glob('*_reply.md'))) == 4
        from cg.core import read_usage
        assert len(read_usage(run)) == 4


def test_engine_timeouts_and_stalls():
    for engine, source, state, attempts in [
        ('codex', 'sleep 5', 'stalled', 2),
        ('codex', "echo '{\"type\":\"item.started\"}'; sleep 5", 'timeout', 1),
        ('claude', 'sleep 5', 'timeout', 1),
    ]:
        with tempfile.TemporaryDirectory() as d:
            repo, run, cfg = setup(d, engine, source, {'call_minutes': {'default': 0.03}, 'stall_seconds': 1})
            started = time.monotonic()
            p = cli(repo, engine)
            assert p.returncode == 3 and 'BLOCKED' in p.stderr, p.stderr
            assert time.monotonic() - started < 5
            rows = records(run)
            assert len(rows) == attempts * 2
            assert all(r['state'] == state and r['exit'] == 3 for r in rows if r['event'] == 'end')


def test_engine_retry_and_first_work_event():
    with tempfile.TemporaryDirectory() as d:
        repo, run, cfg = setup(d, 'codex', '''if [ ! -f attempted ]; then touch attempted; sleep 5; fi
cat >/dev/null
echo '{"type":"item.completed"}'
sleep 1.2
echo '{"type":"thread.started","thread_id":"retry-session"}'
''')
        p = cli(repo)
        assert p.returncode == 0, p.stderr
        assert [r['state'] for r in records(run) if r['event'] == 'end'] == ['stalled', 'ok']


def test_engine_claude_repo_and_failures():
    with tempfile.TemporaryDirectory() as d:
        repo, run, cfg = setup(d, 'claude', '''cat >/dev/null
echo '{"result":"승인","session_id":"claude-session","usage":{"input_tokens":2},"total_cost_usd":0.1}'
''')
        p = cli(repo, 'claude', '--repo', '대상')
        assert p.returncode == 0, p.stderr
        end = records(run)[-1]
        assert end['repo'] == '대상' and end['session'] == 'claude-session'
        assert end['effort'] is None and end['effort_source'] == 'default'
        fake_executable(repo / 'fakebin', 'claude', '#!/bin/sh\nexit 7\n')
        assert cli(repo, 'claude').returncode == 7
        assert records(run)[-1]['state'] == 'failed'


def test_engine_effort_sources():
    from cg.calls import resolve_effort
    with tempfile.TemporaryDirectory() as d:
        run = Path(d)
        (run / 'meta.json').write_text('{"route":"standard"}')
        assert resolve_effort({}, run, 'developer', 'codex') == ('medium', 'default')
        cfg = {'codex': {'default_effort': 'high'}}
        assert resolve_effort(cfg, run, 'developer', 'codex') == ('high', 'config')
        cfg['routes'] = {'standard': {'effort': {'developer': 'xhigh'}}}
        assert resolve_effort(cfg, run, 'developer', 'codex') == ('xhigh', 'route')
        import io
        with patch('sys.stderr', new_callable=io.StringIO) as err:
            assert resolve_effort(cfg, run, 'developer', 'codex', 'low') == ('low', 'flag')
            assert '경고' in err.getvalue()


def test_engine_reusable_call():
    from cg import calls
    with tempfile.TemporaryDirectory() as d:
        repo, run, cfg = setup(d, 'codex', '''tail -n 1 ai-log/*/*/usage.jsonl | grep -q '"event": "start"' || exit 8
cat > prompt.txt
printf '%s\n' "$@" > args.txt
while [ "$#" -gt 0 ]; do
    if [ "$1" = '-o' ]; then shift; printf '승인' > "$1"; fi
    shift
done
echo '{"type":"thread.started","thread_id":"api-session"}'
echo '{"type":"item.started"}'
''')
        workdir = repo / '작업'
        workdir.mkdir()
        with patch('cg.calls.config', return_value=cfg), patch('cg.calls.root', return_value=repo), \
                patch('cg.prompt.root', return_value=repo), \
                patch.dict(os.environ, {'PATH': str(repo / 'fakebin') + ':' + os.environ['PATH']}):
            result = calls.invoke('codex', 'dev-reviewer', '검수 내용', run=run,
                                  workdir=workdir, effort='high', extra_guidance='추가 안내문')
        assert result['state'] == 'ok' and result['reply'] == '승인'
        assert result['session'] == 'api-session' and result['record']['effort_source'] == 'flag'
        assert result['record'] == records(run)[-1]
        args = (repo / 'args.txt').read_text().splitlines()
        assert args[args.index('-C') + 1] == str(workdir)
        assert args[args.index('-s') + 1] == 'read-only'
        assert '추가 안내문' in (repo / 'prompt.txt').read_text()
