export class AppError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly statusCode: number = 500,
    public readonly details?: Record<string, string>,
  ) {
    super(message);
    this.name = 'AppError';
  }
}

export class ValidationError extends AppError {
  constructor(message: string, details?: Record<string, string>) {
    super('VALIDATION_ERROR', message, 422, details);
  }
}

export class NotFoundError extends AppError {
  constructor(resource: string, id?: string) {
    super('RESOURCE_NOT_FOUND', id ? `${resource} ${id} not found` : `${resource} not found`, 404);
  }
}

export class UnauthorizedError extends AppError {
  constructor(message = 'Unauthorized') {
    super('UNAUTHORIZED', message, 401);
  }
}

export class ForbiddenError extends AppError {
  constructor(message = 'Forbidden', details?: Record<string, string>) {
    super('FORBIDDEN', message, 403, details);
  }
}

export class ConflictError extends AppError {
  constructor(message: string) {
    super('IDEMPOTENCY_CONFLICT', message, 409);
  }
}

export class WorkflowStateError extends AppError {
  constructor(message: string) {
    super('INVALID_WORKFLOW_STATE', message, 422);
  }
}

export class RateLimitError extends AppError {
  constructor(retryAfter: number) {
    super('RATE_LIMITED', `Rate limit exceeded. Retry after ${retryAfter}s`, 429);
  }
}

export class TenantNotFoundError extends AppError {
  constructor(slug: string, inactive = false) {
    super(
      'TENANT_NOT_FOUND',
      inactive ? `Tenant ${slug} is not active` : `Tenant not found`,
      inactive ? 403 : 404,
    );
  }
}
