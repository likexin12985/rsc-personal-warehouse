"""Independent checks for the formal role matrix and exact migration delta."""
from datetime import datetime
import json
from uuid import UUID, uuid5

from sqlalchemy import text

DEFAULTS = {
    'technician': ('submit_loss','apply_scrap_recovery'),
    'provincial_manager': ('review_loss_regional','review_scrap_recovery_regional'),
    'admin': ('finalize_loss','dispose_loss','reverse_loss','approve_loss_correction','correct_loss',
              'review_scrap_recovery_headquarters','execute_scrap_recovery'),
}
NAMESPACE=UUID('a6f9c663-8a4c-4a96-bc6f-f8cbe4c7bd02')


def require_formal_grant(db, *, role_code, action):
    """Read the migrated policy in current-head gates; never repair a fixture.

    Historical pre-0165 fixtures must keep their explicit historical setup.
    This helper neither adds a missing grant nor overrides a reviewed deny.
    """
    from sqlalchemy import select
    from app.foundation_models import Permission, Role, RolePermission

    # 0100 also supplies full-resource read grants to these three roles.
    assert (action in DEFAULTS.get(role_code, ())
        or (action == 'read' and role_code in DEFAULTS)), 'unexpected formal loss role/action'
    with db.no_autoflush:
        grant = db.scalars(select(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(Role.code == role_code, Role.status == 'active', Role.is_external.is_(False),
                Permission.resource == 'stock_operation', Permission.action == action,
                Permission.field_code == '')).one_or_none()
    assert grant is not None and grant.effect == 'allow', 'formal loss grant missing or denied'
    return grant


def uses_migrated_loss_policy(db):
    """Only the reviewed predecessor may use historical fixture grants.

    Shared multi-generation fixtures also produce genuine 0164 history before
    an upgrade. An empty, branched or unknown revision is never a legacy bypass.
    """
    with db.no_autoflush:
        revisions = db.scalars(text('SELECT version_num FROM alembic_version')).all()
    assert revisions in (['20261213_0164'], ['20261214_0165'], ['20261215_0166'], ['20261216_0167'], ['20261217_0168'], ['20261218_0169'], ['20261219_0170'], ['20261220_0171'], ['20261221_0172'], ['20261222_0173'], ['20261223_0174'], ['20261224_0175'], ['20261225_0176'], ['20261227_0178'], ['20261228_0179'], ['20261229_0180']), 'unreviewed loss fixture migration head'
    return revisions != ['20261213_0164']


def assert_fresh_defaults(db):
    assert db.scalar(text('SELECT count(*) FROM public.users'))==0
    rows=db.execute(text("""SELECT r.code,p.action,rp.effect,p.field_code,r.is_external
        FROM public.role_permissions rp JOIN public.roles r ON r.id=rp.role_id
        JOIN public.permissions p ON p.id=rp.permission_id WHERE p.resource='stock_operation'
        AND p.action=ANY(:actions)"""),dict(actions=[a for actions in DEFAULTS.values() for a in actions])).all()
    expected={(role,action,'allow','',False) for role,actions in DEFAULTS.items() for action in actions}
    assert len(rows)==11 and set(map(tuple,rows))==expected, 'formal role matrix missing or overgranted before fixture'
    return dict(defaultRoleActions=11,usersBeforeFixture=0,fixtureGrantsRequired=False,externalGrants=0)


def assert_authorization_transition(before, after, *, revision='0165'):
    """Old business rows stay byte-exact; only the planned authorization delta is allowed."""
    resource = 'stock_operation'
    if revision == '0165':
        defaults, namespace, description = DEFAULTS, NAMESPACE, 'Formal V1 loss/scrap operation: '
    elif revision == '0167':
        defaults = {
            'provincial_manager': ('submit_return_condition', 'supplement_return_condition',
                'withdraw_return_condition', 'execute_return_condition', 'release_return_condition',
                'review_return_condition_regional'),
            'admin': ('review_return_condition_headquarters', 'cancel_return_condition_approval'),
        }
        namespace = UUID('69f2c3be-541c-5c3c-b6aa-bdae07abd594')
        description = 'Formal V1 return condition operation: '
    elif revision == '0169':
        defaults = {'admin': ('close',), 'provincial_manager': ('close',)}
        namespace = UUID('988a6ffb-2497-51e0-856d-d908961da71d')
        description = 'Formal V1 material request closure: '
        resource = 'material_request'
    else:
        raise ValueError('unreviewed authorization migration')
    changed={'permissions','role_permissions','users'}
    assert set(before)==set(after)
    for name in before.keys()-changed:
        assert after[name]==before[name], name+' changed during additive authorization migration'
    def rows(snapshot,name):
        return {r['id']:r for raw in snapshot[name] for r in [json.loads(raw)]}
    oldp,newp=rows(before,'permissions'),rows(after,'permissions')
    oldg,newg=rows(before,'role_permissions'),rows(after,'role_permissions')
    roles={r['code']:r for r in rows(before,'roles').values()}
    expectedp,expectedg={},{}
    changed_roles=set()
    natural={(r['resource'],r['action'],r['field_code']):r for r in oldp.values()}
    pairs={(r['role_id'],r['permission_id']):r for r in oldg.values()}
    for code,actions in defaults.items():
        for action in actions:
            p=natural.get((resource,action,''))
            if p is None:
                pid=str(uuid5(namespace,'permission:'+resource+':'+action))
                expectedp[pid]=dict(id=pid,resource=resource,action=action,field_code='',
                    description=description+action)
            else:
                pid=p['id']
            role_id=roles[code]['id']
            if (role_id,pid) not in pairs:
                gid=str(uuid5(namespace,'grant:'+code+':'+resource+':'+action))
                expectedg[gid]=dict(id=gid,role_id=role_id,permission_id=pid,effect='allow')
                changed_roles.add(role_id)
    for old,new,expected,timestamps in ((oldp,newp,expectedp,('created_at','updated_at')),
                                        (oldg,newg,expectedg,('created_at',))):
        assert set(new)==set(old)|set(expected)
        assert all(new[k]==v for k,v in old.items()), 'existing authorization rows were modified'
        for key,value in expected.items():
            actual=dict(new[key])
            times=[actual.pop(name) for name in timestamps]
            assert len(set(times))==1 and datetime.fromisoformat(times[0]).tzinfo is not None
            assert actual==value
    affected={r['user_id'] for r in rows(before,'role_assignments').values() if r['role_id'] in changed_roles}
    old_users,new_users=rows(before,'users'),rows(after,'users')
    assert set(new_users)==set(old_users)
    for key,user in old_users.items():
        expected=dict(user)
        expected['authorization_version']+=int(key in affected)
        assert new_users[key]==expected, 'authorization migration changed unrelated user data'
    return dict(createdPermissions=len(expectedp),createdGrants=len(expectedg),advancedUsers=len(affected))
