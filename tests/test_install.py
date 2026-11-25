import json
import shutil
import tempfile
from pathlib import Path
from helpers import KIT, P, commit, fresh_repo, sh


def test_seal_and_tag_install():
    with tempfile.TemporaryDirectory() as d:
        repo = fresh_repo(d)
        assert "봉인 유지" in P("verify", repo).stdout
        assert "# source: dev" in (repo / ".crossgate/kit/MANIFEST.sha256").read_text()
        want = (KIT / "VERSION").read_text().strip()
        assert json.loads((repo / ".crossgate/config.json").read_text())["kit_version"] == want, "설치기가 kit_version을 맞춘다"
        assert "경고: config kit_version" not in P("gate", repo).stderr
        card = repo / ".crossgate/kit/roles/qa.md"
        card.write_text(card.read_text() + "\n몰래 고침\n")
        out = P("verify", repo, ok=False)
        assert out.returncode and "변경됨: roles/qa.md" in out.stderr, out.stderr
        assert "킷 복사본이 원본과 다릅니다" in P("gate", repo, ok=False).stderr
        (repo / ".crossgate/kit/roles/extra.md").write_text("x")
        assert "추가됨: roles/extra.md" in P("verify", repo, ok=False).stderr
        # 태그에서 설치하면 그 태그의 내용과 출처가 봉인된다(작업 트리를 임시 저장소에 커밋·태그해 시험)
        kit = Path(d) / "kitcopy"
        shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns(".git"))
        (kit / "VERSION").write_text("9.9.9\n")
        sh("git init -q -b main && git config user.email t@t && git config user.name t && git add -A && git commit -qm k && git tag v9.9.9", kit)
        (kit / "VERSION").write_text("dirty-not-installed\n")  # 작업 중 변경은 태그 설치에 섞이면 안 된다
        sh(f"sh {kit}/install.sh --ref v9.9.9 .", repo)
        assert (repo / ".crossgate/kit/VERSION").read_text().strip() == "9.9.9"
        assert "# source: v9.9.9" in (repo / ".crossgate/kit/MANIFEST.sha256").read_text()
        assert not (repo / ".crossgate/kit/roles/extra.md").exists()
        # 태그 이름과 그 태그의 VERSION이 어긋난 릴리스는 설치하지 않는다
        sh("git checkout -q -- VERSION && git tag v9.9.10", kit)
        out = sh(f"sh {kit}/install.sh --ref v9.9.10 .", repo, ok=False)
        assert out.returncode and "어긋난" in out.stderr, out.stderr
        # 설치된 것보다 낮은 버전은 밝혀야 깔린다(태그를 받아오지 않은 원본에서 올리다 옛 버전이 깔리지 않게)
        out = sh(f"sh {KIT}/install.sh --dev .", repo, ok=False)
        assert out.returncode and "낮은 버전" in out.stderr and "fetch --tags" in out.stderr, out.stderr
        assert (repo / ".crossgate/kit/VERSION").read_text().strip() == "9.9.9", "거부한 설치는 킷을 건드리지 않는다"
        assert "봉인 유지" in P("verify", repo).stdout
        sh(f"sh {KIT}/install.sh --dev --downgrade .", repo)
        assert (repo / ".crossgate/kit/VERSION").read_text().strip() == (KIT / "VERSION").read_text().strip()


def test_release_tag_changelog_and_version_agree():
    """원본의 최신 태그는 그 태그의 VERSION과 같고, 작업 중 VERSION은 그보다 낮지 않으며 변경 이력 맨 위와 같다."""
    version = (KIT / "VERSION").read_text().strip()
    top = next(line for line in (KIT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines() if line.startswith("## "))
    assert top.split()[1] == version, f"CHANGELOG 맨 위({top})와 VERSION({version})이 다르다"
    tags = sh("git tag --sort=-v:refname", KIT, ok=False).stdout.split() if (KIT / ".git").exists() else []
    if not tags:
        return  # 태그 없는 복사본(압축본, 얕은 clone)
    latest = tags[0]
    assert sh(f"git show {latest}:VERSION", KIT).stdout.strip() == latest.lstrip("v"), f"{latest} 의 VERSION이 태그와 다르다"
    number = lambda v: tuple(map(int, v.split(".")))
    assert number(version) >= number(latest.lstrip("v")), f"VERSION {version} 이 최신 태그 {latest} 보다 낮다"


def test_migrate_from_0_1():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / "proj"
        repo.mkdir()
        sh("git init -q -b main && git config user.email t@t && git config user.name t", repo)
        (repo / "CLAUDE.md").write_text("# 프로젝트\n\n배포는 `pipeline deploy`로 한다.\n")
        (repo / ".github/workflows").mkdir(parents=True)
        (repo / ".github/workflows/pipeline-check.yml").write_text("run: python3 .pipeline/kit/bin/pipeline merge-check\n")
        commit(repo, "init")
        old = Path(d) / "old"
        old.mkdir()
        sh(f"git -C {KIT} archive v0.1.3 | tar -x -C {old}", repo)
        sh(f"sh {old}/install.sh --dev .", repo)
        cfg = json.loads((repo / ".pipeline/config.json").read_text())
        cfg["gate"] = [{"name": "mine", "run": "true"}]
        (repo / ".pipeline/config.json").write_text(json.dumps(cfg))
        (repo / ".pipeline/current-run").write_text("ai-log/x")
        sh(f"sh {KIT}/install.sh --dev .", repo)
        assert not (repo / ".pipeline").exists()
        new_cfg = json.loads((repo / ".crossgate/config.json").read_text())
        assert new_cfg["gate"] == [{"name": "mine", "run": "true"}], "프로젝트 설정 보존"
        assert ".crossgate/*" in new_cfg["protected"] and not any(".pipeline" in x for x in new_cfg["protected"])
        assert (repo / ".crossgate/current-run").read_text() == "ai-log/x"
        assert not (repo / ".claude/skills/pipeline").exists() and not (repo / ".claude/agents/pipeline-reviewer.md").exists()
        assert (repo / ".claude/skills/crossgate/SKILL.md").exists()
        for name in ("AGENTS.md", "CLAUDE.md"):
            t = (repo / name).read_text()
            assert "ai-pipeline-kit" not in t and t.count("crossgate:start") == 1, name
        assert "`crossgate deploy`" in (repo / "CLAUDE.md").read_text()
        gi = (repo / ".gitignore").read_text()
        assert ".crossgate/current-run" in gi and ".pipeline" not in gi
        assert ".crossgate/kit/bin/crossgate" in (repo / ".github/workflows/pipeline-check.yml").read_text()
        assert "봉인 유지" in P("verify", repo).stdout


def test_setup_skill_and_kit_source():
    with tempfile.TemporaryDirectory() as d:
        home = Path(d) / "home"
        home.mkdir()
        sh(f"HOME={home} sh {KIT}/install.sh --global", Path(d))
        skill = (home / ".claude/skills/crossgate-setup/SKILL.md").read_text()
        assert "{{KIT}}" not in skill and str(KIT) in skill and "name: crossgate-setup" in skill
        repo = fresh_repo(d)
        cfg = json.loads((repo / ".crossgate/config.json").read_text())
        assert "kit_source" not in cfg and "kit_remote" not in cfg, "개인 경로는 커밋 대상 설정에 넣지 않는다"
        assert json.loads((repo / ".crossgate/local.json").read_text())["kit_source"] == str(KIT)
        assert ".obsidian/" in (repo / ".gitignore").read_text().splitlines(), "편집기 설정 폴더는 git에서 뺀다"
        assert sh("git check-ignore -q .crossgate/local.json && echo ignored", repo).stdout.strip() == "ignored"
        # 0.2.1이 config.json에 넣은 값은 재설치 때 local.json으로 옮겨진다
        cfg["kit_source"] = "/old/path"; cfg["kit_remote"] = "x"
        (repo / ".crossgate/config.json").write_text(json.dumps(cfg))
        sh(f"sh {KIT}/install.sh --dev .", repo)
        cfg2 = json.loads((repo / ".crossgate/config.json").read_text())
        assert "kit_source" not in cfg2 and "kit_remote" not in cfg2
        assert (repo / ".claude/skills/crossgate/SKILL.md").read_text().count("## 8. 업그레이드") == 1

