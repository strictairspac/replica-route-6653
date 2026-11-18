"""Stdlib-only checks for the key-range migration engine.

Run from backend/: python -m tests.test_migration
Covers: snapshot-vs-new-write overwrite, cutover gate, late writes,
target failure + idempotent retry, no-data-loss invariant, determinism.
"""
import copy
import json
import unittest

from src.services.migration import simulate


def base_plan(**over):
    plan = {
        'name': 'unit', 'key_start': 'k1', 'key_end': 'k9',
        'source_shard': 's', 'target_shard': 't',
        'source_initial': [{'key': 'k1', 'value': 'v1'}, {'key': 'k2', 'value': 'v2'}],
        'target_initial': [], 'events': [],
    }
    plan.update(over)
    return plan


class MigrationEngineTests(unittest.TestCase):
    def test_snapshot_old_packet_never_overwrites_newer_write(self):
        # Stream applies v2 first; a stale snapshot v1 arriving later must skip.
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 50, 'kind': 'write', 'key': 'k1', 'value': 'v3'},
            {'at_ms': 100, 'kind': 'stream', 'key': 'k1', 'value': 'v3', 'version': 2},
            {'at_ms': 150, 'kind': 'copy_snapshot', 'key': 'k1', 'value': 'v1', 'version': 1},
        ])
        r = simulate(plan)
        target = {x['key']: x for x in r['final_target']}
        self.assertEqual(target['k1']['value'], 'v3')
        self.assertEqual(target['k1']['version'], 2)
        stale = [e for e in r['trace'] if e['kind'] == 'copy_snapshot'][0]
        self.assertEqual(stale['decision'], 'skipped')
        self.assertFalse(stale['applied'])

    def test_duplicate_stream_retried_after_target_recovery_applies_once(self):
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'stream', 'key': 'k2', 'value': 'v2', 'version': 1},
            {'at_ms': 5, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 10, 'kind': 'write', 'key': 'k2', 'value': 'v2x'},
            {'at_ms': 15, 'kind': 'target_down'},
            {'at_ms': 20, 'kind': 'stream', 'key': 'k2', 'value': 'v2x', 'version': 2},
            {'at_ms': 30, 'kind': 'target_up'},
            {'at_ms': 40, 'kind': 'stream', 'key': 'k2', 'value': 'v2x', 'version': 2},
            {'at_ms': 50, 'kind': 'stream', 'key': 'k2', 'value': 'v2x', 'version': 2},
            {'at_ms': 60, 'kind': 'cutover'},
        ])
        r = simulate(plan)
        self.assertTrue(r['cutover_completed'])
        applied = [e for e in r['trace'] if e['kind'] == 'stream' and e['applied']]
        self.assertEqual(len(applied), 3)  # k2 v1, k1 v1, k2 v2 — each exactly once
        dupes = [e for e in r['trace'] if e['kind'] == 'stream' and e['duplicate']]
        self.assertEqual(len(dupes), 1)
        rejected = [e for e in r['trace'] if e['decision'] == 'rejected']
        # down-window stream + nothing else rejected
        self.assertEqual(len(rejected), 1)
        self.assertIn('目标分片不可用', rejected[0]['reason'])
        self.assertTrue(r['summary']['no_data_loss'])

    def test_cutover_rejected_while_behind_and_accepted_after_catchup(self):
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'copy_snapshot', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 10, 'kind': 'write', 'key': 'k2', 'value': 'v2b'},
            {'at_ms': 20, 'kind': 'cutover'},  # k2 missing on target -> reject
            {'at_ms': 30, 'kind': 'stream', 'key': 'k2', 'value': 'v2b', 'version': 2},
            {'at_ms': 40, 'kind': 'cutover'},  # now safe
        ])
        r = simulate(plan)
        cuts = [e for e in r['trace'] if e['kind'] == 'cutover']
        self.assertEqual(cuts[0]['decision'], 'rejected')
        self.assertTrue(cuts[0]['gate_reasons'])
        self.assertEqual(cuts[1]['decision'], 'applied')
        self.assertTrue(r['cutover_completed'])
        self.assertEqual(r['phase'], 'completed')

    def test_late_writes_after_cutover_have_explicit_disposition(self):
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 0, 'kind': 'stream', 'key': 'k2', 'value': 'v2', 'version': 1},
            {'at_ms': 10, 'kind': 'cutover'},
            # old route, source healthy -> forwarded to target
            {'at_ms': 20, 'kind': 'write', 'key': 'k1', 'value': 'late-ok', 'route_hint': 'old'},
            # new route -> direct target write
            {'at_ms': 30, 'kind': 'write', 'key': 'k2', 'value': 'new-route'},
            {'at_ms': 40, 'kind': 'source_down'},
            # old route but source unreachable -> explicit reject, no silent drop
            {'at_ms': 50, 'kind': 'write', 'key': 'k1', 'value': 'late-lost', 'route_hint': 'old'},
        ])
        r = simulate(plan)
        writes = [e for e in r['trace'] if e['kind'] == 'write']
        self.assertEqual(writes[0]['route'], 'old->forwarded')
        self.assertEqual(writes[1]['route'], 'target')
        self.assertEqual(writes[2]['decision'], 'rejected')
        self.assertIn('旧路由迟到写', writes[2]['reason'])
        target = {x['key']: x for x in r['final_target']}
        self.assertEqual(target['k1']['value'], 'late-ok')
        self.assertNotIn('late-lost', json.dumps(r['final_target']))

    def test_out_of_range_keys_remain_on_source(self):
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'write', 'key': 'zzz', 'value': 'outside'},
            {'at_ms': 10, 'kind': 'copy_snapshot', 'key': 'zzz', 'value': 'outside', 'version': 1},
            {'at_ms': 20, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 30, 'kind': 'stream', 'key': 'k2', 'value': 'v2', 'version': 1},
            {'at_ms': 40, 'kind': 'cutover'},
            {'at_ms': 50, 'kind': 'read', 'key': 'zzz'},
        ])
        r = simulate(plan)
        self.assertTrue(r['cutover_completed'])
        owner = {x['key']: x['owner'] for x in r['ownership']}
        self.assertEqual(owner['zzz'], 'source')
        self.assertEqual(owner['k1'], 'target')
        read = [e for e in r['trace'] if e['kind'] == 'read'][0]
        self.assertEqual(read['owner'], 'source')

    def test_concurrent_writes_during_copy_are_all_caught_up(self):
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'copy_snapshot', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 0, 'kind': 'copy_snapshot', 'key': 'k2', 'value': 'v2', 'version': 1},
            {'at_ms': 10, 'kind': 'write', 'key': 'k1', 'value': 'w1'},
            {'at_ms': 10, 'kind': 'write', 'key': 'k2', 'value': 'w2'},
            {'at_ms': 20, 'kind': 'write', 'key': 'k1', 'value': 'w1b'},
            {'at_ms': 30, 'kind': 'stream', 'key': 'k1', 'value': 'w1', 'version': 2},
            {'at_ms': 40, 'kind': 'stream', 'key': 'k2', 'value': 'w2', 'version': 2},
            {'at_ms': 50, 'kind': 'stream', 'key': 'k1', 'value': 'w1b', 'version': 3},
            {'at_ms': 60, 'kind': 'cutover'},
        ])
        r = simulate(plan)
        self.assertTrue(r['cutover_completed'])
        self.assertEqual(r['summary']['data_loss_keys'], [])
        target = {x['key']: x for x in r['final_target']}
        self.assertEqual(target['k1'], {'key': 'k1', 'value': 'w1b', 'version': 3})
        self.assertEqual(target['k2'], {'key': 'k2', 'value': 'w2', 'version': 2})

    def test_target_down_blocks_replication_and_cutover(self):
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'target_down'},
            {'at_ms': 10, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 20, 'kind': 'cutover'},
        ])
        r = simulate(plan)
        self.assertFalse(r['cutover_completed'])
        self.assertEqual(r['phase'], 'cutover_blocked')
        cut = [e for e in r['trace'] if e['kind'] == 'cutover'][0]
        self.assertIn('目标分片不可用', '；'.join(cut['gate_reasons']))

    def test_newer_target_writes_after_cutover_are_not_flagged_as_loss(self):
        # Source's last accepted version of k1 is v2; after cutover the target
        # takes v3/v4 directly. The gate/verdict must treat this as safe.
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 10, 'kind': 'write', 'key': 'k1', 'value': 'v2'},
            {'at_ms': 20, 'kind': 'stream', 'key': 'k1', 'value': 'v2', 'version': 2},
            {'at_ms': 30, 'kind': 'stream', 'key': 'k2', 'value': 'v2', 'version': 1},
            {'at_ms': 40, 'kind': 'cutover'},
            {'at_ms': 50, 'kind': 'write', 'key': 'k1', 'value': 'v3'},
            {'at_ms': 60, 'kind': 'write', 'key': 'k1', 'value': 'v4'},
        ])
        r = simulate(plan)
        self.assertTrue(r['cutover_completed'])
        self.assertEqual(r['summary']['data_loss_keys'], [])
        self.assertTrue(r['summary']['no_data_loss'])
        target = {x['key']: x for x in r['final_target']}
        self.assertEqual(target['k1']['version'], 4)

    def test_deterministic_checksum(self):
        plan = base_plan(events=[
            {'at_ms': 0, 'kind': 'stream', 'key': 'k1', 'value': 'v1', 'version': 1},
            {'at_ms': 10, 'kind': 'stream', 'key': 'k2', 'value': 'v2', 'version': 1},
            {'at_ms': 20, 'kind': 'cutover'},
        ])
        self.assertEqual(simulate(copy.deepcopy(plan))['checksum'],
                         simulate(copy.deepcopy(plan))['checksum'])

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            simulate(base_plan(key_start='z', key_end='a', events=[
                {'at_ms': 0, 'kind': 'cutover'}]))
        with self.assertRaises(ValueError):
            simulate(base_plan(events=[
                {'at_ms': 5, 'kind': 'cutover'}, {'at_ms': 0, 'kind': 'read', 'key': 'k1'}]))
        with self.assertRaises(ValueError):
            simulate(base_plan(events=[]))
        with self.assertRaises(ValueError):
            simulate(base_plan(source_initial=[
                {'key': 'k1', 'value': 'a'}, {'key': 'k1', 'value': 'b'}],
                events=[{'at_ms': 0, 'kind': 'cutover'}]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
