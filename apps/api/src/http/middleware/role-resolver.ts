import { createMiddleware } from 'hono/factory';
import { getDb } from '../../db/client.js';
import { getRedis } from '../../db/redis.js';
import type { ActorContext } from '../../shared/types.js';

export const roleResolverMiddleware = createMiddleware(async (c, next) => {
  const actor = c.get('actor') as ActorContext | undefined;
  if (!actor) {
    await next();
    return;
  }

  // Service accounts carry permissions in the token
  if (actor.actorType === 'service') {
    await next();
    return;
  }

  const redis = getRedis();
  const cacheKey = `perms:${actor.userId}`;
  const cached = await redis.get(cacheKey);

  if (cached) {
    actor.permissions = JSON.parse(cached);
    c.set('actor', actor);
    await next();
    return;
  }

  // Resolve from DB
  const sql = getDb();
  const rows = await sql<{ permission_name: string }[]>`
    SELECT DISTINCT p.name AS permission_name
    FROM user_role_assignments ura
    JOIN role_permissions rp ON rp.role_id = ura.role_id
    JOIN permissions p ON p.id = rp.permission_id
    WHERE ura.user_id = ${actor.userId}
      AND ura.firm_id = ${actor.tenantId}
      AND ura.revoked_at IS NULL
  `;

  const permissions = rows.map((r) => r.permission_name);
  actor.permissions = permissions;
  c.set('actor', actor);

  // Cache for 30 seconds
  await redis.set(cacheKey, JSON.stringify(permissions), 'EX', 30);

  await next();
});
