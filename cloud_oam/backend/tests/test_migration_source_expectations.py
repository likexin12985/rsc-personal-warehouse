"""Historical-to-current proofs cannot silently skip equivalent signatures."""
import hashlib

import pytest

import migration_source_expectations as sources


def chain(monkeypatch, patches):
    monkeypatch.setattr(sources, '_successor_paths', lambda revision: tuple(
        (str(index), index) for index in range(len(patches))))
    monkeypatch.setattr(sources, '_source_patches', lambda path: patches[path])


def test_follows_mixed_comma_spacing_and_checks_each_predecessor(monkeypatch):
    chain(monkeypatch, [
        {'public.guard(uuid,boolean)': ('first', 'second')},
        {'public.guard(uuid, boolean)': ('second', 'third')},
        {'public.guard(uuid,  boolean)': ('third', 'last')},
    ])
    assert sources.current_source_body('base', 'public.guard(uuid, boolean)', 'first') == 'last'
    assert sources.current_source_hash('base', 'public.guard(uuid,boolean)', 'first') == hashlib.sha256(b'last').hexdigest()
    with pytest.raises(AssertionError, match='historical source discontinuity'):
        sources.current_source_body('base', 'public.guard(uuid, boolean)', 'wrong')


def test_does_not_merge_different_schema_or_overload(monkeypatch):
    chain(monkeypatch, [{
        'private.guard(uuid,boolean)': ('other', 'private'),
        'public.guard(text,boolean)': ('other', 'overload'),
    }])
    assert sources.current_source_body('base', 'public.guard(uuid, boolean)', 'first') == 'first'


def test_duplicate_equivalent_patch_coordinates_fail_closed(monkeypatch):
    chain(monkeypatch, [{
        'public.guard(uuid,boolean)': ('first', 'second'),
        'public.guard(uuid, boolean)': ('first', 'other'),
    }])
    with pytest.raises(AssertionError, match='duplicate source coordinate'):
        sources.current_source_body('base', 'public.guard(uuid, boolean)', 'first')


def test_inherited_patch_key_cannot_be_skipped_by_literal_name_filter(monkeypatch, tmp_path):
    predecessor = tmp_path / 'predecessor.py'
    predecessor.write_text("ACCOUNT_SIGNATURE = 'public.guard(uuid, boolean)'\n")
    revision = tmp_path / 'successor.py'
    revision.write_text(
        "import runpy\n"
        f"previous = runpy.run_path({str(predecessor)!r})\n"
        "def _sources():\n"
        "    return {previous['ACCOUNT_SIGNATURE']: ('first', 'second')}\n"
    )
    monkeypatch.setattr(sources, '_successor_paths', lambda _: (('synthetic', str(revision)),))
    monkeypatch.setattr(sources, '_catalog_source_patches', lambda *args: {})
    monkeypatch.setattr(sources, '_catalog_source_key', lambda *args: False)
    assert sources.current_source_body('base', 'public.guard(uuid, boolean)', 'first') == 'second'
    with pytest.raises(AssertionError, match='historical source discontinuity'):
        sources.current_source_body('base', 'public.guard(uuid, boolean)', 'wrong')
