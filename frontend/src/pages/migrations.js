import {table} from '../components/common/table.js';
import {badge} from '../components/common/badge.js';
import {esc,date} from '../utils/html.js';
import {session} from '../hooks/session.js';

export function migrationsPage(items,plans){
  return `<div class="split"><section>
    <div class="section-top"><h2>在线搬迁执行记录</h2><span>SNAPSHOT / STREAM / CUTOVER</span></div>
    ${table(['演练 / 时间','搬迁脚本','状态 / 阶段','证据'],items.map(m=>[
      `<strong>${esc(m.name)}</strong><small>${date(m.created_at)}</small>`,
      esc(plans.find(p=>p.id===m.migration_plan_id)?.code||('#'+m.migration_plan_id)),
      `${badge(m.status)}${m.result?`<small>阶段：${esc(m.result.phase)}</small>`:''}`,
      `<a href="#migration_evidence/${m.id}">查看 →</a>`]))}
    <div class="protocol"><h3>搬迁衔接口径（由引擎强制）</h3>
      <ol>
        <li>存量拷贝只写目标，逐键版本闸：incoming_version 不高于本地版本即跳过，旧拷贝不覆盖新写</li>
        <li>搬迁中源分片持续接写并产生新版本，增量复制与拷贝/写入可并发交错</li>
        <li>切流闸门：目标在线且逐键版本不低于源已提交位点，否则拒绝切流并给出每个缺口原因</li>
        <li>切流后旧路由迟到写：源可达则转发目标，源或目标不可达则明确拒绝，绝不回写旧主</li>
        <li>目标故障窗的复制包不应用，恢复后重发，同版本包幂等跳过，不重复应用数据</li>
      </ol>
    </div>
  </section>
  <section class="form-panel"><span class="eyebrow">RUN RANGE MIGRATION</span>
    <h2>发起一次在线搬迁</h2>
    <form id="migration-form">
      <label>演练名称<input name="name" value="range-shift-20261118" required></label>
      <label>搬迁脚本<select name="migration_plan_id">${plans.map(p=>`<option value="${p.id}">${esc(p.code)} · ${esc(p.title)}</option>`).join('')}</select></label>
      <button class="primary" ${session.user.role==='viewer'?'disabled':''}>冻结快照并执行 ↗</button>
    </form>
    <p class="help">冻结脚本快照后由后台 Python 逐事件重放；拒绝切流、拒绝迟到写都属于预期安全结果，不是任务失败。</p>
  </section></div>`;
}
