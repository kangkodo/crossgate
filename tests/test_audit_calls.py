"""실제 세션 기록을 열지 않고 밖 호출 찾기를 검사한다."""
import datetime as dt
import io
import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from helpers import P, fresh_repo


def session(home, when, name, cwd, model, source='exec'):
    path = home / 'sessions' / when.strftime('%Y/%m/%d') / f'rollout-{when:%Y-%m-%dT%H-%M-%S}-{name}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{'type': 'session_meta', 'timestamp': '2000-01-01T00:00:00Z',
             'payload': {'cwd': str(cwd), 'source': source, 'originator': '검사'}},
            {'type': 'turn_context', 'payload': {'model': model, 'effort': 'high'}}]
    # 대화 줄은 JSON 파싱조차 하지 않아야 한다. 메타 앞뒤에 있어도 결과에 영향이 없다.
    path.write_text(json.dumps(rows[0]) + '\n{"type":"response_item","payload":대화는 읽지 않음}\n'
                    + json.dumps(rows[1]) + '\n{"type":"response_item","payload":"비공개 대화"}\n')
    return path


def cli(repo, home, *args):
    env = {**os.environ, 'CODEX_HOME': str(home), 'TZ': 'Asia/Seoul'}
    env.pop('CROSSGATE_RUN', None)
    return subprocess.run(['python3', '.crossgate/kit/bin/crossgate', 'audit-calls', *args],
                          cwd=repo, env=env, capture_output=True, text=True)


def snapshot(repo):
    return {p.relative_to(repo): p.read_bytes() for p in repo.rglob('*') if p.is_file()}


def test_audit_classification_and_raw_save():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d).resolve()
        run = repo / P('init-run 검사', repo).stdout.strip()
        home = repo / '가짜세션'
        when = dt.datetime(2026, 1, 10, 12)
        for name, offset, model, source, cwd in [
            ('시작', 0, '시작모델', 'exec', repo),
            ('옛호출', 120, '옛모델', 'exec', repo / '하위'),
            ('밖', 240, '밖모델', 'exec', repo),
            ('대화', 360, '대화모델', 'cli', repo),
            ('보조', 480, '검사-auto-review', 'exec', repo),
            ('다른폴더', 600, '제외모델', 'exec', Path(str(repo) + '-다른폴더')),
            ('다른모델', 0, '다른모델', 'exec', repo),
            ('오차초과', 31, '시작모델', 'exec', repo),
            ('미완료', 720, '미완료모델', 'exec', repo),
        ]:
            session(home, when + dt.timedelta(seconds=offset), name, cwd, model, source)
        # 다른 실행의 기록도 읽고, 끝 시각에서 시간을 빼는 것은 시작 기록이 없을 때뿐이다.
        other = repo / 'ai-log/20260101/옛실행'
        other.mkdir(parents=True)
        records = [
            {'id': 'a', 'event': 'start', 'ts': '2026-01-10T03:00:00Z', 'model': '시작모델'},
            {'id': 'a', 'event': 'end', 'ts': '2026-01-10T03:10:00Z', 'seconds': 300, 'model': '시작모델'},
            {'ts': '2026-01-10T12:03:00', 'seconds': 30, 'model': '옛모델'},
            {'ts': '2026-01-10T03:04:00Z', 'seconds': 0, 'model': '밖모델', 'engine': 'claude'},
            {'id': 'b', 'event': 'start', 'ts': '2026-01-10T03:12:00Z', 'model': '미완료모델'},
        ]
        (other / 'usage.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
        (run / 'usage.jsonl').write_text('')
        session(home, when, '형식불명', repo, '모델').write_text('{}\n')
        (home / 'sessions/알수없는파일.jsonl').write_text('{}\n')
        before = snapshot(repo)
        p = cli(repo, home, '--since', '2026-01-10', '--days', '1')
        assert p.returncode == 0, p.stderr
        for text in ('크로스게이트 호출: 3', '크로스게이트 밖 호출: 3', '대화형 세션: 1', '보조 세션: 제외', '건너뛴 세션 파일: 2', 'high'):
            assert text in p.stdout, p.stdout
        assert '제외모델' not in p.stdout and '비공개 대화' not in p.stdout
        assert snapshot(repo) == before
        p = cli(repo, home, '--since', '2026-01-10', '--run', str(run), '--save')
        assert p.returncode == 0, p.stderr
        after = snapshot(repo)
        changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
        assert len(changed) == 1 and next(iter(changed)).parent == run.relative_to(repo) / 'raw'
        assert '크로스게이트 밖 호출: 3' in after[next(iter(changed))].decode()
        assert '세션을 남기지' in cli(repo, home, '--help').stdout


def test_audit_period_defaults_and_unavailable():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d).resolve()
        home = repo / '가짜세션'
        p = cli(repo, home)
        assert p.returncode == 0 and '확인할 수 없음' in p.stdout
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).replace(tzinfo=None)
        for days in (1, 5, 8):
            session(home, now - dt.timedelta(days=days), str(days), repo, f'모델{days}')
        p = cli(repo, home)
        assert '크로스게이트 밖 호출: 2' in p.stdout, p.stdout
        run = repo / P('init-run 기간', repo).stdout.strip()
        meta = json.loads((run / 'meta.json').read_text())
        meta['created'] = (now - dt.timedelta(days=2)).isoformat() + '+09:00'
        (run / 'meta.json').write_text(json.dumps(meta))
        assert '크로스게이트 밖 호출: 1' in cli(repo, home).stdout
        assert '크로스게이트 밖 호출: 3' in cli(repo, home, '--days', '10').stdout
        (repo / '.crossgate/current-run').unlink()
        before = snapshot(repo)
        p = cli(repo, home, '--save')
        assert p.returncode == 0 and '저장할 실행이 없습니다' in p.stdout
        assert snapshot(repo) == before
        assert cli(repo, home, '--since', '잘못된날짜').returncode == 0
        assert cli(repo, home, '--days', '-1').returncode == 0


def test_audit_unreadable_sessions():
    from cg.commands import audit_calls
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        args = SimpleNamespace(run=None, since=None, days=None, save=False)
        with patch.object(audit_calls, 'root', return_value=base), \
                patch.dict(os.environ, {'CODEX_HOME': d, 'CROSSGATE_RUN': ''}), \
                patch.object(audit_calls.os, 'walk', side_effect=PermissionError('접근 거부')), \
                patch('sys.stdout', new_callable=io.StringIO) as out:
            (base / 'sessions').mkdir()
            audit_calls.cmd_audit_calls(args)
        assert '확인할 수 없음' in out.getvalue()
        assert '크로스게이트 밖 호출: 0' not in out.getvalue()
