import { auditFromContext } from '../../shared/audit.js';
import { publishDomainEvent } from '../../shared/events.js';
import { NotFoundError, ValidationError, WorkflowStateError } from '../../shared/errors.js';
import type { ActorContext } from '../../shared/types.js';
import * as householdRepo from './repository.js';
import * as userRepo from '../users/repository.js';
import type { CreateHouseholdInput, ListHouseholdsQuery, UpdateHouseholdInput } from './schemas.js';
import type { HouseholdDto, HouseholdStatus } from './types.js';
import { toHouseholdDto } from './types.js';

async function ensureAdvisorExists(tenantId: string, advisorUserId: string): Promise<void> {
  const advisor = await userRepo.findUserById(tenantId, advisorUserId);
  if (!advisor) {
    throw new ValidationError('Primary advisor must reference an existing user in the tenant', {
      primaryAdvisorId: 'Primary advisor was not found in this tenant',
    });
  }
}

async function emitHouseholdEvent(
  type: 'household.created' | 'household.updated' | 'household.status_changed',
  household: HouseholdDto,
  metadata: Record<string, unknown> = {},
): Promise<void> {
  await publishDomainEvent({
    type,
    tenantId: household.tenantId,
    resourceId: household.id,
    occurredAt: new Date().toISOString(),
    payload: {
      household,
      ...metadata,
    },
  });
}

export async function createHousehold(
  actor: ActorContext,
  input: CreateHouseholdInput,
): Promise<HouseholdDto> {
  await ensureAdvisorExists(actor.tenantId, input.primaryAdvisorId);

  const row = await householdRepo.create({
    tenantId: actor.tenantId,
    name: input.name,
    primaryAdvisorId: input.primaryAdvisorId,
    serviceTeam: input.serviceTeam,
    notes: input.notes,
    createdBy: actor.userId,
  });

  const household = toHouseholdDto(row);

  await auditFromContext(actor, 'household.created', {
    resourceType: 'household',
    resourceId: household.id,
    metadata: { status: household.status, primaryAdvisorId: household.primaryAdvisorId },
  });
  await emitHouseholdEvent('household.created', household);

  return household;
}

export async function listHouseholds(
  tenantId: string,
  query: ListHouseholdsQuery,
): Promise<{ households: HouseholdDto[]; pagination: { hasMore: boolean; totalCount: number } }> {
  const { rows, total } = await householdRepo.list(tenantId, query);
  return {
    households: rows.map(toHouseholdDto),
    pagination: {
      hasMore: query.offset + rows.length < total,
      totalCount: total,
    },
  };
}

export async function getHousehold(tenantId: string, householdId: string): Promise<HouseholdDto> {
  const row = await householdRepo.findById(tenantId, householdId);
  if (!row) {
    throw new NotFoundError('Household', householdId);
  }
  return toHouseholdDto(row);
}

export async function updateHousehold(
  actor: ActorContext,
  householdId: string,
  input: UpdateHouseholdInput,
): Promise<HouseholdDto> {
  const existing = await householdRepo.findById(actor.tenantId, householdId);
  if (!existing) {
    throw new NotFoundError('Household', householdId);
  }

  if (input.primaryAdvisorId) {
    await ensureAdvisorExists(actor.tenantId, input.primaryAdvisorId);
  }

  const row = await householdRepo.update(actor.tenantId, householdId, input);
  const household = toHouseholdDto(row);

  await auditFromContext(actor, 'household.updated', {
    resourceType: 'household',
    resourceId: householdId,
    metadata: { previousStatus: existing.status, changes: input as Record<string, unknown> },
  });
  await emitHouseholdEvent('household.updated', household);

  return household;
}

async function transitionStatus(
  actor: ActorContext,
  householdId: string,
  nextStatus: HouseholdStatus,
): Promise<HouseholdDto> {
  const existing = await householdRepo.findById(actor.tenantId, householdId);
  if (!existing) {
    throw new NotFoundError('Household', householdId);
  }

  if (existing.status === nextStatus) {
    return toHouseholdDto(existing);
  }

  if (nextStatus === 'inactive' && existing.status !== 'active') {
    throw new WorkflowStateError(`Cannot deactivate household with status "${existing.status}"`);
  }

  if (nextStatus === 'active') {
    if (existing.status !== 'inactive') {
      throw new WorkflowStateError(`Cannot reactivate household with status "${existing.status}"`);
    }
  }

  if (nextStatus === 'closed') {
    if (existing.status === 'closed') {
      return toHouseholdDto(existing);
    }

    const blockingAccounts = await householdRepo.countBlockingAccounts(actor.tenantId, householdId);
    if (blockingAccounts > 0) {
      throw new WorkflowStateError('Cannot close household with active or restricted accounts');
    }
  }

  const row = await householdRepo.updateStatus(actor.tenantId, householdId, nextStatus);
  const household = toHouseholdDto(row);

  await auditFromContext(actor, 'household.status_changed', {
    resourceType: 'household',
    resourceId: householdId,
    metadata: {
      previousStatus: existing.status,
      newStatus: nextStatus,
    },
  });
  await emitHouseholdEvent('household.status_changed', household, {
    previousStatus: existing.status,
    newStatus: nextStatus,
  });

  return household;
}

export function deactivateHousehold(actor: ActorContext, householdId: string): Promise<HouseholdDto> {
  return transitionStatus(actor, householdId, 'inactive');
}

export function reactivateHousehold(actor: ActorContext, householdId: string): Promise<HouseholdDto> {
  return transitionStatus(actor, householdId, 'active');
}

export function closeHousehold(actor: ActorContext, householdId: string): Promise<HouseholdDto> {
  return transitionStatus(actor, householdId, 'closed');
}
