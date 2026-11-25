import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from helpers import KIT, P, commit, sh


def tagged_upgrade(d):
    kit = Path(d) / 'kit'
    shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    sh('git init -q -b main && git config user.email t@t && git config user.name t', kit)
    (kit / 'VERSION').write_text('9.9.8\n')
    commit(kit, '이전 킷')
    sh('git tag v9.9.8', kit)
    (kit / 'VERSION').write_text('9.9.9\n')
    commit(kit, '새 킷')
    sh('git tag v9.9.9', kit)
    repo = Path(d) / 'project'
    repo.mkdir()
    sh('git init -q -b main && git config user.email t@t && git config user.name t', repo)
    (repo / 'app.txt').write_text('원래 내용\n')
    (repo / '.gitignore').write_text('private/\n')
    for name in ('AGENTS.md', 'CLAUDE.md'):
        (repo / name).write_text('프로젝트 안내\n')
    commit(repo, '처음')
    sh(f'sh {kit}/install.sh --ref v9.9.8 .', repo)
    commit(repo, '이전 설치')
    sh('git checkout -qb upgrade', repo)
    sh(f'sh {kit}/install.sh --ref v9.9.9 .', repo)
    return kit, repo


def test_infra_pr_conditions():
    with tempfile.TemporaryDirectory() as d:
        kit, template = tagged_upgrade(d)
        cases = ('통과', '범위', '설정', '무시 삭제', '무시 추가', 'AGENTS.md', 'CLAUDE.md',
                 '봉인', '출처', '동일 버전', '낮은 버전', '숫자 상승', '처음 설치', '일반 변경', '표시 훼손', '이름 변경')
        for index, case in enumerate(cases):
            repo = Path(d) / str(index)
            shutil.copytree(template, repo)
            reason = ''
            if case == '이름 변경':
                (repo / 'app.txt').rename(repo / '.crossgate/kit/roles/renamed.md')
                # 봉인을 맞춰 이름 변경의 원래 경로가 검사되는지 확인한다.
                path = repo / '.crossgate/kit/MANIFEST.sha256'
                with path.open('a') as f:
                    digest = hashlib.sha256((repo / '.crossgate/kit/roles/renamed.md').read_bytes()).hexdigest()
                    f.write(f'{digest}  roles/renamed.md\n')
                reason = '조건 1'
            elif case == '숫자 상승':
                sh(f'sh {kit}/install.sh --ref v9.10.0 .', repo)
            elif case == '범위':
                (repo / 'app.txt').write_text('바뀜\n')
                reason = '조건 1'
            elif case == '설정':
                path = repo / '.crossgate/config.json'
                cfg = json.loads(path.read_text()); cfg['forbidden'] = ['x']
                path.write_text(json.dumps(cfg))
                reason = '조건 2'
            elif case.startswith('무시'):
                path = repo / '.gitignore'
                path.write_text(path.read_text().replace('private/\n', '') if case == '무시 삭제'
                                else path.read_text() + 'anything/\n')
                reason = '조건 3'
            elif case in ('AGENTS.md', 'CLAUDE.md', '표시 훼손'):
                path = repo / ('AGENTS.md' if case == '표시 훼손' else case)
                path.write_text(path.read_text().replace('crossgate:end', 'broken:end') if case == '표시 훼손'
                                else path.read_text() + '외부 변경\n')
                reason = '조건 4'
            elif case == '봉인':
                (repo / '.crossgate/kit/roles/qa.md').write_text('바뀜\n')
                reason = '킷 복사본이 원본과 다릅니다'
            elif case == '출처':
                path = repo / '.crossgate/kit/MANIFEST.sha256'
                path.write_text(path.read_text().replace('# source: v9.9.9', '# source: dev'))
                reason = '조건 6'
            elif case in ('동일 버전', '낮은 버전'):
                sh(f'sh {kit}/install.sh --downgrade --ref v9.9.8 .', repo)
                path = repo / '.claude/skills/crossgate/SKILL.md'
                path.write_text(path.read_text() + '\n추가 안내\n')
                if case == '낮은 버전':
                    # 새 태그의 숫자가 문자열 정렬과 다르게 비교되는지도 확인한다.
                    (kit / 'VERSION').write_text('9.10.0\n')
                    commit(kit, '높은 기준'); sh('git tag v9.10.0', kit)
                    sh('git checkout main', repo)
                    sh(f'sh {kit}/install.sh --ref v9.10.0 .', repo)
                    commit(repo, '높은 기준')
                    sh('git checkout upgrade', repo)
                    # 공통 조상 자체를 높은 버전으로 만든 별도 기준을 사용한다.
                    sh('git merge -q --ff-only main', repo)
                    sh(f'sh {kit}/install.sh --downgrade --ref v9.9.9 .', repo)
                reason = '조건 7'
            elif case == '처음 설치':
                reason = '처음 설치'
            elif case == '일반 변경':
                sh(f'sh {kit}/install.sh --downgrade --ref v9.9.8 .', repo)
                (repo / 'app.txt').write_text('일반 변경\n')
                reason = '실행 기록이 0개'
            else:
                for name in ('AGENTS.md', 'CLAUDE.md'):
                    path = repo / name
                    path.write_text(path.read_text().replace('## AI 교차검수 파이프라인', '## 크로스게이트 안내'))
                snippet = (repo / '.crossgate/kit/templates/gitignore-snippet').read_text().splitlines()
                with (repo / '.gitignore').open('a') as f:
                    f.write(snippet[-1] + '\n')
            commit(repo, '변경')
            base = 'main~1' if case == '처음 설치' else 'main'
            result = P(f'merge-check --base {base}', repo, ok=False)
            output = result.stdout + result.stderr
            if case in ('통과', '숫자 상승'):
                version = '9.10.0' if case == '숫자 상승' else '9.9.9'
                assert result.returncode == 0 and f'✓ 인프라 PR(킷 업그레이드 9.9.8 → {version})' in output, output
            else:
                assert result.returncode != 0 and reason in output, (case, output)
