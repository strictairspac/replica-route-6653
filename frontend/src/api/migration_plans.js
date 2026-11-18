import {api} from './client.js';
export const migrationPlansApi={list:()=>api('/migration_plans'),create:data=>api('/migration_plans','POST',data),update:(id,data)=>api('/migration_plans/'+id,'PUT',data)};
