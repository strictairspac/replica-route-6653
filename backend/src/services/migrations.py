from sqlalchemy import select
from redis import Redis
from fastapi import HTTPException
from src.models.entities import MigrationPlan, Migration
from src.config.settings import REDIS_URL
from src.constants.enums import RUN_STATES
from src.utils.serialization import record
from .common import save
from .migration import simulate

queue = Redis.from_url(REDIS_URL, decode_responses=True)
QUEUE_KEY = 'replicaroute:migration-queue'


def listing(db):
    return [record(x) for x in db.scalars(select(Migration).order_by(Migration.id.desc()))]


def detail(db, ident):
    obj = db.get(Migration, ident)
    if not obj:
        raise HTTPException(404, '搬迁演练不存在')
    return record(obj)


def create(db, data, user):
    plan = db.get(MigrationPlan, data.migration_plan_id)
    if not plan:
        raise HTTPException(404, '搬迁脚本不存在')
    snapshot = record(plan)
    try:
        simulate(snapshot)
    except ValueError as e:
        raise HTTPException(422, str(e))
    obj = Migration(name=data.name, migration_plan_id=plan.id,
                    input_snapshot=snapshot, status=RUN_STATES[0])
    result = save(db, obj, user, '按键范围搬迁入队 ' + data.name)
    try:
        queue.rpush(QUEUE_KEY, str(obj.id))
    except Exception:
        obj.status = 'failed'
        obj.error = '队列暂时不可用，请重新创建'
        db.commit()
        raise HTTPException(503, obj.error)
    return result
