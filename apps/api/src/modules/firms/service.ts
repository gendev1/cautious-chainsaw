import * as repo from './repository.js';
import { toFirmDto, type FirmDto } from './types.js';
import type { CreateFirmInput, UpdateFirmInput } from './schemas.js';
import type { ActorContext } from '../../shared/types.js';
import { ConflictError, NotFoundError, WorkflowStateError } from '../../shared/errors.js';
import { emitAuditEvent } from '../../shared/audit.js';
import { getRedis } from '../../db/redis.js';

function firmCacheKey(firmId: string): string {
  return `firm:${firmId}`;
}

function tenantCacheKey(slug: string): string {
  return `tenant:${slug}`;
}

async function invalidateCache(firmId: string, slug?: string): Promise<void> {
  const redis = getRedis();
  const keys = [firmCacheKey(firmId)];
  if (slug) keys.push(tenantCacheKey(slug));
  await redis.del(...keys);
}

export async function createFirm(
  input: CreateFirmInput,
  actor: ActorContext,
): Promise<FirmDto> {
  const existing = await repo.findBySlug(input.slug);
  if (existing) {
    throw new ConflictError(`Firm with slug "${input.slug}" already exists`);
  }

  const row = await repo.create({
    name: input.name,
    slug: input.slug,
    branding: input.branding ?? {},
  });

  await emitAuditEvent({
    firmId: row.id,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'firm.created',
    resourceType: 'firm',
    resourceId: row.id,
    metadata: { name: row.name, slug: row.slug },
  });

  return toFirmDto(row);
}

export async function getCurrentFirm(tenantId: string): Promise<FirmDto> {
  const redis = getRedis();
  const cached = await redis.get(firmCacheKey(tenantId));
  if (cached) {
    return JSON.parse(cached) as FirmDto;
  }

  const row = await repo.findById(tenantId);
  if (!row) {
    throw new NotFoundError('Firm', tenantId);
  }

  const dto = toFirmDto(row);
  await redis.set(firmCacheKey(tenantId), JSON.stringify(dto), 'EX', 300);
  return dto;
}

export async function updateFirm(
  tenantId: string,
  input: UpdateFirmInput,
  actor: ActorContext,
): Promise<FirmDto> {
  const existing = await repo.findById(tenantId);
  if (!existing) {
    throw new NotFoundError('Firm', tenantId);
  }

  const row = await repo.update(tenantId, {
    name: input.name,
    branding: input.branding,
  });

  await invalidateCache(tenantId, existing.slug);

  await emitAuditEvent({
    firmId: tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'firm.updated',
    resourceType: 'firm',
    resourceId: tenantId,
    metadata: { changes: input as Record<string, unknown> },
  });

  return toFirmDto(row);
}

export async function suspendFirm(
  tenantId: string,
  actor: ActorContext,
): Promise<FirmDto> {
  const existing = await repo.findById(tenantId);
  if (!existing) {
    throw new NotFoundError('Firm', tenantId);
  }
  if (existing.status !== 'active') {
    throw new WorkflowStateError(
      `Cannot suspend firm with status "${existing.status}". Only active firms can be suspended.`,
    );
  }

  const row = await repo.updateStatus(tenantId, 'suspended');
  await invalidateCache(tenantId, existing.slug);

  await emitAuditEvent({
    firmId: tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'firm.suspended',
    resourceType: 'firm',
    resourceId: tenantId,
  });

  return toFirmDto(row);
}

export async function activateFirm(
  tenantId: string,
  actor: ActorContext,
): Promise<FirmDto> {
  const existing = await repo.findById(tenantId);
  if (!existing) {
    throw new NotFoundError('Firm', tenantId);
  }
  if (existing.status !== 'provisioning' && existing.status !== 'suspended') {
    throw new WorkflowStateError(
      `Cannot activate firm with status "${existing.status}". Only provisioning or suspended firms can be activated.`,
    );
  }

  const row = await repo.updateStatus(tenantId, 'active');
  await invalidateCache(tenantId, existing.slug);

  await emitAuditEvent({
    firmId: tenantId,
    actorId: actor.userId,
    actorType: actor.actorType,
    action: 'firm.activated',
    resourceType: 'firm',
    resourceId: tenantId,
  });

  return toFirmDto(row);
}
