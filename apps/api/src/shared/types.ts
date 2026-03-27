export interface TenantContext {
  tenantId: string;
  firmSlug: string;
  firmName: string;
  firmStatus: string;
}

export interface ActorContext {
  userId: string;
  tenantId: string;
  actorType: 'user' | 'service' | 'impersonator';
  sessionId: string;
  roles: string[];
  permissions: string[];
  mfa: boolean;
  impersonatorId?: string;
}

export interface RequestContext {
  requestId: string;
  correlationId: string;
  tenant?: TenantContext;
  actor?: ActorContext;
}

export type FirmStatus = 'provisioning' | 'active' | 'suspended' | 'deactivated';
export type UserStatus = 'invited' | 'active' | 'disabled';
export type InvitationStatus = 'pending' | 'accepted' | 'expired' | 'revoked';
