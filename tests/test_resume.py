"""이어가기는 임시 실행 기록만 읽고 실제 AI를 호출하지 않는다."""
import datetime as dt
import json
import os
import subprocess
import tempfile
import time
from helpers import P, fresh_repo


def resume(repo, *args):
    env = dict(os.environ)
    env.pop('CROSSGATE_RUN', None)
    return subprocess.run(['python3', '.crossgate/kit/bin/crossgate', 'resume', *args],
                          cwd=repo, env=env, capture_output=True, text=True)


def save_records(run, records, name='verdicts.jsonl'):
    (run / name).write_text(''.join(json.dumps(r) + '\n' for r in records))


def record(stage, verdict, actor='codex', **extra):
    return dict(stage=stage, verdict=verdict, actor=actor, attempt=1, **extra)


def test_resume_next_actions_and_read_only():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = repo / P('init-run 이어가기 --route full', repo).stdout.strip()
        approved = [record('request', 'APPROVED', 'user')]
        cases = [
            ([], '요청 정리(결정 목록, 요청 검수, 한 번에 질문)'),
            ([record('request', 'APPROVED')], '요청 정리('),
            ([record('request', 'ESCALATED')], '사용자 조정 대기'),
            (approved, 'plan: 그 단계 시작 또는 검수 요청'),
            (approved + [record('plan', 'ESCALATED'), record('plan', 'APPROVED')], '사용자 조정 대기'),
            (approved + [record('plan', 'APPROVED'), record('plan', 'READY')],
             'plan: 그 단계 시작 또는 검수 요청'),
            (approved + [record('plan', 'REJECTED', issues={'P-1': 'open', 'P-2': 'resolved'})],
             'plan: 수정 후 재검수 (미해결 P-1)'),
            (approved + [record('plan', 'BLOCKED')], 'plan: 환경 문제를 풀고 같은 회차 재검수'),
            (approved + [record('plan', 'ESCALATED'), record('plan', 'APPROVED', 'user')],
             'dev: 그 단계 시작 또는 검수 요청'),
            (approved + [record(s, 'PASSED' if s == 'qa' else 'APPROVED')
                         for s in ('plan', 'dev', 'qa', 'wiki')], '병합 조건 검사 후 병합'),
            (approved + [record('plan', 'APPROVED'), record('dev', 'APPROVED'), record('qa', 'FAILED')],
             'qa: 수정 후 재검수'),
        ]
        for records, expected in cases:
            save_records(run, records)
            files = [p for p in run.rglob('*') if p.is_file()] + [repo / '.crossgate/current-run']
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
            result = resume(repo, '--run', str(run))
            assert result.returncode == 0, result.stderr
            assert '다음 할 일: ' + expected in result.stdout, result.stdout
            assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
            assert set(files[:-1]) == {p for p in run.rglob('*') if p.is_file()}
        meta = json.loads((run / 'meta.json').read_text())
        meta['closed'] = '2026-01-01T00:00:00'
        (run / 'meta.json').write_text(json.dumps(meta))
        output = resume(repo, '--run', str(run)).stdout
        assert '닫힘' in output and meta['created'] in output and run.name in output
        assert '브랜치 main' in output


def test_resume_interrupted_calls():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = repo / P('init-run 호출 --route light', repo).stdout.strip()
        assert '중단된 호출: 없음' in resume(repo).stdout
        save_records(run, [], 'usage.jsonl')
        assert '중단된 호출: 없음' in resume(repo).stdout
        now = dt.datetime.now(dt.timezone.utc)
        def start(call_id, role, minutes):
            return dict(event='start', id=call_id, role=role, engine='codex',
                        ts=(now - dt.timedelta(minutes=minutes)).isoformat())
        rows = [start('검수중단', 'dev-reviewer', 25), start('개발진행', 'developer', 25),
                start('개발중단', 'developer', 45), start('QA중단', 'qa', 45),
                start('완료호출', 'dev-reviewer', 25), dict(event='end', id='완료호출'),
                start('옛완료', 'dev-reviewer', 25), dict(id='옛완료'),
                start('최근호출', 'dev-reviewer', 1)]
        save_records(run, rows, 'usage.jsonl')
        output = resume(repo).stdout
        for name in ('검수중단', '개발중단', 'QA중단'):
            assert name in output, output
        for name in ('개발진행', '완료호출', '옛완료', '최근호출'):
            assert name not in output, output
        cfgpath = repo / '.crossgate/config.json'
        cfg = json.loads(cfgpath.read_text())
        cfg['timeouts'] = {'call_minutes': {'default': 30, 'developer': 50}}
        cfgpath.write_text(json.dumps(cfg))
        output = resume(repo).stdout
        assert '검수중단' not in output and '개발중단' not in output and 'QA중단' in output
        cfg.pop('timeouts')
        cfgpath.write_text(json.dumps(cfg))
        assert '개발진행' not in resume(repo).stdout


def test_resume_list_and_no_current():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        assert '열린 실행이 없습니다' in resume(repo).stdout
        runs = {}
        now = time.time()
        for name, days in [('최근실행', 1), ('이전실행', 10), ('오래된실행', 20), ('닫힌실행', 0), ('끝난실행', 0)]:
            run = repo / P('init-run ' + name + ' --route standard', repo).stdout.strip()
            runs[name] = run
            if name == '닫힌실행':
                meta = json.loads((run / 'meta.json').read_text())
                meta['closed'] = dt.datetime.now().isoformat()
                (run / 'meta.json').write_text(json.dumps(meta))
            if name == '끝난실행':
                save_records(run, [record('request', 'APPROVED', 'user'), record('dev', 'APPROVED')])
            for p in run.iterdir():
                if p.is_file():
                    os.utime(p, (now - days * 86400,) * 2)
        (repo / '.crossgate/current-run').unlink()
        output = resume(repo).stdout
        assert output == resume(repo, '--list').stdout
        assert output.index('최근실행') < output.index('이전실행')
        assert all(name not in output for name in ('오래된실행', '닫힌실행', '끝난실행'))
        assert '이전실행' not in resume(repo, '--list', '--days', '2').stdout
        assert '오래된실행' in resume(repo, '--list', '--days', '30').stdout
        # 마지막 활동은 생성 시각이 아니라 세 기록 파일의 수정 시각이다.
        (runs['오래된실행'] / 'usage.jsonl').write_text('')
        assert resume(repo, '--list').stdout.index('오래된실행') < resume(repo, '--list').stdout.index('최근실행')
        assert resume(repo, '--list', '--days', '-1').returncode != 0


def test_resume_pm_repositories_and_legacy_route():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        run = repo / P('init-run 저장소', repo).stdout.strip()
        cfgpath = repo / '.crossgate/config.json'
        cfg = json.loads(cfgpath.read_text())
        cfg.update(mode='pm', repos={'가': {'path': '.'}, '나': {'path': '하위'}}, cap=4)
        cfgpath.write_text(json.dumps(cfg))
        (repo / '하위').mkdir()
        fresh_repo(repo / '하위')
        (repo / '하위' / '줄\n바꿈.txt').write_text('미추적')
        meta = json.loads((run / 'meta.json').read_text())
        meta['route'] = 'quick'
        (run / 'meta.json').write_text(json.dumps(meta))
        rows = [record('request', 'APPROVED', 'user'), record('dev', 'APPROVED', repo='가'),
                record('dev', 'REJECTED', repo='나', issues={'D-1': 'open'}),
                record('dev', 'BLOCKED', repo='나')]
        rows[-1]['attempt'] = 2
        save_records(run, rows)
        output = resume(repo).stdout
        assert '등급 standard' in output and 'plan' not in output
        assert 'dev [가]' in output and 'dev [나]' in output and '2/4' in output
        assert 'dev [나]: 환경 문제를 풀고 같은 회차 재검수' in output
        assert '[가] 브랜치 main' in output and '[나] 브랜치 main · 커밋되지 않은 변경 1개' in output
        rows[-1] = record('dev', 'APPROVED', repo='나')
        save_records(run, rows)
        assert '작업공간 규칙대로 마무리하고 실행 닫기' in resume(repo).stdout
