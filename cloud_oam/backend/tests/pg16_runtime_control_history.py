"""Recreate real predecessor facts for the independent control runtime suite."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.foundation_models import Role
from app.formal_services.notification_events import record_business_notification
from pg16_opening_publication_fixture import prepare_stocktake_inventory
from pg16_personal_stock_fixture import establish_personal_stock
from test_formal_access import make_organization, make_user, assign


TABLES = ('inventory_transactions', 'inventory_movements', 'stock_balances',
          'serial_current_positions', 'notification_events', 'outbox_events')


def snapshot(owner):
    with owner.connect() as connection:
        return {table: connection.scalars(text(
            f'SELECT to_jsonb(t) FROM public.{table} t ORDER BY to_jsonb(t)::text'
        )).all() for table in TABLES}


def assert_history_preserved(owner, before):
    after = snapshot(owner)
    # These facts belong to a separate region and closed opening. Subsequent
    # checks may append other facts but must not remove or mutate these rows.
    for table in TABLES:
        assert all(row in after[table] for row in before[table]), table


def prepare_history(owner, api, edge):
    with Session(owner) as db:
        hq = make_organization(db, name='Synthetic control predecessor HQ')
        region = make_organization(db, name='Synthetic control predecessor region', parent=hq)
        admin, _ = make_user(db, hq, name='Synthetic control predecessor reviewer')
        manager, _ = make_user(db, region, name='Synthetic control predecessor counter')
        reviewer, _ = make_user(db, hq, name='Synthetic control independent reviewer')
        roles = {row.code: row for row in db.scalars(select(Role))}
        for user in (admin, reviewer):
            assign(db, user, roles['admin'], scope_type='national', scope_id='*')
        assign(db, manager, roles['provincial_manager'],
               scope_type='organization', scope_id=str(region.id))
        db.commit()
        admin_id, manager_id, reviewer_id = admin.id, manager.id, reviewer.id
        manager_person_id = manager.person_id
    fixture = prepare_stocktake_inventory(owner, edge,
        actor_user_id=admin_id, assignee_user_id=manager_id)
    # A real positive count and two reviews post one unit; no seeded balances,
    # invented control ownership, fake journal or weakened trigger is used.
    establish_personal_stock(api, fixture, admin=admin_id, manager=manager_id,
                             engineer=manager_id, reviewer=reviewer_id)
    # Opening records an outbox fact. Audience resolution is a separate step;
    # retain a real notification manifest without invoking any delivery channel.
    with Session(api) as db:
        identifier = uuid4()
        at = datetime.now(timezone.utc)
        record_business_notification(db, event_type='pg16_control_predecessor',
            business_type='pg16_control_predecessor', business_id=identifier,
            dedup_key='pg16-control-predecessor:'+str(identifier), payload={},
            recipient_person_id=manager_person_id, occurred_at=at, now=at)
        db.commit()
    before = snapshot(owner)
    assert before['inventory_transactions'] and before['inventory_movements']
    assert any(row['quantity'] > 0 for row in before['stock_balances'])
    assert before['notification_events'] and before['outbox_events']
    return before
