from typing import Literal
from pydantic import BaseModel, Field, model_validator

MIGRATION_EVENT_KINDS = (
    'write', 'read', 'copy_snapshot', 'stream', 'target_down', 'target_up',
    'source_down', 'source_up', 'cutover',
)
# Replication packets carry an explicit source-origin version; a plain write
# gets its version assigned by the owning shard inside the engine.
PACKET_KINDS = ('copy_snapshot', 'stream')


class KVItem(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)


class MigrationEvent(BaseModel):
    at_ms: int = Field(ge=0, le=86400000)
    kind: Literal[*MIGRATION_EVENT_KINDS]
    key: str | None = Field(default=None, max_length=80)
    value: str | None = Field(default=None, max_length=500)
    version: int | None = Field(default=None, ge=1, le=1000000000)
    # old = request still routed by the pre-cutover table; target = new route.
    route_hint: Literal['old', 'target'] | None = None

    @model_validator(mode='after')
    def fields_by_kind(self):
        if self.kind in PACKET_KINDS:
            if not self.key or self.value is None or self.version is None:
                raise ValueError(f'{self.kind} 事件需要 key、value、version')
        if self.kind == 'write' and (not self.key or self.value is None):
            raise ValueError('write 事件需要 key、value（版本由归属分片分配）')
        if self.kind == 'read' and not self.key:
            raise ValueError('read 事件需要 key')
        return self


class MigrationPlanInput(BaseModel):
    code: str = Field(min_length=2, max_length=80)
    title: str = Field(min_length=2, max_length=160)
    key_start: str = Field(min_length=1, max_length=80)
    key_end: str = Field(min_length=1, max_length=80)
    source_shard: str = Field(min_length=2, max_length=80)
    target_shard: str = Field(min_length=2, max_length=80)
    source_initial: list[KVItem] = Field(default=list, max_length=200)
    target_initial: list[KVItem] = Field(default=list, max_length=200)
    events: list[MigrationEvent] = Field(min_length=1, max_length=100)
    note: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def plan_shape(self):
        if self.key_start > self.key_end:
            raise ValueError('key_start 必须不大于 key_end')
        if self.source_shard == self.target_shard:
            raise ValueError('源分片与目标分片不能相同')
        if any(a.at_ms > b.at_ms for a, b in zip(self.events, self.events[1:])):
            raise ValueError('事件必须按 at_ms 非递减排列')
        for label, items in (('源初始数据', self.source_initial), ('目标初始数据', self.target_initial)):
            keys = [x.key for x in items]
            if len(keys) != len(set(keys)):
                raise ValueError(label + '存在重复键')
        return self


class MigrationRunInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    migration_plan_id: int = Field(gt=0)
