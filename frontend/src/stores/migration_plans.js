import {migrationPlansApi} from '../api/migration_plans.js';
export const migrationPlansStore={items:[],async refresh(){this.items=await migrationPlansApi.list();return this.items;}};
