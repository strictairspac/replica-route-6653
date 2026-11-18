import {api,download} from './api/client.js';
import {nodesStore} from './stores/nodes.js';
import {request_setsStore} from './stores/request_sets.js';
import {trialsStore} from './stores/trials.js';
import {migrationPlansStore} from './stores/migration_plans.js';
import {migrationsStore} from './stores/migrations.js';
import {session} from './hooks/session.js';
import {feedback} from './hooks/feedback.js';
import {formValues} from './utils/forms.js';
import {esc,date} from './utils/html.js';
import {frame} from './components/common/frame.js';
import {table} from './components/common/table.js';
import {loginPage} from './pages/login.js';
import {nodesPage} from './pages/nodes.js';
import {requestSetsPage} from './pages/request_sets.js';
import {trialsPage} from './pages/trials.js';
import {evidencePage} from './pages/evidence.js';
import {migrationPlansPage} from './pages/migration_plans.js';
import {migrationsPage} from './pages/migrations.js';
import {migrationEvidencePage} from './pages/migration_evidence.js';
import {downloadMigration} from './api/migrations.js';
import {titles} from './router/routes.js';
let timer=null;const app=document.querySelector('#app');
function bindForm(id,path,transform,after){document.querySelector(id)?.addEventListener('submit',async e=>{e.preventDefault();const b=e.target.querySelector('button');b.disabled=true;try{const data=transform(formValues(e.target));const ident=data.id;delete data.id;const result=await api(path+(ident?'/'+ident:''),ident?'PUT':'POST',data);await after(result);feedback('保存成功，记录已更新。')}catch(err){feedback(err.message.includes('JSON')?'JSON格式错误，请检查引号与逗号':err.message,true)}finally{b.disabled=false}})}
async function render(){clearTimeout(timer);if(!session.token){app.innerHTML=loginPage();bindForm('#login','/auth/login',x=>x,async x=>{session.save(x);location.hash='nodes';await render()});return}
try{await api('/auth/me');await Promise.all([nodesStore.refresh(),request_setsStore.refresh(),trialsStore.refresh(),migrationPlansStore.refresh(),migrationsStore.refresh()]);const [page='nodes',id]=location.hash.slice(1).split('/');const nodes=nodesStore.items,sets=request_setsStore.items,trials=trialsStore.items,plans=migrationPlansStore.items,migrations=migrationsStore.items;let content,trial,migration;
if(page==='request_sets')content=requestSetsPage(sets);
else if(page==='trials')content=trialsPage(trials,sets);
else if(page==='evidence'){trial=id?await api('/trials/'+id):trials[0];content=evidencePage(trial)}
else if(page==='migration_plans')content=migrationPlansPage(plans);
else if(page==='migrations')content=migrationsPage(migrations,plans);
else if(page==='migration_evidence'){migration=id?await api('/migrations/'+id):migrations[0];content=migrationEvidencePage(migration)}
else if(page==='audit'){const rows=await api('/audit');content=table(['时间','操作人','动作'],rows.map(x=>[date(x.created_at),esc(x.actor),esc(x.action)]))}
else content=nodesPage(nodes);
const [title,subtitle]=titles[page]||titles.nodes;app.innerHTML=frame(page,title,subtitle,content);document.querySelector('#logout').onclick=()=>{session.clear();render()};
bindForm('#node-form','/nodes',x=>({...x,position:Number(x.position),delay_ms:Number(x.delay_ms)}),render);
bindForm('#request-form','/request_sets',x=>({...x,events:JSON.parse(x.events)}),render);
bindForm('#trial-form','/trials',x=>({...x,request_set_id:Number(x.request_set_id),max_delay_ms:Number(x.max_delay_ms),sticky_ms:Number(x.sticky_ms)}),async x=>{location.hash='evidence/'+x.id;await render()});
bindForm('#plan-form','/migration_plans',x=>({...x,source_initial:JSON.parse(x.source_initial),target_initial:JSON.parse(x.target_initial),events:JSON.parse(x.plan_events)}),render);
bindForm('#migration-form','/migrations',x=>({...x,migration_plan_id:Number(x.migration_plan_id)}),async x=>{location.hash='migration_evidence/'+x.id;await render()});
document.querySelectorAll('.edit-node').forEach(b=>b.onclick=()=>{const n=nodes.find(x=>x.id===Number(b.dataset.id)),form=document.querySelector('#node-form');for(const key of ['id','name','role','status','lineage','position','delay_ms','note'])form.elements[key].value=n[key];document.querySelector('#node-heading').textContent='修改节点 #'+n.id;form.scrollIntoView({behavior:'smooth',block:'center'})});
document.querySelectorAll('.edit-request').forEach(b=>b.onclick=()=>{const s=sets.find(x=>x.id===Number(b.dataset.id)),form=document.querySelector('#request-form');for(const key of ['id','code','title','note'])form.elements[key].value=s[key];form.elements.events.value=JSON.stringify(s.events,null,2);document.querySelector('#request-heading').textContent='修改请求集 #'+s.id;form.scrollIntoView({behavior:'smooth',block:'center'})});
document.querySelectorAll('.edit-plan').forEach(b=>b.onclick=()=>{const p=plans.find(x=>x.id===Number(b.dataset.id)),form=document.querySelector('#plan-form');for(const key of ['id','code','title','key_start','key_end','source_shard','target_shard','note'])form.elements[key].value=p[key];form.elements.source_initial.value=JSON.stringify(p.source_initial,null,2);form.elements.target_initial.value=JSON.stringify(p.target_initial,null,2);form.elements.plan_events.value=JSON.stringify(p.events,null,2);document.querySelector('#plan-heading').textContent='修改搬迁脚本 #'+p.id;form.scrollIntoView({behavior:'smooth',block:'center'})});
document.querySelector('#export')?.addEventListener('click',async e=>{try{await download(e.target.dataset.id);feedback('路由JSON已下载')}catch(err){feedback(err.message,true)}});
document.querySelector('#migration-export')?.addEventListener('click',async e=>{try{await downloadMigration(e.target.dataset.id);feedback('搬迁JSON已下载')}catch(err){feedback(err.message,true)}});
const busy=trials.some(x=>['queued','running'].includes(x.status))||migrations.some(x=>['queued','running'].includes(x.status))
  ||(page==='evidence'&&trial&&['queued','running'].includes(trial.status))
  ||(page==='migration_evidence'&&migration&&['queued','running'].includes(migration.status));
if(busy)timer=setTimeout(render,1500);
}catch(err){if(err.message.includes('登录')||err.message.includes('Not authenticated')){session.clear();await render()}else{if(!document.querySelector('#feedback'))app.innerHTML='<main><div id="feedback"></div><button onclick="location.reload()">重新加载</button></main>';feedback(err.message,true)}}}
window.addEventListener('hashchange',render);render();
