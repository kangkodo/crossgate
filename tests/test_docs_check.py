"""문서 점검은 임시 설치 폴더만 읽고 실제 AI를 호출하지 않는다."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from helpers import KIT


def setup_docs(directory, files, docs=None):
    root = Path(directory)
    shutil.copytree(KIT / "bin", root / ".crossgate/kit/bin")
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    if docs is not None:
        (root / ".crossgate/config.json").write_text(json.dumps({"docs": docs}))
    return root


def check_docs(root, *args, cwd=None):
    return subprocess.run(
        [sys.executable, str(root / ".crossgate/kit/bin/crossgate"), "docs-check", *args],
        cwd=cwd or root, capture_output=True, text=True)


def test_docs_check_four_kinds_and_read_only():
    with tempfile.TemporaryDirectory() as d:
        root = setup_docs(d, {
            "docs/INDEX.md": "[안내](guide.md)\n[없음](gone.md)\n",
            "docs/guide.md": "# 안내\n[깨짐](missing.txt)\n[앵커](#없음)\n[[위키]]\n",
            "docs/orphan.md": "# 누락\n",
        })
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
        result = check_docs(root)
        assert result.returncode == 1, result.stderr
        for line in (
            "docs/INDEX.md:2: 목차 불일치 → gone.md",
            "docs/orphan.md:1: 목차 불일치",
            "docs/guide.md:2: 깨진 링크 → missing.txt",
            "docs/guide.md:3: 없는 앵커 → #없음",
            "docs/guide.md:4: 위키링크 → [[위키]]",
        ):
            assert line in result.stdout, result.stdout
        assert result.stdout.splitlines()[-1] == "검사한 링크: 4개, 문제: 6개"
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
        assert before == after


def test_docs_check_paths_anchors_and_images():
    with tempfile.TemporaryDirectory() as d:
        root = setup_docs(d, {
            "docs/INDEX.md": '[안내](<하위/사용 안내.md> "제목")\n',
            "docs/하위/사용 안내.md": (
                '# 한글 제목!\n# 한글 제목!\n# A  B_C-D\n'
                '다른 제목\n---\n<a id="직접"></a><a name=legacy></a>\n'
                '[하나](#한글-제목) [둘](#한글-제목-1) [셋](#a--b_c-d)\n'
                '[직접](#직접) [이전](#legacy) [제목](#다른-제목)\n'
                '[밖](../../outside.md#바깥)\n'
                '[경로](사용%20안내.md#%ED%95%9C%EA%B8%80-%EC%A0%9C%EB%AA%A9)\n'
                '![그림](../../image(1).png "설명")\n'
                '[외부](https://example.invalid/no) [메일](mailto:a@example.invalid)\n'
                '[전화](tel:123) [외부](http://example.invalid)\n'
            ),
            "outside.md": "# 바깥\n", "image(1).png": "이미지",
        })
        result = check_docs(root)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines()[-1] == "검사한 링크: 10개, 문제: 0개"
        (root / "image(1).png").unlink()
        result = check_docs(root)
        assert result.returncode == 1 and "깨진 링크 → ../../image(1).png" in result.stdout


def test_docs_check_ignores_code_and_references():
    with tempfile.TemporaryDirectory() as d:
        root = setup_docs(d, {"docs/INDEX.md": (
            '```md\n[[위키]] [깨짐](no.md)\n```\n'
            '~~~~\n![그림](no.png)\n~~~\n[[여전히 코드]]\n~~~~\n'
            '    [들여쓰기](no.md)\n\n'
            '`[[코드]]` ``[코드](no.md) `안쪽` ``\n'
            '`여러 줄\n[코드](no.md)`\n'
            '[참조][이름]\n[이름]: no.md\n'
            '[실제](missing.md)\n'
        )})
        result = check_docs(root)
        assert result.returncode == 1, result.stderr
        assert "docs/INDEX.md:16: 깨진 링크 → missing.md" in result.stdout, result.stdout
        assert result.stdout.splitlines()[-1] == "검사한 링크: 1개, 문제: 2개"
        assert "참조형 링크" in check_docs(root, "--help").stdout


def test_docs_check_defaults_missing_and_require_index():
    with tempfile.TemporaryDirectory() as d:
        root = setup_docs(d, {})
        result = check_docs(root, "--require-index")
        assert result.returncode == 0 and "문서 폴더 없음" in result.stdout
        assert result.stdout.splitlines()[-1] == "검사한 링크: 0개, 문제: 0개"
        (root / "docs").mkdir()
        result = check_docs(root)
        assert result.returncode == 0 and "목차 없음" in result.stdout
        result = check_docs(root, "--require-index")
        assert result.returncode == 1 and "목차 없음" in result.stdout
        assert result.stdout.splitlines()[-1] == "검사한 링크: 0개, 문제: 1개"


def test_docs_check_config_precedence_exclude_and_install_root():
    with tempfile.TemporaryDirectory() as d:
        root = setup_docs(d, {
            "manual/목차.md": "[안내](nested/guide.md)\n",
            "manual/nested/guide.md": "# 안내\n",
            "manual/skip/bad.md": "[[제외]]\n",
            "manual/nested/draft.md": "[[제외]]\n",
            "chosen/목차.md": "# 별도 문서\n",
        }, {"root": "manual", "index": "목차.md", "exclude": ["skip/**", "**/draft.md"]})
        result = check_docs(root, cwd=root / "manual/nested")
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines()[-1] == "검사한 링크: 1개, 문제: 0개"
        result = check_docs(root, "chosen", cwd=root / "manual/nested")
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines()[-1] == "검사한 링크: 0개, 문제: 0개"
