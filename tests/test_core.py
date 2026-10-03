


def test_mask():
    from cg import core as mod
    assert mod.mask("token=abcdef123456") == "token=***"
    assert mod.mask('"api_key": "zzzzzzzz"') == '"api_key": "***"'
    assert mod.mask("Authorization: Bearer abcdefghij") == "Authorization: Bearer ***"
    assert mod.mask("ghp_" + "a" * 30) == "***"
    assert mod.mask("평범한 문장 token 없음") == "평범한 문장 token 없음"

