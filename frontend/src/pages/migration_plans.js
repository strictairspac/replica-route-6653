import {table} from '../components/common/table.js';
import {esc} from '../utils/html.js';
import {session} from '../hooks/session.js';

const samplePlan={
  code:'MG-1118-D', title:'按键范围在线搬迁演练',
  key_start:'user:100', key_end:'user:199',
  source_shard:'shard-harbor', target_shard:'shard-bay',
  source_initial:[{key:'user:100',value:'snap-100'},{key:'user:150',value:'snap-150'}],
  target_initial:[],
  events:[
    {at_ms:0,kind:'copy_snapshot',key:'user:150',value:'snap-150',version:1},
    {at_ms:50,kind:'write',key:'user:100',value:'write-100-v2'},
    {at_ms:100,kind:'stream',key:'user:100',value:'write-100-v2',version:2},
    {at_ms:150,kind:'cutover'},
    {at_ms:200,kind:'write',key:'user:100',value:'late-write',route_hint:'old'},
  ],
  note:'存量拷贝、增量写入与切流并发交错的时序编排',
};

const kindDoc=[
  ['write / read','请求带键值；write 另需 value，route_hint=old 表示旧路由迟到写'],
  ['copy_snapshot','存量拷贝批次，需 key/value/version；旧版本不覆盖新写'],
  ['stream','增量复制包，需 key/value/version；重复版本幂等跳过'],
  ['cutover','切流；目标未逐键追平时拒绝，可在补齐后再次发起'],
  ['source_down / source_up','源分片故障与恢复，影响搬迁中写入与迟到写转发'],
  ['target_down / target_up','目标分片故障与恢复，故障窗复制包不应用，恢复后重试'],
];

export function migrationPlansPage(items){
  return `<div class="split"><section>
    <div class="section-top"><h2>按键范围搬迁脚本</h2><span>${items.length} PLANS / RANGE MIGRATION</span></div>
    ${table(['脚本 / 键范围','分片 源 → 目标','事件数','操作'],items.map(p=>[
      `<strong>${esc(p.code)}</strong><small>${esc(p.title)}</small><small>${esc(p.note)}</small>`,
      `${esc(p.source_shard)} → ${esc(p.target_shard)}<small>${esc(p.key_start)} .. ${esc(p.key_end)}</small>`,
      `${p.events.length} 个`,
      `<button class="small edit-plan" data-id="${p.id}" ${session.user.role==='viewer'?'disabled':''}>编辑</button>`]))}
    <div class="protocol"><h3>事件字段速查</h3>
      ${kindDoc.map(([k,d])=>`<p><b>${esc(k)}</b><br>${esc(d)}</p>`).join('')}
      <p>事件按 at_ms 非递减，同一时刻按输入先后执行，用于表达并发交错。搬迁中源分片持续接写；切流后范围键归属目标，旧路由迟到写由源转发或明确拒绝。</p>
    </div>
  </section>
  <section class="form-panel"><span class="eyebrow">RANGE MIGRATION PLAN</span>
    <h2 id="plan-heading">编排搬迁时序</h2>
    <form id="plan-form">
      <input type="hidden" name="id">
      <div class="fields">
        <label>脚本编号<input name="code" value="MG-1118-D" required></label>
        <label>场景标题<input name="title" value="按键范围在线搬迁演练" required></label>
      </div>
      <div class="fields">
        <label>键范围起<input name="key_start" value="user:100" required></label>
        <label>键范围止<input name="key_end" value="user:199" required></label>
      </div>
      <div class="fields">
        <label>源分片<input name="source_shard" value="shard-harbor" required></label>
        <label>目标分片<input name="target_shard" value="shard-bay" required></label>
      </div>
      <label>源分片初始数据 JSON<textarea name="source_initial" rows="4" required>${esc(JSON.stringify(samplePlan.source_initial,null,2))}</textarea></label>
      <label>目标分片初始数据 JSON<textarea name="target_initial" rows="3" required>${esc(JSON.stringify(samplePlan.target_initial,null,2))}</textarea></label>
      <label>搬迁时序事件 JSON<textarea name="plan_events" rows="14" required>${esc(JSON.stringify(samplePlan.events,null,2))}</textarea></label>
      <label>备注<input name="note" value="存量拷贝、增量写入与切流并发交错的时序编排"></label>
      <button class="primary" ${session.user.role==='viewer'?'disabled':''}>保存搬迁脚本 +</button>
    </form>
    <p class="help">保存前后端会用同一引擎试跑校验；非法时序返回 422，不会产生演练记录。</p>
  </section></div>`;
}

export const planSample=samplePlan;
