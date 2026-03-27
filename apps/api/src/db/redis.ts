import Redis from 'ioredis';
import { getConfig } from '../config.js';

let _redis: Redis | null = null;

export function getRedis(): Redis {
  if (_redis) return _redis;
  const config = getConfig();
  _redis = new Redis(config.REDIS_URL, {
    maxRetriesPerRequest: 3,
    lazyConnect: true,
  });
  return _redis;
}
