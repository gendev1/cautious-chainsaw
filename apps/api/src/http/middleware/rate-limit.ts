import { createMiddleware } from 'hono/factory';
import { getRedis } from '../../db/redis.js';
import { getConfig } from '../../config.js';
import { RateLimitError } from '../../shared/errors.js';
import type { ActorContext } from '../../shared/types.js';

async function checkLimit(key: string, limit: number, windowSeconds: number): Promise<{ allowed: boolean; remaining: number; resetAt: number }> {
  const redis = getRedis();
  const now = Math.floor(Date.now() / 1000);
  const windowStart = now - windowSeconds;

  const pipeline = redis.pipeline();
  pipeline.zremrangebyscore(key, 0, windowStart);
  pipeline.zadd(key, now.toString(), `${now}:${Math.random()}`);
  pipeline.zcard(key);
  pipeline.expire(key, windowSeconds);

  const results = await pipeline.exec();
  const count = (results?.[2]?.[1] as number) ?? 0;
  const remaining = Math.max(0, limit - count);
  const resetAt = now + windowSeconds;

  return { allowed: count <= limit, remaining, resetAt };
}

export const rateLimitMiddleware = createMiddleware(async (c, next) => {
  const config = getConfig();
  const actor = c.get('actor') as ActorContext | undefined;

  // Skip rate limiting for service accounts
  if (actor?.actorType === 'service') {
    await next();
    return;
  }

  try {
    const tenantId = c.get('tenantId') as string | undefined;

    // Per-tenant limit
    if (tenantId) {
      const tenantResult = await checkLimit(
        `ratelimit:tenant:${tenantId}`,
        config.RATE_LIMIT_TENANT_RPM,
        60,
      );
      c.header('X-RateLimit-Limit', config.RATE_LIMIT_TENANT_RPM.toString());
      c.header('X-RateLimit-Remaining', tenantResult.remaining.toString());
      c.header('X-RateLimit-Reset', tenantResult.resetAt.toString());

      if (!tenantResult.allowed) {
        throw new RateLimitError(60);
      }
    }

    // Per-user limit
    if (actor?.userId) {
      const userResult = await checkLimit(
        `ratelimit:user:${actor.userId}`,
        config.RATE_LIMIT_USER_RPM,
        60,
      );
      if (!userResult.allowed) {
        throw new RateLimitError(60);
      }
    }
  } catch (err) {
    if (err instanceof RateLimitError) throw err;
    // Fail open on Redis errors
    console.warn('Rate limiting failed, allowing request:', err);
  }

  await next();
});

export const loginRateLimitMiddleware = createMiddleware(async (c, next) => {
  const config = getConfig();
  const ip = c.req.header('x-forwarded-for')?.split(',')[0]?.trim() ?? 'unknown';

  try {
    const result = await checkLimit(
      `ratelimit:login:${ip}`,
      config.RATE_LIMIT_LOGIN_RPM,
      60,
    );
    if (!result.allowed) {
      throw new RateLimitError(60);
    }
  } catch (err) {
    if (err instanceof RateLimitError) throw err;
    console.warn('Login rate limiting failed, allowing request:', err);
  }

  await next();
});
