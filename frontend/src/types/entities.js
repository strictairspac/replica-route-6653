/** Node:{id,name,role,status,lineage,position,delay_ms}; RequestSet:{id,code,title,events,note}; Trial:{id,name,request_set_id,max_delay_ms,sticky_ms,input_snapshot,status,result,error}
 *  MigrationPlan:{id,code,title,key_start,key_end,source_shard,target_shard,source_initial,target_initial,events,note}
 *  Migration:{id,name,migration_plan_id,input_snapshot,status,result,error} */
export const entityNames=['nodes','request_sets','trials','migration_plans','migrations'];
