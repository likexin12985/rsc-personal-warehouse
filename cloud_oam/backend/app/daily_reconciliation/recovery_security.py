"""Pinned 0134 original-request fence runtime catalog."""
from sqlalchemy import text

INDEX_SQL=text("""
SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am a ON a.oid=c.relam
 WHERE i.indexrelid=to_regclass('public.uq_daily_review_seal_request')
 AND i.indrelid='public.daily_review_request_seals'::regclass
 AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive AND i.indimmediate
 AND i.indnkeyatts=2 AND i.indnatts=2 AND i.indpred IS NULL AND i.indexprs IS NULL AND a.amname='btree'
 AND pg_get_indexdef(i.indexrelid,1,true)='actor_user_id' AND pg_get_indexdef(i.indexrelid,2,true)='request_id')
""")

def validate_recovery_catalog(connection):
    from .review_security import DailyReviewSecurityError
    if connection.scalar(INDEX_SQL) is not True:
        raise DailyReviewSecurityError('daily_review_recovery_unique_index_drift')

TABLES=frozenset({'daily_review_request_seals'})
FUNCTIONS={('rsc_daily_review_seal_live_0134', 'daily_review_request_seals, timestamp with time zone'): {'definition': ('v',
                                                                                                              True,
                                                                                                              'plpgsql',
                                                                                                              ('search_path=pg_catalog, '
                                                                                                               'public',)),
                                                                                               'shape': ('f',
                                                                                                         'void',
                                                                                                         False),
                                                                                               'sha256': '3ac884cfa062e44c88a89446bdad6150b2a3405c70391d754d5b056206da3fe9',
                                                                                               'api': False},
 ('rsc_guard_daily_review_seal_0134', ''): {'definition': ('v',
                                                           True,
                                                           'plpgsql',
                                                           ('search_path=pg_catalog, '
                                                            'public',)),
                                            'shape': ('f', 'trigger', False),
                                            'sha256': 'bda59a7d32ce28f1c8751e69708d0819f89966e88a0eab3b58028ab9fd859ae7',
                                            'api': False},
 ('rsc_guard_daily_review_trace_0134', ''): {'definition': ('v',
                                                            True,
                                                            'plpgsql',
                                                            ('search_path=pg_catalog, '
                                                             'public',)),
                                             'shape': ('f', 'trigger', False),
                                             'sha256': '044ca663500b74aefcb6a911b593bfa59420050073a269dccf9af155ec73d656',
                                             'api': False},
 ('rsc_guard_daily_review_seal_immutable_0134', ''): {'definition': ('v',
                                                                     True,
                                                                     'plpgsql',
                                                                     ('search_path=pg_catalog, '
                                                                      'public',)),
                                                      'shape': ('f',
                                                                'trigger',
                                                                False),
                                                      'sha256': '65685f59742ccca9b52ef8593849517845314e4cb9b73e74e96895b740e5ca22',
                                                      'api': False}}
TRIGGERS={'trg_daily_review_seal_insert_0134': ('daily_review_request_seals',
                                       'rsc_guard_daily_review_seal_0134',
                                       'A',
                                       7,
                                       False,
                                       False,
                                       False),
 'trg_daily_review_seal_commit_0134': ('daily_review_request_seals',
                                       'rsc_guard_daily_review_seal_0134',
                                       'A',
                                       5,
                                       True,
                                       True,
                                       True),
 'trg_daily_review_seal_immutable_0134': ('daily_review_request_seals',
                                          'rsc_guard_daily_review_seal_immutable_0134',
                                          'A',
                                          27,
                                          False,
                                          False,
                                          False),
 'trg_daily_review_seal_truncate_0134': ('daily_review_request_seals',
                                         'rsc_guard_daily_review_seal_immutable_0134',
                                         'A',
                                         34,
                                         False,
                                         False,
                                         False),
 'trg_daily_review_events_trace_0134': ('daily_review_events',
                                        'rsc_guard_daily_review_trace_0134',
                                        'A',
                                        7,
                                        False,
                                        False,
                                        False),
 'trg_daily_review_seal_audit_0134': ('audit_events',
                                      'rsc_guard_daily_review_seal_0134',
                                      'A',
                                      7,
                                      False,
                                      False,
                                      False),
 'trg_daily_review_seal_audit_commit_0134': ('audit_events',
                                             'rsc_guard_daily_review_seal_0134',
                                             'A',
                                             5,
                                             True,
                                             True,
                                             True)}
