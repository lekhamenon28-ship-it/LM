import {DatabaseSync} from 'node:sqlite';
import {mkdirSync} from 'node:fs';
mkdirSync('.data',{recursive:true});
const sqlite=new DatabaseSync(process.env.AETHER_DB_PATH||'.data/auth.sqlite');
export const DB={prepare(sql){let args=[];return {bind(...values){args=values;return this},async run(){const result=sqlite.prepare(sql).run(...args);return {success:true,meta:{changes:Number(result.changes)}}},async first(){return sqlite.prepare(sql).get(...args)||null},async all(){return {results:sqlite.prepare(sql).all(...args)}}}}};
