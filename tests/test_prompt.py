"""실제 규칙 문서로 역할별 프롬프트 발췌를 검사한다."""
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

from helpers import KIT


ROLE_RULES = {
    "request-reviewer": ((1, 2, 3, 4, 10), "요청"),
    "plan-reviewer": ((1, 2, 3, 4, 5, 10), "기획"),
    "dev-reviewer": ((1, 2, 3, 4, 5, 6, 10, 11), "개발"),
    "wiki-reviewer": ((1, 2, 3, 4, 5), "Wiki"),
    "developer": ((1, 6, 10, 11), None),
    "qa": ((1, 2, 3, 6, 11, 15), "QA"),
    "auditor": ((1, 2, 3, 4, 5, 6, 10), "감사"),
}


def test_prompt_role_rules():
    from cg import prompt
    rules = (KIT / "CROSSGATE.md").read_text(encoding="utf-8")
    rubric = rules.split("## 3. 루브릭\n", 1)[1].split("\n## ", 1)[0]
    rows = {line.split("|")[1].strip(): line for line in rubric.splitlines()
            if line.startswith("| ") and not line.startswith("| 단계 |")}
    for pm in (False, True):
        for role, (chapters, stage) in ROLE_RULES.items():
            excerpt = prompt.excerpt_rules(rules, role, pm)
            expected = [n for n in chapters if n != 11 or pm]
            assert [int(n) for n in re.findall(r"^## (\d+)\.", excerpt, re.M)] == expected, role
            assert len(excerpt) < len(rules), role
            for name, row in rows.items():
                assert (row in excerpt) == (name == stage), (role, name)
            assert ("### 요청 정리: 결정 목록" in excerpt) == (role in ("request-reviewer", "plan-reviewer", "auditor"))
            assert "기준의 우선순위:" in excerpt
            assert "역할 AI는 이 목록부터 읽고" in excerpt
            assert "검수 AI는 읽기 전용이다" in excerpt
            if role.endswith("-reviewer"):
                assert "## 2. 판정 규칙" in excerpt
                assert "첫 줄은 반드시 `판정: 승인` / `판정: 반려` / `판정: BLOCKED`" in excerpt
            if 6 in chapters:
                assert "`crossgate verdict`만 `verdicts.jsonl`에 쓰고" in excerpt


def test_prompt_missing_sections_use_full_rules():
    from cg.prompt import excerpt_rules
    rules = (KIT / "CROSSGATE.md").read_text(encoding="utf-8")
    for role, (chapters, stage) in ROLE_RULES.items():
        for number in chapters:
            changed = re.sub(rf"^## {number}\. .+$", f"## {number}. 바뀐 제목", rules, flags=re.M)
            assert excerpt_rules(changed, role, True) == changed, (role, number)
        changed = rules.replace("### 요청 정리: 결정 목록", "### 바뀐 제목")
        assert excerpt_rules(changed, role, True) == changed, role
        if stage:
            changed = rules.replace(f"| {stage} | ①", "| 바뀐 단계 | ①")
            assert excerpt_rules(changed, role, True) == changed, role
    assert excerpt_rules(rules, "알 수 없는 역할", False) == rules


def test_prompt_assembly_and_pm_config():
    from cg import prompt
    rules = (KIT / "CROSSGATE.md").read_text(encoding="utf-8")
    task = "## 읽을 것\n\n작업 지시 본문"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for cfg in ({}, {"mode": "pm"}):
            with patch.object(prompt, "root", return_value=root), patch.object(prompt, "config", return_value=cfg):
                for role in ROLE_RULES:
                    result = prompt.build_prompt(role, root / "ai-log/run", task)
                    card = (KIT / "roles" / f"{role}.md").read_text(encoding="utf-8")
                    assert result == "\n\n---\n\n".join([
                        card, prompt.excerpt_rules(rules, role, cfg.get("mode") == "pm"),
                        f"# 이번 작업\n\n실행 기록 폴더: `ai-log/run`\n\n{task}"])
                result = prompt.build_prompt("developer", root / "ai-log/run", task, root / "repo", True)
                assert "작업 저장소: `repo`. 쓰기는 이 저장소 안에서만 된다." in result
                assert "실행 기록 폴더는 쓸 수 없으니" in result
