


def test_mask():
    from cg import core as mod
    assert mod.mask("token=abcdef123456") == "token=***"
    assert mod.mask('"api_key": "zzzzzzzz"') == '"api_key": "***"'
    assert mod.mask("Authorization: Bearer abcdefghij") == "Authorization: Bearer ***"
    assert mod.mask("ghp_" + "a" * 30) == "***"
    assert mod.mask("평범한 문장 token 없음") == "평범한 문장 token 없음"
    # 주소 속 비밀번호(scheme://user:pass@host)
    assert mod.mask("DATABASE_URL=postgres://app:s3cr3t@db.internal:5432/main") == "DATABASE_URL=postgres://app:***@db.internal:5432/main"
    assert mod.mask("redis://:pw@cache/0 그리고 https://u:p%40ss@example.com/x") == "redis://:***@cache/0 그리고 https://u:***@example.com/x"
    for plain in ("http://localhost:3000/a@b", "https://example.com:8443/path", "ssh://git@github.com/o/r.git", "시각 12:30@회의실"):
        assert mod.mask(plain) == plain, plain

