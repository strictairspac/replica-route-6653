import {migrationsApi} from '../api/migrations.js';
export const migrationsStore={items:[],async refresh(){this.items=await migrationsApi.list();return this.items;}};
