import type { TenantContext, ActorContext } from './types.js';

export type AppEnv = {
  Variables: {
    requestId: string;
    correlationId: string;
    tenant: TenantContext;
    tenantId: string;
    actor: ActorContext;
  };
};
