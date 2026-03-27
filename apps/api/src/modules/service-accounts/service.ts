import * as repo from './repository.js';
import { toServiceAccountDto, type ServiceAccountDto } from './types.js';
import type { CreateServiceAccountInput } from './schemas.js';
import type { ActorContext } from '../../shared/types.js';
import { ConflictError, NotFoundError, WorkflowStateError } from '../../shared/errors.js';
import { emitAuditEvent } from '../../shared/audit.js';
import { generateToken, hashToken } from '../../shared/crypto.js';

const KEY_PREFIX = 'wsa_';
const DEFAULT_GRACE_HOURS = 24;

export interface ServiceAccountCreatedResult {
  account: ServiceAccountDto;
  apiKey: string;
}

export interface RotateKeyResult {
  account: ServiceAccountDto;
  apiKey: string;
  graceExpiresAt: string;
}

export async function createServiceAccount(
  input: CreateServiceAccountInput,
  actor: ActorContext,
): Promise<ServiceAccountCreatedResult> {
  const rawKey = KEY_PREFIX + generateToken(32);
  const keyHash = hashToken(rawKey);

  const row = await repo.create({
    name: input.name,
    firmId: actor.tenantId,
    keyHash,
    permissions: input.permissions,
  });

  await emitAuditEvent({
    firmId: actor.tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'service_account.created',
    resourceType: 'service_account',
    resourceId: row.id,
    metadata: { name: input.name, permissions: input.permissions },
  });

  return {
    account: toServiceAccountDto(row),
    apiKey: rawKey,
  };
}

export async function listServiceAccounts(actor: ActorContext): Promise<ServiceAccountDto[]> {
  const rows = await repo.list(actor.tenantId);
  return rows.map(toServiceAccountDto);
}

export async function rotateKey(
  id: string,
  actor: ActorContext,
): Promise<RotateKeyResult> {
  const existing = await repo.findById(id, actor.tenantId);
  if (!existing) {
    throw new NotFoundError('Service account', id);
  }
  if (existing.status === 'revoked') {
    throw new WorkflowStateError('Cannot rotate key for a revoked service account');
  }

  const rawKey = KEY_PREFIX + generateToken(32);
  const newKeyHash = hashToken(rawKey);
  const graceExpiresAt = new Date(Date.now() + DEFAULT_GRACE_HOURS * 60 * 60 * 1000);

  const row = await repo.rotateKey(id, actor.tenantId, newKeyHash, graceExpiresAt);

  await emitAuditEvent({
    firmId: actor.tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'service_account.key_rotated',
    resourceType: 'service_account',
    resourceId: id,
    metadata: { graceExpiresAt: graceExpiresAt.toISOString() },
  });

  return {
    account: toServiceAccountDto(row),
    apiKey: rawKey,
    graceExpiresAt: graceExpiresAt.toISOString(),
  };
}

export async function revokeServiceAccount(
  id: string,
  actor: ActorContext,
): Promise<ServiceAccountDto> {
  const existing = await repo.findById(id, actor.tenantId);
  if (!existing) {
    throw new NotFoundError('Service account', id);
  }
  if (existing.status === 'revoked') {
    throw new WorkflowStateError('Service account is already revoked');
  }

  const row = await repo.revoke(id, actor.tenantId);

  await emitAuditEvent({
    firmId: actor.tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'service_account.revoked',
    resourceType: 'service_account',
    resourceId: id,
    metadata: { name: existing.name },
  });

  return toServiceAccountDto(row);
}
