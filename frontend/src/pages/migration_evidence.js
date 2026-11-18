import {table} from '../components/common/table.js';
import {badge} from '../components/common/badge.js';
import {esc,date} from '../utils/html.js';
import {MIGRATION_PHASES,MIGRATION_DECISIONS,MIGRATION_KIND_HINTS} from '../constants/migration.js';

const decisionClass=d=>d==='rejected'?'rejected':(d==='skipped'?'muted-decision':'');
const ver=(s,v)=>s==null?'—':`${esc(s)} <small>v${v}</small>`;

export function migrationEvidencePage(migration){
  if(!migration)return '<div class="empty">还没有搬迁证据。先编排搬迁脚本，再从「搬迁执行」发起一次演练。</div>';
  const r=migration.result;
  return `<div class="evidence-top"><div>
      <span class="eyebrow">MIGRATION EVIDENCE / #${migration.id}</span>
      <h2>${esc(migration.name)} ${badge(migration.status)}</h2>
      <p>${date(migration.created_at)} · 搬迁脚本快照已冻结</p>
    </div><button id="migration-export" data-id="${migration.id}">导出 JSON ↓</button></div>
  ${migration.error?`<div class="error">${esc(migration.error)}</div>`:''}
  ${r?renderResult(migration,r):'<div class="notice">后台重放中，每1.5秒刷新。可保留此页，或稍后从执行记录返回。</div>'}`;
}

function renderResult(m,r){
  const s=r.summary;
  const verdictOk=s.no_data_loss&&r.cutover_completed;
  return `<div class="verdict"><div class="verdict-icon">${verdictOk?'✓':'!'}</div><div>
      <span class="eyebrow">NO-DATA-LOSS CHECK / 阶段 · ${esc(MIGRATION_PHASES[r.phase]||r.phase)}</span>
      <h2>${r.cutover_completed?(s.no_data_loss?'切流完成且无数据丢失':'切流完成但存在丢写风险'):'未切流：目标未补齐，归属保留在源分片'}</h2>
      <p>写入 ${s.writes}（路由 ${s.routed_writes} / 拒绝 ${s.rejected_writes}）· 读取拒绝 ${s.rejected_reads}
        · 拷贝 应用${s.copy_applied}/跳过${s.copy_skipped} · 增量 应用${s.stream_applied}/跳过${s.stream_skipped}
        · 切流拒绝 ${s.cutover_rejected} 次</p>
    </div></div>
  <div class="policy-strip">键范围 ${esc(r.key_range.start)} .. ${esc(r.key_range.end)}
    <span>／</span> 归属：${r.cutover_completed?'范围键已切至目标':'范围键仍属源分片'}
    <span>／</span> 源已提交键 ${s.source_accepted_keys} · 目标范围键 ${s.target_in_range_keys}</div>

  <div class="section-top"><h2>两端内容与键归属</h2><span>SOURCE / TARGET SNAPSHOT</span></div>
  ${table(['键','是否搬迁范围','当前归属','源分片（版本/值）','目标分片（版本/值）'],r.ownership.map(o=>[
    `<strong>${esc(o.key)}</strong>`,
    o.in_range?'范围内':'范围外',
    `<span class="owner ${esc(o.owner)}">${o.owner==='target'?'目标':'源'}</span>`,
    o.source?`v${o.source.version} · ${esc(o.source.value)}`:'—',
    o.target?`v${o.target.version} · ${esc(o.target.value)}`:'—']))}

  <div class="section-top"><h2>搬迁时序证据（可并发交错）</h2><span>同一 at_ms 按输入顺序执行</span></div>
  ${table(['时间 / 事件','键 / 路由','入版→目标版本','决策与理由（含拒绝原因）'],r.trace.map(e=>[
    `${e.at_ms}ms<small>${esc(MIGRATION_KIND_HINTS[e.kind]||e.kind)}</small>`,
    `${e.key?esc(e.key):'—'}<small>${esc(e.route||e.owner||'')}</small>`,
    `${e.incoming_version??'—'} → ${e.target_version??'—'}${e.stored_version!=null?` <small>存 v${e.stored_version}</small>`:''}`,
    `<span class="${decisionClass(e.decision)}">${esc(MIGRATION_DECISIONS[e.decision]||e.decision)}</span> ${esc(e.reason)}${e.gate_reasons.length?`<small class="rejected">闸门：${esc(e.gate_reasons.join('；'))}</small>`:''}`]))}

  <div class="evidence-notes"><section><h3>可用性代价</h3>
      <p>${esc(r.policy.availability_cost)}</p>
      <h3>迟到写与幂等口径</h3>
      <p>${esc(r.policy.late_write_rule)}</p>
      <p>${esc(r.policy.stream_rule)}</p>
    </section><section><h3>版本与切流闸门</h3>
      <p>${esc(r.policy.version_model)}</p>
      <p>${esc(r.policy.cutover_gate)}</p>
      <p>${esc(r.mode)}</p>
      <code>${esc(r.checksum)}</code>
    </section></div>`;
}
