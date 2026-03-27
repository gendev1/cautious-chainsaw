import { serve } from '@hono/node-server';
import { loadConfig } from './config.js';
import { createApp } from './app.js';
import { getRedis } from './db/redis.js';

async function main() {
  const config = loadConfig();
  const app = createApp();

  // Connect Redis eagerly
  const redis = getRedis();
  await redis.connect();
  console.log('Redis connected');

  serve({ fetch: app.fetch, port: config.PORT }, (info) => {
    console.log(`API server running on http://localhost:${info.port}`);
  });
}

main().catch((err) => {
  console.error('Failed to start:', err);
  process.exit(1);
});
