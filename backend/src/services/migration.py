"""Deterministic key-range online migration replay.

This is a controlled migration *model*, not a live shard cluster.  Every event is
replayed in timeline order; concurrency between copy, replication and writes is
expressed by interleaving events at the same ``at_ms`` (input order wins).

Correctness model
-----------------
* Every accepted write on a shard gets a per-key monotonic integer ``version``.
  The version is the only overwrite authority: an apply (存量拷贝 / 增量复制) is
  accepted only when the incoming version is strictly higher than the version
  already stored on the target.  Old copies therefore never clobber newer writes
  and retried packets are applied at most once (idempotent replay).
* Routing ownership of the migrating range flips once, at ``cutover``.  Writes
  arriving on the old route after cutover are never silently dropped: they are
  forwarded to the target while the source stays reachable, or explicitly
  rejected with a reason when they cannot be handed over.
* Cutover is refused unless the target is healthy and has caught up with every
  accepted in-range write on the source (same value and version, key by key).
"""
import copy
import hashlib
import json

MIGRATION_KINDS = (
    'write', 'read', 'copy_snapshot', 'stream', 'target_down', 'target_up',
    'source_down', 'source_up', 'cutover',
)
# Phases shown on the timeline and in the evidence page.
PHASES = ('preparing', 'copying', 'catching_up', 'cutover_blocked', 'cutover', 'completed', 'failed')


def _in_range(key, start, end):
    return start <= key <= end


def simulate(plan):
    """Replay one migration plan and return the full evidence structure."""
    plan = copy.deepcopy(plan)
    name = plan.get('name', '未命名搬迁')
    start, end = plan['key_start'], plan['key_end']
    if not isinstance(start, str) or not isinstance(end, str) or not (start <= end):
        raise ValueError('键范围必须是非空字符串且 key_start <= key_end')
    if len(start) > 80 or len(end) > 80:
        raise ValueError('键长度不得超过80')

    def load(side):
        items = plan.get(side + '_initial') or []
        store = {}
        for item in items:
            key, value = item.get('key'), item.get('value')
            if not isinstance(key, str) or not 1 <= len(key) <= 80:
                raise ValueError('初始数据键必须是1–80位字符串')
            if key in store:
                raise ValueError('初始数据存在重复键：' + key)
            store[key] = {'value': value, 'version': 1}
        return store

    src, dst = load('source'), load('target')
    events = plan.get('events') or []
    if not 1 <= len(events) <= 100:
        raise ValueError('需要1–100个时序事件')

    src_status = {'healthy': True}
    dst_status = {'healthy': True}
    cutover_done = False
    blocked_until = None          # at_ms of the first rejected cutover, for phase display
    last_time = -1
    trace = []
    # Highest version ever *accepted* on the source per in-range key — the
    # authoritative list the target must catch up with before cutover.
    src_accepted = {k: copy.deepcopy(v) for k, v in src.items() if _in_range(k, start, end)}
    counters = {'writes': 0, 'reads': 0, 'routed_writes': 0, 'rejected_writes': 0,
                'rejected_reads': 0, 'copy_applied': 0, 'copy_skipped': 0,
                'stream_applied': 0, 'stream_skipped': 0, 'cutover_rejected': 0}

    def gate_reasons():
        """Every reason why cutover is currently unsafe (empty list = safe)."""
        reasons = []
        if not dst_status['healthy']:
            reasons.append('目标分片不可用，无法接管')
        missing, stale = [], []
        for key, item in sorted(src_accepted.items()):
            other = dst.get(key)
            if other is None:
                missing.append(key)
            elif other['version'] < item['version'] or (
                    other['version'] == item['version'] and other['value'] != item['value']):
                # Versions are monotonic per key: a target version strictly
                # higher than the source's last accepted write means the target
                # already took newer post-cutover writes — safe, not data loss.
                # Equal version with a different value signals a bad packet.
                stale.append(key)
        if missing:
            reasons.append('目标缺少源分片已提交键：' + '、'.join(missing[:6]) + ('…' if len(missing) > 6 else ''))
        if stale:
            reasons.append('目标版本/内容落后的键：' + '、'.join(stale[:6]) + ('…' if len(stale) > 6 else ''))
        return reasons

    def row(index, event):
        return {'index': index + 1, 'at_ms': event['at_ms'], 'kind': event['kind'],
                'key': event.get('key'), 'route': None, 'owner': None, 'decision': 'applied',
                'reason': '', 'incoming_version': None, 'source_version': None,
                'target_version': None, 'stored_version': None, 'applied': None,
                'duplicate': False, 'in_range': None, 'gate_reasons': [],
                'route_hint': event.get('route_hint')}

    for index, event in enumerate(events):
        at, kind = event['at_ms'], event['kind']
        if at < last_time:
            raise ValueError('事件必须按时间非递减排列')
        last_time = at
        if kind not in MIGRATION_KINDS:
            raise ValueError('未知迁移事件类型：' + str(kind))
        r = row(index, event)
        key = event.get('key')

        if kind in ('source_down', 'source_up'):
            src_status['healthy'] = kind == 'source_up'
            r.update(reason='源分片' + ('下线' if kind == 'source_down' else '恢复'))
        elif kind in ('target_down', 'target_up'):
            dst_status['healthy'] = kind == 'target_up'
            r.update(reason='目标分片' + ('下线' if kind == 'target_down' else '恢复'))

        elif kind in ('copy_snapshot', 'stream'):
            if key is None or 'value' not in event or event.get('version') is None:
                raise ValueError(kind + ' 事件需要 key/value/version')
            ver = event['version']
            if not isinstance(ver, int) or ver < 1:
                raise ValueError('复制版本号必须是正整数')
            r.update(in_range=_in_range(key, start, end), incoming_version=ver,
                     source_version=src.get(key, {}).get('version'),
                     target_version=dst.get(key, {}).get('version'))
            if not r['in_range']:
                r.update(decision='rejected', applied=False,
                         reason='键不在搬迁范围 %s..%s 内，拒绝复制到目标' % (start, end))
                counters['copy_skipped' if kind == 'copy_snapshot' else 'stream_skipped'] += 1
            elif not dst_status['healthy']:
                r.update(decision='rejected', applied=False,
                         reason='目标分片不可用，复制包未应用，需在目标恢复后重试')
                counters['copy_skipped' if kind == 'copy_snapshot' else 'stream_skipped'] += 1
            else:
                current = dst.get(key)
                if current is not None and current['version'] >= ver:
                    # Older snapshot packet arriving after a newer stream/write,
                    # or an exact duplicate retry: keep the newer copy.
                    r.update(decision='skipped', applied=False, stored_version=current['version'],
                             duplicate=(current['version'] == ver),
                             reason=('重复复制包，版本 %d 已应用，幂等跳过' % ver)
                             if current['version'] == ver
                             else '旧版本 %d 不得覆盖目标已有的新版本 %d' % (ver, current['version']))
                    counters['copy_skipped' if kind == 'copy_snapshot' else 'stream_skipped'] += 1
                else:
                    dst[key] = {'value': event['value'], 'version': ver}
                    r.update(decision='applied', applied=True, stored_version=ver,
                             reason=('存量拷贝批次写入目标' if kind == 'copy_snapshot' else '增量日志应用到目标'))
                    counters['copy_applied' if kind == 'copy_snapshot' else 'stream_applied'] += 1

        elif kind == 'write':
            if key is None or 'value' not in event:
                raise ValueError('write 事件需要 key/value')
            r['in_range'] = _in_range(key, start, end)
            counters['writes'] += 1
            if not r['in_range']:
                # Keys outside the migrating range always belong to the source.
                if not src_status['healthy']:
                    r.update(decision='rejected', reason='范围外键归属源分片，源分片不可用，拒绝写入')
                    counters['rejected_writes'] += 1
                else:
                    new_ver = src.get(key, {'version': 0})['version'] + 1
                    src[key] = {'value': event['value'], 'version': new_ver}
                    r.update(route='source', owner='source', decision='routed',
                             stored_version=new_ver, reason='范围外键，按旧路由写入源分片')
                    counters['routed_writes'] += 1
            elif not cutover_done:
                # Before cutover the source keeps accepting writes even while the
                # snapshot/stream pipeline runs concurrently.
                if not src_status['healthy']:
                    r.update(decision='rejected', reason='切流前范围键归属源分片，源分片不可用，拒绝写入（不向未接管目标盲写）')
                    counters['rejected_writes'] += 1
                else:
                    new_ver = src.get(key, {'version': 0})['version'] + 1
                    src[key] = {'value': event['value'], 'version': new_ver}
                    src_accepted[key] = {'value': event['value'], 'version': new_ver}
                    r.update(route='source', owner='source', decision='routed',
                             stored_version=new_ver,
                             reason='搬迁进行中源分片持续接写，记录版本 %d，等待增量复制追赶' % new_ver)
                    counters['routed_writes'] += 1
            else:
                # Post-cutover: the new route goes to the target. A request still
                # carrying the old route gets an explicit disposition.
                if event.get('route_hint') == 'old':
                    if not src_status['healthy']:
                        r.update(route='old', owner='target', decision='rejected',
                                 reason='旧路由迟到写：源分片已不可达，无法转发目标，明确拒绝以避免双主丢写')
                        counters['rejected_writes'] += 1
                    elif not dst_status['healthy']:
                        r.update(route='old', owner='target', decision='rejected',
                                 reason='旧路由迟到写：目标分片不可用，转发无落点，明确拒绝')
                        counters['rejected_writes'] += 1
                    else:
                        new_ver = dst.get(key, {'version': 0})['version'] + 1
                        dst[key] = {'value': event['value'], 'version': new_ver}
                        r.update(route='old->forwarded', owner='target', decision='routed',
                                 stored_version=new_ver,
                                 reason='旧路由迟到写经源分片转发至新归属目标，版本 %d，未在旧拷贝落盘' % new_ver)
                        counters['routed_writes'] += 1
                else:
                    if not dst_status['healthy']:
                        r.update(route='target', owner='target', decision='rejected',
                                 reason='新路由写目标分片，但目标不可用，拒绝写入')
                        counters['rejected_writes'] += 1
                    else:
                        new_ver = dst.get(key, {'version': 0})['version'] + 1
                        dst[key] = {'value': event['value'], 'version': new_ver}
                        r.update(route='target', owner='target', decision='routed',
                                 stored_version=new_ver, reason='切流后范围键归属目标，直接写入新版本 %d' % new_ver)
                        counters['routed_writes'] += 1

        elif kind == 'read':
            if key is None:
                raise ValueError('read 事件需要 key')
            r['in_range'] = _in_range(key, start, end)
            counters['reads'] += 1
            owner = 'source' if (not r['in_range'] or not cutover_done) else 'target'
            shard = dst if owner == 'target' else src
            alive = dst_status['healthy'] if owner == 'target' else src_status['healthy']
            r['owner'] = owner
            r['source_version'], r['target_version'] = src.get(key, {}).get('version'), dst.get(key, {}).get('version')
            if not alive:
                r.update(decision='rejected', reason='归属分片 %s 不可用，拒绝读取，不返回陈旧数据' % ('目标' if owner == 'target' else '源'))
                counters['rejected_reads'] += 1
            elif key not in shard:
                r.update(decision='rejected', reason='归属分片 %s 无此键，拒绝读取' % ('目标' if owner == 'target' else '源'))
                counters['rejected_reads'] += 1
            else:
                item = shard[key]
                r.update(route=owner, decision='routed', stored_version=item['version'],
                         reason=('切流后读取新归属目标' if owner == 'target' else '读取当前归属源分片'))

        elif kind == 'cutover':
            reasons = gate_reasons()
            r['gate_reasons'] = reasons
            r['source_version'], r['target_version'] = len(src_accepted), len(dst)
            if cutover_done:
                r.update(decision='rejected', reason='切流已完成，重复切流请求拒绝')
                counters['cutover_rejected'] += 1
            elif reasons:
                cutover_done = False
                if blocked_until is None:
                    blocked_until = at
                r.update(decision='rejected', reason='目标未补齐，拒绝切流：' + '；'.join(reasons))
                counters['cutover_rejected'] += 1
            else:
                cutover_done = True
                r.update(decision='applied', owner='target',
                         reason='目标已追平源分片全部已提交范围键，路由归属切换至目标')

        trace.append(r)

    # Final verification: every accepted in-range source write must exist on the
    # target with at least the same version.  A newer target version comes from
    # legitimate post-cutover writes; only missing/stale keys count as data loss.
    loss = []
    if cutover_done:
        for key, item in sorted(src_accepted.items()):
            other = dst.get(key)
            if other is None or other['version'] < item['version'] or (
                    other['version'] == item['version'] and other['value'] != item['value']):
                loss.append(key)

    if any(r['kind'] == 'cutover' and r['decision'] == 'applied' for r in trace):
        phase = 'completed'
    elif any(r['kind'] == 'cutover' and r['decision'] == 'rejected' for r in trace):
        phase = 'cutover_blocked'
    elif counters['copy_applied'] or any(r['kind'] == 'copy_snapshot' for r in trace):
        phase = 'copying'
    elif counters['stream_applied'] or any(r['kind'] == 'stream' for r in trace):
        phase = 'catching_up'
    else:
        phase = 'preparing'

    result = {
        'mode': '受控按键范围在线迁移模型 / 非真实分片集群',
        'migration': name,
        'key_range': {'start': start, 'end': end},
        'phase': phase,
        'cutover_completed': cutover_done,
        'cutover_blocked_at_ms': blocked_until,
        'trace': trace,
        'ownership': [
            {'key': k, 'owner': ('target' if cutover_done and _in_range(k, start, end) else 'source'),
             'in_range': _in_range(k, start, end),
             'source': copy.deepcopy(src.get(k)), 'target': copy.deepcopy(dst.get(k))}
            for k in sorted(set(src) | set(dst))
        ],
        'final_source': [{'key': k, **copy.deepcopy(v)} for k, v in sorted(src.items())],
        'final_target': [{'key': k, **copy.deepcopy(v)} for k, v in sorted(dst.items())],
        'summary': {
            **counters,
            'gate_checks': len(gate_reasons()),
            'data_loss_keys': loss,
            'no_data_loss': not loss,
            'source_accepted_keys': len(src_accepted),
            'target_in_range_keys': sum(1 for k in dst if _in_range(k, start, end)),
        },
        'policy': {
            'version_model': '每键单调整数版本，仅当 incoming_version > 本地版本时应用',
            'snapshot_rule': '存量拷贝只写目标且受版本闸控制，旧包不覆盖新写，重复包幂等跳过',
            'stream_rule': '增量复制与写入可交错，同一版本闸保证至少一次投递下不重复应用',
            'cutover_gate': '目标在线且逐键追平源分片全部已接受范围键（目标版本不低于源已提交版本；版本相等时值须一致；目标版本更高代表已接新写，允许切流）',
            'late_write_rule': '切流后旧路由迟到写：源可达则转发目标，不可达则明确拒绝，绝不回写旧主形成双主',
            'availability_cost': '搬迁期间双份存储与复制带宽；切流前源不可用会拒绝范围写；目标故障窗口复制/新路由写被拒；旧路由需源保持短暂可达以转发迟到写',
            'same_time_rule': 'at_ms 相同按输入顺序执行，用于表达并发交错',
        },
    }
    result['checksum'] = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result
