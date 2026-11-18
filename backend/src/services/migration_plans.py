from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import MigrationPlan
from src.utils.serialization import record
from src.services.migration import simulate
from .common import save


def listing(db):
    return [record(x) for x in db.scalars(select(MigrationPlan).order_by(MigrationPlan.id))]


def _payload(data):
    payload = data.model_dump()
    payload['source_initial'] = [x.model_dump() for x in data.source_initial]
    payload['target_initial'] = [x.model_dump() for x in data.target_initial]
    payload['events'] = [x.model_dump(exclude_none=True) for x in data.events]
    return payload


def _validate(payload):
    # Fail fast at enqueue/edit time with the same 422 semantics as routing trials.
    try:
        simulate(payload)
    except ValueError as e:
        raise HTTPException(422, str(e))


def create(db, data, user):
    payload = _payload(data)
    _validate(payload)
    return save(db, MigrationPlan(**payload), user, '创建按键范围搬迁脚本 ' + data.code)


def update(db, ident, data, user):
    obj = db.get(MigrationPlan, ident)
    if not obj:
        raise HTTPException(404, '搬迁脚本不存在')
    payload = _payload(data)
    _validate(payload)
    for k, v in payload.items():
        setattr(obj, k, v)
    return save(db, obj, user, '更新按键范围搬迁脚本 ' + data.code)
