import postgres from 'postgres';
import { getConfig } from '../config.js';

let _sql: postgres.Sql | null = null;

export function getDb(): postgres.Sql {
  if (_sql) return _sql;
  const config = getConfig();
  _sql = postgres(config.DATABASE_URL, {
    max: 20,
    idle_timeout: 20,
    connect_timeout: 10,
    types: {
      bigint: postgres.BigInt,
    },
  });
  return _sql;
}

export type Sql = postgres.Sql;
export type Row = postgres.Row;
