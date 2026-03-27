import bcrypt from 'bcrypt';
import { generateToken, hashToken } from '../../shared/crypto.js';
import { NotFoundError, ValidationError, ConflictError, WorkflowStateError } from '../../shared/errors.js';
import { auditFromContext, emitAuditEvent } from '../../shared/audit.js';
import type { ActorContext } from '../../shared/types.js';
import * as repo from './repository.js';
import { toUserDto, toInvitationDto } from './types.js';
import type { UserDto, InvitationDto } from './types.js';
import type { InviteUserInput, RegisterInput, UpdateUserInput, ListUsersQuery } from './schemas.js';

const BCRYPT_ROUNDS = 12;
const INVITATION_TTL_HOURS = 72;

// ---------------------------------------------------------------------------
// Invite user
// ---------------------------------------------------------------------------

export async function inviteUser(
  actor: ActorContext,
  input: InviteUserInput,
): Promise<{ invitation: InvitationDto; token: string }> {
  const firmId = actor.tenantId;

  // Idempotency: if there is already a pending invitation for this email, return it
  const existing = await repo.findPendingInvitationByEmail(firmId, input.email);
  if (existing) {
    // Return existing invitation without exposing the original token
    // Caller will not get a new token; they should use resend if needed
    return { invitation: toInvitationDto(existing), token: '' };
  }

  // Check if a user with this email already exists in the firm
  const existingUser = await repo.findUserByEmail(firmId, input.email);
  if (existingUser) {
    throw new ConflictError(`A user with email ${input.email} already exists in this firm`);
  }

  // Create a placeholder user row with 'invited' status
  const user = await repo.createUser({
    firmId,
    email: input.email,
    displayName: input.displayName,
    status: 'invited',
  });

  // Create the invitation with a secure token
  const rawToken = generateToken();
  const tokenHash = hashToken(rawToken);
  const expiresAt = new Date(Date.now() + INVITATION_TTL_HOURS * 60 * 60 * 1000);

  const invitation = await repo.createInvitation({
    firmId,
    email: input.email,
    role: input.role,
    displayName: input.displayName,
    invitedBy: actor.userId,
    tokenHash,
    expiresAt,
  });

  // Audit
  await auditFromContext(actor, 'user.invited', {
    resourceType: 'user',
    resourceId: user.id,
    metadata: { email: input.email, role: input.role, invitationId: invitation.id },
  });

  return { invitation: toInvitationDto(invitation), token: rawToken };
}

// ---------------------------------------------------------------------------
// Register via invitation token
// ---------------------------------------------------------------------------

export async function register(input: RegisterInput): Promise<UserDto> {
  const tokenHash = hashToken(input.token);
  const invitation = await repo.findInvitationByTokenHash(tokenHash);

  if (!invitation) {
    throw new ValidationError('Invalid or expired invitation token');
  }

  if (new Date() > invitation.expires_at) {
    // Mark as expired
    await repo.updateInvitationStatus(invitation.id, 'expired');
    throw new ValidationError('Invitation has expired');
  }

  // Hash the password
  const passwordHash = await bcrypt.hash(input.password, BCRYPT_ROUNDS);

  // Find the invited user
  const existingUser = await repo.findUserByEmail(invitation.firm_id, invitation.email);
  if (!existingUser) {
    throw new NotFoundError('User', invitation.email);
  }

  if (existingUser.status !== 'invited') {
    throw new WorkflowStateError('User has already been registered');
  }

  // Activate the user
  const user = await repo.updateUser(invitation.firm_id, existingUser.id, {
    displayName: input.displayName ?? undefined,
    status: 'active',
    passwordHash,
  });

  // Mark invitation as accepted
  await repo.updateInvitationStatus(invitation.id, 'accepted', { acceptedAt: new Date() });

  // Audit (system context since register is public)
  await emitAuditEvent({
    firmId: invitation.firm_id,
    actorId: user.id,
    actorType: 'user',
    action: 'user.registered',
    resourceType: 'user',
    resourceId: user.id,
    metadata: { email: user.email, invitationId: invitation.id },
  });

  return toUserDto(user);
}

// ---------------------------------------------------------------------------
// List users (tenant-scoped, paginated)
// ---------------------------------------------------------------------------

export async function listUsers(
  firmId: string,
  query: ListUsersQuery,
): Promise<{ users: UserDto[]; pagination: { nextCursor?: string; hasMore: boolean; totalCount: number } }> {
  const { rows, total } = await repo.listUsers(firmId, {
    cursor: query.cursor,
    limit: query.limit,
    status: query.status,
    search: query.search,
  });

  const hasMore = rows.length > query.limit;
  const pageRows = hasMore ? rows.slice(0, query.limit) : rows;
  const nextCursor = hasMore ? pageRows[pageRows.length - 1].id : undefined;

  return {
    users: pageRows.map(toUserDto),
    pagination: { nextCursor, hasMore, totalCount: total },
  };
}

// ---------------------------------------------------------------------------
// Get user
// ---------------------------------------------------------------------------

export async function getUser(firmId: string, userId: string): Promise<UserDto> {
  const row = await repo.findUserById(firmId, userId);
  if (!row) throw new NotFoundError('User', userId);
  return toUserDto(row);
}

// ---------------------------------------------------------------------------
// Update user
// ---------------------------------------------------------------------------

export async function updateUser(
  actor: ActorContext,
  userId: string,
  input: UpdateUserInput,
): Promise<UserDto> {
  const firmId = actor.tenantId;

  const existing = await repo.findUserById(firmId, userId);
  if (!existing) throw new NotFoundError('User', userId);

  // Detect status transitions for audit
  const statusChanging = input.status && input.status !== existing.status;

  const row = await repo.updateUser(firmId, userId, {
    displayName: input.displayName,
    status: input.status,
  });

  // Audit status changes
  if (statusChanging) {
    const action = input.status === 'disabled' ? 'user.disabled' : 'user.enabled';
    await auditFromContext(actor, action, {
      resourceType: 'user',
      resourceId: userId,
      metadata: { previousStatus: existing.status, newStatus: input.status! },
    });
  }

  return toUserDto(row);
}

// ---------------------------------------------------------------------------
// Resend invitation
// ---------------------------------------------------------------------------

export async function resendInvitation(
  actor: ActorContext,
  invitationId: string,
): Promise<{ invitation: InvitationDto; token: string }> {
  const invitation = await repo.findInvitationById(invitationId);
  if (!invitation) throw new NotFoundError('Invitation', invitationId);

  // Only pending invitations can be resent
  if (invitation.status !== 'pending') {
    throw new WorkflowStateError(`Cannot resend invitation with status '${invitation.status}'`);
  }

  // Ensure actor belongs to the same firm
  if (invitation.firm_id !== actor.tenantId) {
    throw new NotFoundError('Invitation', invitationId);
  }

  // Generate new token and extend expiry
  const rawToken = generateToken();
  const tokenHash = hashToken(rawToken);
  const expiresAt = new Date(Date.now() + INVITATION_TTL_HOURS * 60 * 60 * 1000);

  const updated = await repo.updateInvitationStatus(invitation.id, 'pending', {
    tokenHash,
    expiresAt,
  });

  await auditFromContext(actor, 'user.invited', {
    resourceType: 'invitation',
    resourceId: invitationId,
    metadata: { email: invitation.email, resend: true },
  });

  return { invitation: toInvitationDto(updated), token: rawToken };
}
