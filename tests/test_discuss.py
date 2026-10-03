"""토의 CLI는 임시 설치본과 가짜 실행 파일만 사용한다."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from helpers import KIT, fake_executable


def setup(directory, discussion=None):
    base = Path(directory).resolve()
    kit = base / '.crossgate/kit'
    kit.mkdir(parents=True)
    for name in ('bin', 'roles', 'templates'):
        shutil.copytree(KIT / name, kit / name)
    for name in ('VERSION', 'CROSSGATE.md'):
        shutil.copy(KIT / name, kit / name)
    cfg = {'kit_version': (KIT / 'VERSION').read_text().strip(), 'mode': 'pm',
           'timeouts': {'call_minutes': {'default': 0.1}, 'stall_seconds': 3}}
    if discussion is not None:
        cfg['discussion'] = discussion
    (base / '.crossgate/config.json').write_text(json.dumps(cfg))
    run = base / 'ai-log/day/run'
    (run / 'raw').mkdir(parents=True)
    (run / 'meta.json').write_text(json.dumps({'route': 'full', 'created': '2026-01-01T00:00:00'}))
    (base / '.crossgate/current-run').write_text(str(run.relative_to(base)))
    (base / 'topic.md').write_text('질문과 배경')
    (base / 'q.md').write_text('## 읽을 것\n쟁점을 확인한다.')
    (base / 'decision.md').write_text('최종 선택과 이유')
    for engine in ('codex', 'claude'):
        fake_executable(base / 'fakebin', engine, f'''#!{sys.executable}
import json, os, pathlib, sys, time, uuid
base = pathlib.Path({str(base)!r})
engine = {engine!r}
args = sys.argv[1:]
prompt = sys.stdin.read()
model_flag = '-m' if engine == 'codex' else '--model'
model = args[args.index(model_flag) + 1]
resumed = 'resume' in args or '--resume' in args
session = args[args.index('resume' if engine == 'codex' else '--resume') + 1] if resumed else engine + '-' + uuid.uuid4().hex
entry = {{'engine': engine, 'args': args, 'prompt': prompt, 'session': session, 'model': model}}
with (base / 'calls.jsonl').open('a') as f:
    f.write(json.dumps(entry) + '\\n')
if (base / 'barrier').exists():
    (base / (engine + '.started')).touch()
    other = 'claude' if engine == 'codex' else 'codex'
    deadline = time.monotonic() + 2
    while not (base / (other + '.started')).exists():
        if time.monotonic() > deadline:
            sys.exit(9)
        time.sleep(0.01)
if (base / ('fail-' + engine)).exists() or (resumed and (base / 'fail-resume').exists()):
    sys.exit(7)
if (base / ('timeout-' + engine)).exists():
    time.sleep(2)
reply = '근거 token=abcdefghi'
if engine == 'codex':
    pathlib.Path(args[args.index('-o') + 1]).write_text(reply)
    print(json.dumps({{'type': 'thread.started', 'thread_id': session}}))
    print(json.dumps({{'type': 'item.completed'}}))
else:
    print(json.dumps({{'result': reply, 'session_id': session}}))
''')
    return base, run, cfg


def cli(base, *args, code=0):
    result = subprocess.run([sys.executable, '.crossgate/kit/bin/crossgate', *args], cwd=base,
                            env={**os.environ, 'PATH': str(base / 'fakebin') + ':' + os.environ['PATH'],
                                 'CROSSGATE_RUN': ''}, capture_output=True, text=True, timeout=15)
    assert result.returncode == code, (args, result.stdout, result.stderr)
    return result.stdout + result.stderr


def opened(base, slug='choice'):
    return base / cli(base, 'discuss', 'open', slug, '--topic-file', 'topic.md').strip()


def round_(base, *args, code=0):
    return cli(base, 'discuss', 'round', 'choice', '--prompt-file', 'q.md', *args, code=code)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_discuss_open_and_independent_round():
    with tempfile.TemporaryDirectory() as d:
        base, run, cfg = setup(d)
        folder = opened(base, '../choice !!')
        assert folder.name == '01_choice'
        assert (folder / 'topic.md').read_text() == '질문과 배경'
        meta = json.loads((folder / 'meta.json').read_text())
        assert meta['status'] == 'open' and meta['created']
        assert [p['name'] for p in meta['participants']] == ['codex', 'claude']
        round_(base, '--only', 'codex', code=1)
        round_(base, '--sequential', code=1)
        (base / 'barrier').touch()
        round_(base)
        for call in rows(base / 'calls.jsonl'):
            assert 'resume' not in call['args'] and '--resume' not in call['args']
            assert str(folder / 'topic.md') in call['prompt']
            assert '다른 참가자의 발언을 읽지 않는다' in call['prompt']
            assert 'R1_' not in call['prompt']
        assert len(list((folder / 'turns').glob('R1_*.md'))) == 2
        assert all('abcdefghi' not in p.read_text() for p in (folder / 'turns').iterdir())
        ends = [r for r in rows(run / 'usage.jsonl') if r['event'] == 'end']
        assert all(r['role'] == 'discussant' and r['discussion'] == folder.name and r['round'] == 1 for r in ends)
        assert {r['participant'] for r in ends} == {'codex', 'claude'}
        assert len({r['session_key'] for r in ends}) == 2


def test_discuss_resume_scope_sequential_and_fallback():
    with tempfile.TemporaryDirectory() as d:
        base, run, cfg = setup(d)
        for engine in ('codex', 'claude'):
            cli(base, engine, 'dev-reviewer', '--prompt-file', 'q.md')
        folder = opened(base)
        round_(base)
        first = {r['engine']: r for r in rows(base / 'calls.jsonl')[-2:]}
        assert all('resume' not in r['args'] and '--resume' not in r['args'] for r in first.values())
        cli(base, 'discuss', 'issue', 'choice', '--id', 'T-1', '--status', 'open', '--title', '대안')
        round_(base, '--sequential')
        for call in rows(base / 'calls.jsonl')[-2:]:
            engine = call['engine']
            assert first[engine]['session'] in call['args']
            assert 'issues.md' in call['prompt'] and 'T-1' in call['prompt']
            other = 'claude' if engine == 'codex' else 'codex'
            assert str(folder / f'turns/R1_{other}.md') in call['prompt']
            if engine == 'claude':
                assert str(folder / 'turns/R2_codex.md') in call['prompt']
                assert 'Read,Grep,Glob' in call['args']
            else:
                assert call['args'][:3] == ['exec', 'resume', first[engine]['session']]
                assert 'sandbox_mode="read-only"' in call['args']
        (base / 'fail-resume').touch()
        round_(base)
        calls = rows(base / 'calls.jsonl')
        for call in calls[-4:]:
            assert str(folder / 'topic.md') in call['prompt']
            if 'resume' not in call['args'] and '--resume' not in call['args']:
                assert str(folder / f"turns/R1_{call['engine']}.md") in call['prompt']
        (base / 'fail-resume').unlink()
        reviews = rows(base / 'calls.jsonl')[:2]
        # 역할까지 같은 기록이어도 토의 묶음 키가 있으면 검수가 건너뛴다.
        with (run / 'usage.jsonl').open('a') as handle:
            for row in rows(run / 'usage.jsonl')[:4]:
                if row['event'] == 'end':
                    handle.write(json.dumps({**row, 'session_key': 'other:participant',
                                             'session': 'must-not-resume'}) + '\n')
        for engine in ('codex', 'claude'):
            cli(base, engine, 'dev-reviewer', '--prompt-file', 'q.md')
            assert next(r['session'] for r in reviews if r['engine'] == engine) in rows(base / 'calls.jsonl')[-1]['args']


def test_discuss_failure_retry_and_cap():
    with tempfile.TemporaryDirectory() as d:
        base, run, cfg = setup(d, {'rounds': 1})
        folder = opened(base)
        (base / 'fail-claude').touch()
        assert 'claude' in round_(base, code=1)
        saved = (folder / 'turns/R1_codex.md').read_text()
        assert not (folder / 'turns/R1_claude.md').exists()
        (base / 'fail-claude').unlink()
        round_(base, '--only', 'claude')
        assert (folder / 'turns/R1_codex.md').read_text() == saved
        assert not list((folder / 'turns').glob('R2*'))
        round_(base)
        assert '실험하거나 사용자에게 결정을 요청' in round_(base, code=1)
        round_(base, '--extend')
        assert (folder / 'turns/R3_codex.md').exists()
        round_(base, '--only', 'unknown', '--extend', code=1)
        (base / 'fail-claude').touch()
        round_(base, '--extend', '--sequential', code=1)
        (base / 'fail-claude').unlink()
        round_(base, '--only', 'claude', '--sequential')
        last = rows(base / 'calls.jsonl')[-1]
        assert str(folder / 'turns/R4_codex.md') in last['prompt']
        assert (folder / 'turns/R4_claude.md').exists()
        assert not list((folder / 'turns').glob('R5*'))


def test_discuss_issues_close_and_status():
    with tempfile.TemporaryDirectory() as d:
        base, run, cfg = setup(d)
        folder = opened(base)
        issue = ('discuss', 'issue', 'choice', '--id', 'T-1')
        cli(base, *issue, '--status', 'open', code=1)
        cli(base, *issue, '--status', 'open', '--title', '대안 | 확인')
        cli(base, *issue, '--status', 'experiment', '--note', '확인', '--evidence', 'q.md', 'topic.md')
        assert len(rows(folder / 'issues.jsonl')) == 2
        assert 'experiment' in (folder / 'issues.md').read_text()
        assert 'q.md' in (folder / 'issues.md').read_text()
        for command in ('status', 'resume'):
            text = cli(base, command)
            assert '열린 토의' in text and '01_choice' in text and '쟁점 1' in text
        text = cli(base, 'discuss', 'status')
        assert '01_choice' in text and 'experiment' in text and 'codex' in text
        close = ('discuss', 'close', 'choice', '--decision-file', 'decision.md')
        cli(base, *close, code=1)
        for state in ('agreed', 'rejected', 'deferred', 'user'):
            cli(base, *issue, '--status', state)
            assert state in (folder / 'issues.md').read_text()
        assert len(rows(folder / 'issues.jsonl')) == 6
        assert '사용자에게 물을 쟁점' in cli(base, *close)
        assert (folder / 'decision.md').read_text() == '최종 선택과 이유'
        round_(base, code=1)
        cli(base, *issue, '--status', 'agreed', code=1)
        opened(base, 'forced')
        cli(base, 'discuss', 'issue', 'forced', '--id', 'T-1', '--status', 'open', '--title', '확인')
        cli(base, 'discuss', 'close', 'forced', '--decision-file', 'decision.md', '--force')
        opened(base, 'pending')
        cli(base, 'close-run')
        cli(base, 'discuss', 'round', 'pending', '--prompt-file', 'q.md', code=1)
        cli(base, 'discuss', 'issue', 'pending', '--id', 'T-1', '--status', 'open', '--title', '확인', code=1)
        cli(base, 'discuss', 'open', 'new', '--topic-file', 'topic.md', code=1)


def test_discuss_models_validation_and_timeout():
    with tempfile.TemporaryDirectory() as d:
        participants = [{'name': 'one', 'engine': 'codex', 'model': 'model-one', 'effort': 'low'},
                        {'name': 'two', 'engine': 'codex', 'model': 'model-two', 'effort': 'high'}]
        base, run, cfg = setup(d, {'participants': participants})
        folder = opened(base)
        assert '경고: --effort' not in round_(base)
        ends = [r for r in rows(run / 'usage.jsonl') if r['event'] == 'end']
        assert {r['effort'] for r in ends} == {'low', 'high'}
        assert all(r['effort_source'] == 'participant' for r in ends)
        first = {r['model']: r for r in rows(base / 'calls.jsonl')}
        cfg['resume_reviews'] = False
        cfg['codex'] = {'ephemeral': True}
        (base / '.crossgate/config.json').write_text(json.dumps(cfg))
        round_(base)
        for call in rows(base / 'calls.jsonl')[-2:]:
            assert first[call['model']]['session'] in call['args']
        round_(base, '--effort', 'high')
        ends = [r for r in rows(run / 'usage.jsonl') if r['event'] == 'end']
        assert all(r['effort'] == 'high' and r['effort_source'] == 'flag' for r in ends[-2:])
        assert all('--ephemeral' not in r['args'] for r in rows(base / 'calls.jsonl'))
        for bad in ([{'name': '../bad', 'engine': 'codex'}], participants + participants,
                    [{'name': 'ok', 'engine': 'other'}], [], 'wrong'):
            cfg['discussion'] = {'participants': bad}
            (base / '.crossgate/config.json').write_text(json.dumps(cfg))
            assert 'discussion' in cli(base, 'discuss', 'open', 'bad', '--topic-file', 'topic.md', code=1)
    with tempfile.TemporaryDirectory() as d:
        base, run, cfg = setup(d)
        cfg['timeouts']['call_minutes']['discussant'] = 0.015
        (base / '.crossgate/config.json').write_text(json.dumps(cfg))
        folder = opened(base)
        (base / 'timeout-claude').touch()
        assert 'claude' in round_(base, code=3)
        assert (folder / 'turns/R1_codex.md').exists()


def test_discuss_merge_check_open_blocks():
    from cg.commands import merge_check
    with tempfile.TemporaryDirectory() as d:
        base, run, cfg = setup(d)
        opened(base)
        cfg = {'routes': {'full': {'stages': []}}}
        def fake_git(*args, **kwargs):
            return 'base' if args[0] == 'merge-base' else 'ai-log/day/run/meta.json'
        output = io.StringIO()
        with patch.object(merge_check, 'require_sealed'), patch.object(merge_check, 'config', return_value=cfg), \
                patch.object(merge_check, 'root', return_value=base), patch.object(merge_check, 'git', side_effect=fake_git), \
                contextlib.redirect_stdout(output):
            try:
                merge_check.cmd_merge_check(SimpleNamespace(base='main'))
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError('열린 토의를 통과시킴')
        assert '열린 토의' in output.getvalue()


def test_discuss_excerpt_and_defaults():
    from cg.prompt import excerpt_rules
    from cg.discussion import options
    excerpt = excerpt_rules((KIT / 'CROSSGATE.md').read_text(), 'discussant')
    assert '## 14. 토의' in excerpt and '## 1. 단계와 역할' in excerpt
    assert '## 4. 검수 응답 형식' not in excerpt
    assert options({})[0] == 3
    for invalid in (-1, True, '3', None):
        try:
            options({'discussion': {'rounds': invalid}})
        except ValueError as error:
            assert 'discussion.rounds' in str(error)
        else:
            raise AssertionError('잘못된 상한을 허용함')
