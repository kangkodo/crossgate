from cg.core import KIT, root


def build_prompt(role, run, task, repo=None, writing=False):
    """역할 카드와 규칙 전체, 이번 작업을 기존 순서로 조립한다."""
    r = root()
    prompt = "\n\n---\n\n".join([
        (KIT / "roles" / f"{role}.md").read_text(encoding="utf-8"),
        (KIT / "CROSSGATE.md").read_text(encoding="utf-8"),
        f"# 이번 작업\n\n실행 기록 폴더: `{run.relative_to(r)}`\n\n{task}"])
    if repo and writing:
        prompt += (f"\n\n---\n\n작업 저장소: `{repo.relative_to(r)}`. 쓰기는 이 저장소 안에서만 된다. "
                   "실행 기록 폴더는 쓸 수 없으니 todo.md 체크 대신 항목별 완료 근거를 답변에 적는다.")
    return prompt
