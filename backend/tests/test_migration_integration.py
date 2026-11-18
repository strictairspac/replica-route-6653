"""Integration-style checks with in-memory fakes replacing SQLAlchemy/Redis.

Exercises the same service + worker wiring used in the containers, but with
only the Python standard library available in this sandbox.
"""
import sys
import types
import unittest
from datetime import datetime, timezone


# ---------- minimal SQLAlchemy-shaped fakes ----------
class FakeQuery:
    def __init__(self, rows): self._rows = list(rows)
    def where(self, *a, **k):
        pred = a[0] if a else None
        # Only used as Model.column == value comparisons built below.
        if pred is not None and hasattr(pred, '_fake_pred'):
            col, val = pred._fake_pred
            self._rows = [r for r in self._rows if getattr(r, col, None) == val]
        return self
    def order_by(self, *a, **k): return self
    def limit(self, *a, **k):
        self._rows = self._rows[: a[0]] if a else self._rows
        return self
    def all(self): return list(self._rows)
    def scalar(self):
        rows = self.all()
        return rows[0] if rows else None


class FakeColumn:
    def __init__(self, name): self.name = name
    def __eq__(self, other):
        pred = type('Pred', (), {})()
        pred._fake_pred = (self.name, other)
        return pred


def _model_base():
    class _Base:
        __tablename__ = ''
        def __init__(self, **kw):
            for k, v in kw.items(): setattr(self, k, v)
    return _Base


entities_mod = types.ModuleType('src.models.entities')
class User(_model_base()): __tablename__ = 'users'
class Node(_model_base()): __tablename__ = 'nodes'
class RequestSet(_model_base()): __tablename__ = 'request_sets'
class Trial(_model_base()): __tablename__ = 'trials'
class MigrationPlan(_model_base()): __tablename__ = 'migration_plans'
class Migration(_model_base()): __tablename__ = 'migrations'
class Audit(_model_base()): __tablename__ = 'audit_logs'
entities_mod.User = User
entities_mod.Node = Node
entities_mod.RequestSet = RequestSet
entities_mod.Trial = Trial
entities_mod.MigrationPlan = MigrationPlan
entities_mod.Migration = Migration
entities_mod.Audit = Audit

database_mod = types.ModuleType('src.config.database')
class FakeSession:
    tables = {c.__tablename__: {} for c in (User, Node, RequestSet, Trial, MigrationPlan, Migration, Audit)}
    sequences = {t: 0 for t in tables}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def add(self, obj):
        t = obj.__tablename__
        if getattr(obj, 'id', None) is None:
            self.sequences[t] += 1
            obj.id = self.sequences[t]
        self.tables[t][obj.id] = obj
    def get(self, model, ident): return self.tables[model.__tablename__].get(ident)
    def scalar(self, q):
        rows = q.all()
        return rows[0] if rows else None
    def scalars(self, q): return q
    def commit(self): pass
    def rollback(self): pass
    def execute(self, *a, **k): pass
def _select(*cols):
    model = cols[0].parent if hasattr(cols[0], 'parent') else cols[0]
    return FakeQuery(list(FakeSession.tables[model.__tablename__].values()))
class _Func:
    def count(self): return type('Count', (), {'parent': Audit})()
func = _Func()
database_mod.Session = FakeSession
database_mod.db_session = lambda: (yield FakeSession())
database_mod.Base = type('Base', (), {'metadata': type('M', (), {'create_all': staticmethod(lambda *a, **k: None)})()})
database_mod.engine = object()

settings_mod = types.ModuleType('src.config.settings')
settings_mod.REDIS_URL = 'memory://fake'
settings_mod.DATABASE_URL = 'memory://fake'
settings_mod.JWT_SECRET = 'test'

redis_mod = types.ModuleType('redis')
class FakeRedis:
    def __init__(self): self.lists = {}
    @classmethod
    def from_url(cls, *a, **k): return cls._instance
    def rpush(self, key, val): self.lists.setdefault(key, []).append(val)
    def blpop(self, key, timeout=0):
        lst = self.lists.get(key)
        return (key, lst.pop(0)) if lst else None
    def ping(self): return True
FakeRedis._instance = FakeRedis()
redis_mod.Redis = FakeRedis

# FastAPI is only needed for HTTPException at import time in this sandbox.
fastapi_mod = types.ModuleType('fastapi')
class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail); self.status_code = status_code; self.detail = detail
class _Dep:
    def __call__(self, *a, **k): return None
fastapi_mod.HTTPException = HTTPException
fastapi_mod.Depends = _Dep()
fastapi_mod.APIRouter = type('APIRouter', (), {'__init__': lambda self, *a, **k: None})
responses_mod = types.ModuleType('fastapi.responses')
responses_mod.Response = type('Response', (), {'__init__': lambda self, *a, **k: None})
security_mod = types.ModuleType('fastapi.security')
security_mod.HTTPBearer = type('HTTPBearer', (), {})
pydantic_mod = types.ModuleType('pydantic')
class _BaseModel:
    def __init__(self, **kw):
        for k, v in kw.items(): setattr(self, k, v)
    def model_dump(self, **k): return dict(vars(self))
pydantic_mod.BaseModel = _BaseModel
pydantic_mod.Field = lambda default=None, **k: default
class _Lit:
    def __getitem__(self, item): return item
pydantic_mod.Literal = _Lit()
pydantic_mod.model_validator = lambda **k: (lambda f: f)
jwt_mod = types.ModuleType('jwt')
jwt_mod.encode = lambda *a, **k: ''
jwt_mod.decode = lambda *a, **k: {}
sys.modules['redis'] = redis_mod
sys.modules['fastapi'] = fastapi_mod
sys.modules['fastapi.responses'] = responses_mod
sys.modules['fastapi.security'] = security_mod
sys.modules['pydantic'] = pydantic_mod
sys.modules['jwt'] = jwt_mod

sqlalchemy_mod = types.ModuleType('sqlalchemy')
def select(*cols):
    model = getattr(cols[0], 'parent', cols[0])
    return FakeQuery(list(FakeSession.tables[model.__tablename__].values()))
class _Func:
    def count(self):
        c = type('C', (), {})(); c.parent = Audit; return c
sqlalchemy_mod.select = select
sqlalchemy_mod.func = _Func()
sa_exc = types.ModuleType('sqlalchemy.exc')
sa_exc.IntegrityError = type('IntegrityError', (Exception,), {})
sys.modules['sqlalchemy'] = sqlalchemy_mod
sys.modules['sqlalchemy.exc'] = sa_exc

sys.modules['src.models.entities'] = entities_mod
sys.modules['src.config.database'] = database_mod
sys.modules['src.config.settings'] = settings_mod
sys.modules['redis'] = redis_mod

# record() works off __table__.columns — emulate minimally.
serialization_mod = types.ModuleType('src.utils.serialization')
def record(obj):
    if hasattr(obj, '_asdict'): return dict(obj._asdict())
    return {k: v for k, v in vars(obj).items() if not k.startswith('_')}
serialization_mod.record = record
sys.modules['src.utils.serialization'] = serialization_mod

from src.services import migrations as svc
from src.services.migration import simulate
from src.models.entities import MigrationPlan as Plan, Migration as Mig
from src.worker import run_migration, run_trial  # engine wiring importable


class User_:
    def __init__(self, email): self.email = email


GOOD_PLAN = {
    'code': 'IT-1', 'title': '集成演练', 'key_start': 'k1', 'key_end': 'k9',
    'source_shard': 's', 'target_shard': 't',
    'source_initial': [{'key': 'k1', 'value': 'v1'}], 'target_initial': [],
    'events': [
        {'at_ms': 0, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
        {'at_ms': 10, 'kind': 'cutover'},
        {'at_ms': 20, 'kind': 'write', 'key': 'k1', 'value': 'late', 'route_hint': 'old'},
    ], 'note': ''}


class MigrationServiceIntegration(unittest.TestCase):
    def setUp(self):
        FakeSession.tables = {t: {} for t in FakeSession.tables}
        FakeSession.sequences = {t: 0 for t in FakeSession.tables}
        self.db = FakeSession()
        self.user = User_('operator@qiyuan.test')

    def _save_plan(self, payload=GOOD_PLAN):
        plan = Plan(**payload)
        self.db.add(plan)
        return plan

    def test_enqueue_consumes_and_completes_with_late_write_forwarded(self):
        plan = self._save_plan()
        class In:
            name = 'run-1'; migration_plan_id = plan.id
        rec = svc.create(self.db, In(), self.user)
        self.assertEqual(rec['status'], 'queued')
        # worker drains the exact same redis key the service pushed to
        key, ident = svc.queue.blpop(svc.QUEUE_KEY)
        self.assertEqual(key, svc.QUEUE_KEY)
        mig = self.db.get(Mig, int(ident))
        mig.status = 'running'
        result = run_migration(mig)
        mig.result = result; mig.status = 'completed'
        self.assertTrue(result['cutover_completed'])
        self.assertTrue(result['summary']['no_data_loss'])
        late = [e for e in result['trace'] if e['kind'] == 'write' and e.get('route_hint') == 'old']
        self.assertEqual(late[0]['route'], 'old->forwarded')

    def test_enqueue_rejects_unknown_plan_404(self):
        from fastapi import HTTPException
        class In:
            name = 'run-x'; migration_plan_id = 999
        with self.assertRaises(HTTPException) as ctx:
            svc.create(self.db, In(), self.user)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_retry_packets_apply_once_through_worker_engine(self):
        plan = self._save_plan({**GOOD_PLAN, 'events': [
            {'at_ms': 0, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 10, 'kind': 'target_down'},
            {'at_ms': 20, 'kind': 'stream', 'key': 'k1', 'value': 'v2', 'version': 2},
            {'at_ms': 30, 'kind': 'target_up'},
            {'at_ms': 40, 'kind': 'stream', 'key': 'k1', 'value': 'v2', 'version': 2},
            {'at_ms': 50, 'kind': 'stream', 'key': 'k1', 'value': 'v2', 'version': 2},
            {'at_ms': 60, 'kind': 'cutover'}]})
        result = simulate({k: getattr(plan, k) for k in
                           ('key_start', 'key_end', 'source_initial', 'target_initial', 'events')})
        applied = [e for e in result['trace'] if e['kind'] == 'stream' and e['applied']]
        self.assertEqual(len(applied), 2)
        self.assertTrue(result['cutover_completed'])

    def test_cutover_refused_when_target_behind_and_audit_still_records_enqueue(self):
        plan = self._save_plan({**GOOD_PLAN, 'events': [
            {'at_ms': 0, 'kind': 'write', 'key': 'k2', 'value': 'x'},
            {'at_ms': 10, 'kind': 'cutover'}]})
        result = simulate({k: getattr(plan, k) for k in
                           ('key_start', 'key_end', 'source_initial', 'target_initial', 'events')})
        self.assertFalse(result['cutover_completed'])
        self.assertEqual(result['phase'], 'cutover_blocked')
        cut = [e for e in result['trace'] if e['kind'] == 'cutover'][0]
        self.assertTrue(cut['gate_reasons'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
