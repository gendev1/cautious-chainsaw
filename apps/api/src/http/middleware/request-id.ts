import { createMiddleware } from 'hono/factory';
import { randomUUID } from 'node:crypto';

export const requestIdMiddleware = createMiddleware(async (c, next) => {
  const requestId = c.req.header('x-request-id') ?? randomUUID();
  const correlationId = c.req.header('x-correlation-id') ?? requestId;
  c.set('requestId', requestId);
  c.set('correlationId', correlationId);
  c.header('X-Request-Id', requestId);
  await next();
});
