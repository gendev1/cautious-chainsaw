import type { ErrorHandler } from 'hono';
import { AppError } from '../../shared/errors.js';
import { ZodError } from 'zod';

export const errorHandler: ErrorHandler = (err, c) => {
  const requestId = c.get('requestId') ?? 'unknown';

  if (err instanceof AppError) {
    return c.json(
      {
        success: false,
        error: { code: err.code, message: err.message, details: err.details },
        requestId,
      },
      err.statusCode as any,
    );
  }

  if (err instanceof ZodError) {
    const details: Record<string, string> = {};
    for (const issue of err.issues) {
      details[issue.path.join('.')] = issue.message;
    }
    return c.json(
      {
        success: false,
        error: { code: 'VALIDATION_ERROR', message: 'Request validation failed', details },
        requestId,
      },
      422,
    );
  }

  console.error('Unhandled error:', err);
  return c.json(
    {
      success: false,
      error: { code: 'INTERNAL_ERROR', message: 'An internal error occurred' },
      requestId,
    },
    500,
  );
};
