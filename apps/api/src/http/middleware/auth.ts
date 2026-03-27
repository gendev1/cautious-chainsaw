import { createMiddleware } from 'hono/factory';
import * as jose from 'jose';
import { getRedis } from '../../db/redis.js';
import { UnauthorizedError } from '../../shared/errors.js';
import type { AppEnv } from '../../shared/hono-env.js';

let _publicKey: Uint8Array | CryptoKey | null = null;

async function getPublicKey(): Promise<Uint8Array | CryptoKey> {
  if (_publicKey) return _publicKey;
  const raw = process.env.JWT_PUBLIC_KEY;
  if (raw) {
    _publicKey = await jose.importSPKI(raw, 'ES256');
  } else {
    // Dev fallback: use a symmetric secret
    _publicKey = new TextEncoder().encode(process.env.JWT_SECRET ?? 'dev-secret-change-me');
  }
  return _publicKey;
}

export const authMiddleware = createMiddleware(async (c, next) => {
  const header = c.req.header('authorization');
  if (!header?.startsWith('Bearer ')) {
    throw new UnauthorizedError('Missing Bearer token');
  }

  const token = header.slice(7);
  const key = await getPublicKey();

  let payload: jose.JWTPayload;
  try {
    const result = await jose.jwtVerify(token, key);
    payload = result.payload;
  } catch {
    throw new UnauthorizedError('Invalid or expired token');
  }

  // Validate tenant match
  const tenantId = c.get('tenantId');
  if (tenantId && payload.tid !== tenantId && payload.tid !== '*') {
    throw new UnauthorizedError('Token tenant mismatch');
  }

  // Check session revocation
  const sessionId = payload.sid as string;
  if (sessionId) {
    const redis = getRedis();
    const revoked = await redis.get(`revoked:session:${sessionId}`);
    if (revoked) {
      throw new UnauthorizedError('Session has been revoked');
    }
  }

  c.set('actor', {
    userId: payload.sub!,
    tenantId: payload.tid as string,
    actorType: (payload.act as string) ?? 'user',
    sessionId: sessionId ?? '',
    roles: (payload.roles as string[]) ?? [],
    permissions: [], // resolved in next middleware
    mfa: (payload.mfa as boolean) ?? false,
    impersonatorId: payload.imp as string | undefined,
  });

  await next();
});
