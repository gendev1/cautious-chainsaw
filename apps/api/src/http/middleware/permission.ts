import { createMiddleware } from 'hono/factory';
import { ForbiddenError } from '../../shared/errors.js';
import type { ActorContext } from '../../shared/types.js';

export function requirePermission(...required: string[]) {
  return createMiddleware(async (c, next) => {
    const actor = c.get('actor') as ActorContext | undefined;
    if (!actor) throw new ForbiddenError('No actor context');

    for (const perm of required) {
      if (!actor.permissions.includes(perm)) {
        throw new ForbiddenError(`Missing required permission: ${perm}`, { permission: perm });
      }
    }
    await next();
  });
}

export function requireAnyPermission(...required: string[]) {
  return createMiddleware(async (c, next) => {
    const actor = c.get('actor') as ActorContext | undefined;
    if (!actor) throw new ForbiddenError('No actor context');

    const has = required.some((p) => actor.permissions.includes(p));
    if (!has) {
      throw new ForbiddenError(`Missing one of: ${required.join(', ')}`);
    }
    await next();
  });
}

export function requireMfa() {
  return createMiddleware(async (c, next) => {
    const actor = c.get('actor') as ActorContext | undefined;
    if (!actor?.mfa) {
      throw new ForbiddenError('MFA required for this action');
    }
    await next();
  });
}

export function assertPermission(actor: ActorContext, permission: string): void {
  if (!actor.permissions.includes(permission)) {
    throw new ForbiddenError(`Missing required permission: ${permission}`, { permission });
  }
}
