"""Current authority for dedicated loss evidence upload intents.

An uploaded file is not a loss submission or an approval. Binding it to a
document must additionally prove that exact command's actor and scope.
"""
from sqlalchemy import select

from ..formal_access import FormalAccessError
from ..foundation_models import Organization, Person
from formal_file_integrity import _fail


PURPOSE = 'stock_loss_evidence'


def require_upload_permission(db, actor):
    organization = db.scalar(select(Organization).join(
        Person, Person.organization_id == Organization.id).where(Person.id == actor.person_id)
        .execution_options(populate_existing=True))
    if (organization is None or organization.status != 'active'
            or organization.org_type not in {'headquarters', 'region_company', 'department'}):
        _denied()
    for grant in actor.assignments:
        if grant.role_code not in {'admin', 'provincial_manager', 'technician'}:
            continue
        actions = ()
        if grant.scope_type == 'person' and grant.scope_id == str(actor.person_id):
            if grant.role_code == 'technician':
                actions = ('submit_loss',)
        elif grant.scope_type == 'national' and grant.scope_id == '*':
            if grant.role_code == 'admin' and organization.org_type == 'headquarters':
                actions = ('submit_loss', 'finalize_loss', 'reverse_loss')
        elif grant.scope_type == 'organization' and grant.role_code == 'provincial_manager':
            from uuid import UUID
            try:
                scope_id = UUID(grant.scope_id)
            except (TypeError, ValueError, AttributeError):
                continue
            scope = db.get(Organization, scope_id, populate_existing=True)
            if scope and scope.status == 'active' and scope.org_type == 'region_company':
                actions = ('submit_loss', 'review_loss_regional')
        for action in actions:
            # Bind the action to this exact role assignment; a role name plus
            # an unrelated grant must not form a synthetic permission.
            if not any(e.assignment_id == grant.assignment_id and e.effect == 'allow'
                       and e.role_code == grant.role_code and e.resource == 'stock_operation'
                       and e.action == action and e.field_code == '' for e in actor.entitlements):
                continue
            try:
                # Uploads have no bound business object yet. Any current deny
                # for the action conservatively blocks this upload capability.
                if actor.allows(db, 'stock_operation', action):
                    return
            except FormalAccessError:
                pass
    _denied()


def _denied():
    _fail('file_purpose_forbidden', 'forbidden', '当前账号不能上传报损审核证据')
