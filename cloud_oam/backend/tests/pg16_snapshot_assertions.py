"""Exact append proofs for gates that share pre-existing immutable facts."""


def assert_exact_appends(before, after, expected_ids):
    """Keep every old row byte-equivalent and admit only named new identities."""
    def indexed(rows):
        result = {}
        for value in rows:
            row = dict(value if isinstance(value, dict) else value._mapping)
            identifier = str(row['id'])
            assert identifier not in result, 'duplicate snapshot identity'
            result[identifier] = row
        return result

    old, current = indexed(before), indexed(after)
    expected = tuple(str(identifier) for identifier in expected_ids)
    assert len(set(expected)) == len(expected), 'duplicate expected append identity'
    assert not old.keys() & set(expected), 'append identity already existed'
    assert old.keys() <= current.keys(), 'predecessor rows removed'
    assert all(current[key] == row for key, row in old.items()), 'predecessor rows changed'
    assert current.keys() - old.keys() == set(expected), 'unexpected appended identities'
