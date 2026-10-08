"""Unpublished current-action checks; no startup install or runtime grants.

Only newly inserted events invoke current authority. Historical facts and
request recovery must keep using immutable proof, without reauthorizing old
reviewers. The source lock precedes principal/location locks; the atomic writer
must use that order too. Delegated review is not activated by this candidate.
"""
from pathlib import Path


def statements():
    root = Path(__file__).resolve().parent
    return [(root / name).read_text() for name in ('authority.sql', 'authority_event.sql')]
