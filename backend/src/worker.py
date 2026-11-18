import traceback, json
from pathlib import Path
from src.config.database import Session
from src.models.entities import Trial, Migration
from src.services.trials import queue
from src.services.migrations import QUEUE_KEY
from src.services.routing import simulate as simulate_route
from src.services.migration import simulate as simulate_migration

# One serial worker drains both queues: replay stays deterministic and no two
# simulations ever race for the shared /artifacts volume.
QUEUES = (
    ('replicaroute:queue', Trial, 'route'),
    (QUEUE_KEY, Migration, 'migration'),
)


def run_trial(obj):
    snapshot = obj.input_snapshot
    return simulate_route(snapshot['nodes'], snapshot['request_set']['events'],
                          obj.max_delay_ms, obj.sticky_ms)


def run_migration(obj):
    snapshot = {**obj.input_snapshot, 'name': obj.name}
    return simulate_migration(snapshot)


ENGINES = {Trial: run_trial, Migration: run_migration}


def drain_once():
    """Pop one job from either queue and process it. Returns True if a job ran."""
    item = None
    for key, _, _ in QUEUES:
        item = queue.blpop(key, timeout=1)
        if item:
            queue_key = key
            break
    if not item:
        return False
    ident = int(item[1])
    try:
        model = next(m for k, m, _ in QUEUES if k == queue_key)
        label = next(lb for k, _, lb in QUEUES if k == queue_key)
        with Session() as db:
            obj = db.get(model, ident)
            obj.status = 'running'
            db.commit()
        with Session() as db:
            obj = db.get(model, ident)
            result = ENGINES[model](obj)
            obj.result = result
            obj.status = 'completed'
            db.commit()
        Path(f'/artifacts/{label}-{ident}.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        traceback.print_exc()
        with Session() as db:
            for model in (Trial, Migration):
                obj = db.get(model, ident)
                if obj:
                    obj.status = 'failed'
                    obj.error = '重放失败，请检查脚本事件并重新发起（重试不会重复应用数据，版本闸保证幂等）'
                    db.commit()
                    break
    return True


def main():
    while True:
        Path('/tmp/worker-heartbeat').touch()
        drain_once()


if __name__ == '__main__':
    main()
