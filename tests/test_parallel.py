"""동시 개발은 임시 저장소와 가짜 Codex로만 검사한다."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from helpers import P, commit, fake_executable, fresh_repo, sh


FAKE = '''#!/bin/sh
while [ "$#" -gt 0 ]; do
    if [ "$1" = '-C' ]; then shift; work="$1"; fi
    if [ "$1" = '-s' ]; then shift; test "$1" = workspace-write || exit 8; fi
    shift
done
prompt=$(cat)
printf '%s' "$prompt" | grep -q 'TODO 파일을 고치지 않고' || exit 9
printf '%s' "$prompt" | grep -q '지시 파일에 적힌 대상 파일만' || exit 10
cd "$work" || exit 11
printf '%s\\n' "$PWD" >> "$PARALLEL_WORK_LOG"
echo '{"type":"item.started"}'
printf '%s\\n' "$prompt" | sed -n 's/^실행: //p' | sh
'''


def prepare(d, tasks, gate='true', parallel=None, route='full'):
    repo = (Path(d) / 'repo').resolve()
    repo.mkdir()
    fresh_repo(repo)
    run = repo / P('init-run 동시 --route ' + route, repo).stdout.strip()
    cfgpath = repo / '.crossgate/config.json'
    cfg = json.loads(cfgpath.read_text())
    cfg['gate'] = [{'name': '검사', 'run': gate}]
    if parallel is not None:
        cfg['parallel'] = parallel
    cfgpath.write_text(json.dumps(cfg))
    for i, task in enumerate(tasks):
        (repo / f'q{i}.md').write_text('실행: ' + task + '\n')
    commit(repo, '준비')
    fake_executable(Path(d) / 'fakebin', 'codex', FAKE)
    return repo, run


def parallel_cli(repo, count, *args):
    tasks = [arg for i in range(count) for arg in ('--task', f'q{i}.md')]
    return subprocess.run(['python3', '.crossgate/kit/bin/crossgate', 'parallel', *tasks, *args],
                          cwd=repo, env={**os.environ, 'PATH': str(repo.parent / 'fakebin') + ':' + os.environ['PATH'],
                               'PARALLEL_WORK_LOG': str(repo.parent / 'workdirs')},
                          capture_output=True, text=True, timeout=30)


def test_parallel_merges_and_cleans():
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['echo 첫째 > first.txt', 'echo 둘째 > second.txt'])
        result = parallel_cli(repo, 2)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.count('| merged |') == 2, result.stdout
        workdirs = (repo.parent / 'workdirs').read_text().splitlines()
        assert len(set(workdirs)) == 2
        assert all(Path(path).parent == repo / '.crossgate/worktrees' for path in workdirs)
        assert (repo / 'first.txt').read_text().strip() == '첫째'
        assert (repo / 'second.txt').read_text().strip() == '둘째'
        assert len(sh('git worktree list --porcelain', repo).stdout.split('worktree ')) == 2
        assert not sh('git branch --list "cg/*"', repo).stdout.strip()
        rows = [json.loads(x) for x in (run / 'usage.jsonl').read_text().splitlines()]
        assert len([x for x in rows if x['event'] == 'end']) == 2


def test_parallel_failure_paths_continue():
    for tasks, expected, gate in [
        (['echo 첫째 > a.js', 'echo 둘째 > a.js', 'echo 셋째 > third.txt'], 'conflict', 'true'),
        (['mkdir -p tests/acceptance; echo 침범 > tests/acceptance/check', 'echo 정상 > good.txt'], 'protected', 'true'),
        (['touch bad', 'echo 정상 > good.txt'], 'gate-failed', 'test ! -f bad'),
        (['exit 7', 'echo 정상 > good.txt'], 'failed', 'true'),
    ]:
        with tempfile.TemporaryDirectory() as d:
            repo, run = prepare(d, tasks, gate)
            result = parallel_cli(repo, len(tasks))
            assert result.returncode == 1, result.stdout + result.stderr
            assert f'| {expected} |' in result.stdout and '| merged |' in result.stdout, result.stdout
            assert len(sh('git branch --list "cg/*"', repo).stdout.splitlines()) == 1
            assert not (repo / '.git/MERGE_HEAD').exists()
            assert (repo / ('third.txt' if expected == 'conflict' else 'good.txt')).exists()


def test_parallel_single_and_start_conditions():
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['touch bad'], 'test ! -f bad')
        result = parallel_cli(repo, 1)
        assert result.returncode == 1 and '| gate-failed |' in result.stdout
        assert (repo / 'bad').exists()
        assert not (repo / '.crossgate/worktrees').exists()
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['touch good'], route='light')
        result = parallel_cli(repo, 1)
        assert result.returncode and 'light' in result.stderr
        assert not (run / 'usage.jsonl').exists()
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['touch good'])
        (repo / 'dirty').touch()
        result = parallel_cli(repo, 1)
        assert result.returncode and '깨끗' in result.stderr
        assert not (run / 'usage.jsonl').exists()


def test_parallel_limits_and_gate_order():
    for configured, override, expected in [(2, None, 2), (3, '1', 1)]:
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / 'events'
            probe = Path(d) / 'probe.py'
            probe.write_text('''import fcntl, sys, time
from pathlib import Path
log = Path(sys.argv[1])
def event(name):
    with log.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(name + '\\n')
event('start')
time.sleep(0.3)
Path(sys.argv[2]).touch()
event('end')
''')
            tasks = [f'python3 {probe} {log} result{i}' for i in range(3)]
            repo, run = prepare(d, tasks, f'echo gate >> {log}', {'max': configured})
            result = parallel_cli(repo, 3, *(['--max', override] if override else []))
            assert result.returncode == 0, result.stdout + result.stderr
            active = peak = ended = gates = 0
            for event in log.read_text().splitlines():
                if event == 'start':
                    active += 1
                    peak = max(peak, active)
                elif event == 'end':
                    active -= 1
                    ended += 1
                else:
                    assert ended == 3 and active == 0
                    gates += 1
            assert peak == expected and gates == 4, (peak, gates)


def test_parallel_setup_and_numbers():
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['test -f prepared; touch first', 'test -f prepared; touch second'],
                            'test -f prepared', {'setup': 'touch prepared'})
        result = parallel_cli(repo, 2)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (run / 'raw/parallel-number').read_text() == '2'
        commit(repo, '기록')
        # 남아 있는 브랜치와 워크트리의 번호도 건너뛴다.
        sh(f'git branch cg/{run.name}/7', repo)
        remaining = repo / '.crossgate/worktrees' / (run.name + '-9')
        sh(f'git worktree add --detach {remaining}', repo)
        result = parallel_cli(repo, 2)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (run / 'raw/parallel-number').read_text() == '11'
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['touch first', 'touch second'], parallel={'setup': 'exit 1'})
        result = parallel_cli(repo, 2)
        assert result.returncode == 1 and result.stdout.count('| failed |') == 2
        assert not (run / 'usage.jsonl').exists()
        assert len(sh('git branch --list "cg/*"', repo).stdout.splitlines()) == 2


def test_parallel_pm_repositories():
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['echo 첫째 > first', 'echo 둘째 > second', 'echo 셋째 > third'])
        for name in ('api', 'web'):
            (repo / name).mkdir()
            fresh_repo(repo / name)
        cfgpath = repo / '.crossgate/config.json'
        cfg = json.loads(cfgpath.read_text())
        cfg.update(mode='pm', repos={name: {'path': name, 'gate': [{'name': '검사', 'run': 'true'}]}
                                     for name in ('api', 'web')})
        cfgpath.write_text(json.dumps(cfg))
        missing = parallel_cli(repo, 1)
        assert missing.returncode and 'PM 모드' in missing.stderr
        args = ['--task', 'api:q0.md', '--task', 'api:q1.md', '--task', 'web:q2.md']
        result = parallel_cli(repo, 0, *args)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.count('| merged |') == 3
        assert (repo / 'api/first').exists() and (repo / 'api/second').exists()
        assert (repo / 'web/third').exists()
        assert not sh('git status --porcelain', repo / 'web').stdout.strip()
        assert not sh('git branch --list "cg/*"', repo / 'api').stdout.strip()
        assert not list((repo / '.crossgate/worktrees').iterdir())


def test_parallel_final_gate_and_single_commit():
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['touch first', 'touch second'], 'test ! -f first || test ! -f second')
        result = parallel_cli(repo, 2)
        assert result.returncode == 1 and result.stdout.count('| merged |') == 2
        assert '마지막 gate 실패' in result.stdout
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['echo 완료 > a.js'])
        result = parallel_cli(repo, 1)
        assert result.returncode == 0 and '| merged |' in result.stdout, result.stdout + result.stderr
        assert not sh('git status --porcelain', repo).stdout.strip()
        assert not (repo / '.crossgate/worktrees').exists()


def test_parallel_call_limits_leave_other_tasks_running():
    for state, limits, command in [
        ('timeout', {'call_minutes': {'default': 0.03}, 'stall_seconds': 2}, 'sleep 5'),
        ('stalled', {'call_minutes': {'default': 1}, 'stall_seconds': 0.5}, 'sleep 5'),
    ]:
        with tempfile.TemporaryDirectory() as d:
            repo, run = prepare(d, [command, 'touch good'])
            path = repo / '.crossgate/config.json'
            cfg = json.loads(path.read_text())
            cfg['timeouts'] = limits
            path.write_text(json.dumps(cfg))
            commit(repo, '시간 제한')
            source = FAKE
            if state == 'stalled':
                source = source.replace("echo '{\"type\":\"item.started\"}'",
                                        "printf '%s' \"$prompt\" | grep -q '^실행: sleep' || echo '{\"type\":\"item.started\"}'")
            fake_executable(Path(d) / 'fakebin', 'codex', source)
            result = parallel_cli(repo, 2)
            assert result.returncode == 1 and f'| {state} |' in result.stdout, result.stdout + result.stderr
            assert '| merged |' in result.stdout and (repo / 'good').exists(), result.stdout + result.stderr


def test_parallel_protection_uses_worktree_and_reference():
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['true', 'touch good'])
        todo = run / '01-planning/todo.md'
        todo.write_text('유지할 기획\n')
        (repo / 'q0.md').write_text(f'실행: echo 훼손 > {todo.relative_to(repo)}\n')
        commit(repo, '기획')
        result = parallel_cli(repo, 2)
        assert result.returncode == 1 and '| protected |' in result.stdout, result.stdout + result.stderr
        assert todo.read_text() == '유지할 기획\n'
    with tempfile.TemporaryDirectory() as d:
        repo, run = prepare(d, ['touch good'])
        base = sh('git rev-parse HEAD', repo).stdout.strip()
        path = repo / 'tests/acceptance/check'
        path.parent.mkdir(parents=True)
        path.write_text('보호된 검사\n')
        commit(repo, '보호 경로 변경')
        result = parallel_cli(repo, 1, '--protect-since', base)
        assert result.returncode == 1 and '| protected |' in result.stdout, result.stdout + result.stderr
