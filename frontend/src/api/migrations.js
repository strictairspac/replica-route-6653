import {api} from './client.js';
import {session} from '../hooks/session.js';
export const migrationsApi={list:()=>api('/migrations'),create:data=>api('/migrations','POST',data),detail:id=>api('/migrations/'+id)};
export async function downloadMigration(id){
  const res=await fetch('/api/migrations/'+id+'/export',{headers:{Authorization:'Bearer '+session.token}});
  if(!res.ok)throw Error('导出失败，请重新登录后重试');
  const url=URL.createObjectURL(await res.blob());
  const a=document.createElement('a');a.href=url;a.download='migration-'+id+'.json';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
