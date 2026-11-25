"""잘못된 입력은 기록 전에 막고, 실행 폴더는 이름·경로 어느 쪽으로도 찾는다."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from helpers import P, fresh_repo


def cli(repo, args, cwd=None, env_run=None):
    env = dict(os.environ)
    env.pop('CROSSGATE_RUN', None)
    if env_run is not None:
        env['CROSSGATE_RUN'] = env_run
    return subprocess.run(['python3', str(repo / '.crossgate/kit/bin/crossgate'), *args],
                          cwd=cwd or repo, env=env, capture_output=True, text=True)


def refused(result, *words):
    assert result.returncode and 'Traceback' not in result.stderr, result.stderr
    assert all(w in result.stderr for w in words), result.stderr


def test_verdict_rejects_bad_values_before_writing():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = P('init-run 입력', repo).stdout.strip()
        ledger = repo / run / 'verdicts.jsonl'
        base = ['verdict', '--actor', 'user', '--run', run]
        # 상태 오타가 열린 지적으로 세어지면 조정 요청이 잘못 뜬다
        refused(cli(repo, base + ['--stage', 'plan', '--verdict', 'REJECTED', '--issues', 'P-1=fixed']), 'P-1=fixed')
        refused(cli(repo, base + ['--stage', 'plan', '--verdict', 'REJECTED', '--issues', 'P-1']), '--issues')
        # 끝남·병합 판정은 qa에 PASSED, 나머지에 APPROVED를 기대한다
        refused(cli(repo, base + ['--stage', 'qa', '--verdict', 'APPROVED']), 'PASSED')
        refused(cli(repo, base + ['--stage', 'dev', '--verdict', 'FAILED']), 'REJECTED')
        assert not ledger.exists()
        ok = cli(repo, base + ['--stage', 'plan', '--verdict', 'REJECTED', '--issues', 'P-1=open, P-2=resolved,'])
        assert ok.returncode == 0, ok.stderr
        assert json.loads(ledger.read_text())['issues'] == {'P-1': 'open', 'P-2': 'resolved'}
        refused(cli(repo, ['init-run', '///']), '실행 이름')


def test_escalation_notice_only_after_rejection():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = P('init-run 조정', repo).stdout.strip()
        def verdict(v):
            return cli(repo, ['verdict', '--stage', 'wiki', '--verdict', v, '--issues', 'W-1=open', '--run', run]).stdout
        assert '조정 필요' not in verdict('REJECTED') + verdict('REJECTED')
        assert '조정 필요' in verdict('REJECTED')
        assert '조정 필요' not in verdict('ESCALATED') + verdict('READY')
        assert '⚠' in cli(repo, ['status', '--run', run]).stdout


def test_run_found_by_name_or_root_relative_path():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = P('init-run 찾기', repo).stdout.strip()
        sub = repo / 'sub'
        sub.mkdir()
        name = Path(run).name
        for value in (run, name, str(repo / run)):
            out = cli(repo, ['status', '--run', value], cwd=sub)
            assert out.returncode == 0 and name in out.stdout, out.stderr
        assert name in cli(repo, ['status'], cwd=sub, env_run=name).stdout
        refused(cli(repo, ['status', '--run', 'ai-log/없음']), '찾을 수 없습니다', 'resume --list')
        twin = repo / 'ai-log/20000101' / name
        twin.mkdir(parents=True)
        (twin / 'meta.json').write_text((repo / run / 'meta.json').read_text())
        refused(cli(repo, ['status', '--run', name]), '여럿', '20000101')
        (repo / '.crossgate/current-run').write_text('ai-log/사라짐')
        refused(cli(repo, ['status']), 'current-run', '사라짐')


def test_broken_ledger_and_config_are_named():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = P('init-run 원장', repo).stdout.strip()
        P('verdict --stage plan --verdict READY', repo)
        with open(repo / run / 'verdicts.jsonl', 'a') as f:
            f.write('<<<<<<< HEAD\n')
        refused(cli(repo, ['status', '--run', run]), 'verdicts.jsonl:2')
        (repo / '.crossgate/config.json').write_text('{"gate": [,]}')
        refused(cli(repo, ['status', '--run', run]), 'config.json', 'JSON')
