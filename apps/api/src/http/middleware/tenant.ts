import { createMiddleware } from 'hono/factory';
import { getDb } from '../../db/client.js';
import { getRedis } from '../../db/redis.js';
import { getConfig } from '../../config.js';
import { TenantNotFoundError } from '../../shared/errors.js';
import type { TenantContext } from '../../shared/types.js';

export const tenantMiddleware = createMiddleware(async (c, next) => {
  const config = getConfig();
  const host = c.req.header('host') ?? '';

  // Local dev override
  let slug: string | undefined = c.req.header('x-tenant-slug');
  if (!slug) {
    const match = host.match(new RegExp(`^([a-z0-9-]+)\\.${config.BASE_DOMAIN.replace('.', '\\.')}$`));
    slug = match?.[1];
  }

  // Fallback for localhost dev
  if (!slug && (host.startsWith('localhost') || host.startsWith('127.0.0.1'))) {
    slug = c.req.header('x-tenant-slug');
  }

  if (!slug) {
    throw new TenantNotFoundError('unknown');
  }

  // Check Redis cache first
  const redis = getRedis();
  const cacheKey = `tenant:${slug}`;
  const cached = await redis.get(cacheKey);

  let tenant: TenantContext;

  if (cached) {
    tenant = JSON.parse(cached);
  } else {
    const sql = getDb();
    const [row] = await sql<{ id: string; name: string; slug: string; status: string }[]>`
      SELECT id, name, slug, status FROM firms WHERE slug = ${slug}
    `;

    if (!row) {
      throw new TenantNotFoundError(slug);
    }

    tenant = {
      tenantId: row.id,
      firmSlug: row.slug,
      firmName: row.name,
      firmStatus: row.status,
    };

    await redis.set(cacheKey, JSON.stringify(tenant), 'EX', config.TENANT_CACHE_TTL);
  }

  if (tenant.firmStatus !== 'active') {
    throw new TenantNotFoundError(slug, true);
  }

  c.set('tenant', tenant);
  c.set('tenantId', tenant.tenantId);
  await next();
});
