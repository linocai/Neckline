"""Literal lookup preserves the former escaped-regex matching contract."""
import itertools
import re

from neckline.k10.v2_profiles import matches_term


def former_match(term, text):
    if not isinstance(term, str) or not term:
        return False
    term, text = term.casefold(), text.casefold()
    left = r'(?<![a-z0-9_])' if re.match(r'[a-z0-9_]', term) else ''
    right = r'(?![a-z0-9_])' if re.search(r'[a-z0-9_]$', term) else ''
    return re.search(left + re.escape(term) + right, text) is not None


def test_literal_matching_preserves_boundaries_and_later_occurrences():
    terms = ['', None, 'AI', 'a', '_a', 'a_', '000001.SZ', 'C++', 'A/B',
             '芯片', 'A股', '股A', 'Straße', 'İ', 'ς', '[x]', '$', '\n', 'a\n', 'a\n\n']
    edges = ['', 'a', '0', '_', '-', '/', ' ', '中', 'é', '\n']
    for term, left, right in itertools.product(terms, edges, edges):
        value = term if isinstance(term, str) else ''
        # A rejected first occurrence cannot hide a later bounded occurrence.
        texts = [left + value + right, left + value + right + ' / ' + value,
                 left + value.upper() + right, 'unrelated']
        for text in texts:
            assert matches_term(term, text) == former_match(term, text), (term, text)


def test_literal_lookup_does_not_compile_per_company_patterns(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Literal profile lookup compiled a regex')
    monkeypatch.setattr(re, 'search', forbidden)
    monkeypatch.setattr(re, 'match', forbidden)
    for number in range(1089):
        code = f'{number:06d}.SZ'
        assert matches_term(code, f'公司 {code} 公告')
        assert not matches_term(code, f'prefix{code}_suffix')
