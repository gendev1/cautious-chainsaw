import { z } from 'zod';

export const startImpersonationSchema = z.object({
  target_user_id: z.string().uuid('target_user_id must be a valid UUID'),
  reason: z.string().min(1, 'Reason is required').max(1000),
  duration_minutes: z
    .number()
    .int()
    .min(1, 'Duration must be at least 1 minute')
    .max(60, 'Duration cannot exceed 60 minutes'),
  idempotency_key: z
    .string()
    .min(1, 'Idempotency key is required')
    .max(255),
});

export const listImpersonationSessionsSchema = z.object({
  impersonator_user_id: z.string().uuid().optional(),
  target_user_id: z.string().uuid().optional(),
  started_after: z.string().datetime().optional(),
  started_before: z.string().datetime().optional(),
  cursor: z.string().uuid().optional(),
  limit: z.coerce.number().int().min(1).max(100).default(25),
});

export type StartImpersonationInput = z.infer<typeof startImpersonationSchema>;
export type ListImpersonationSessionsInput = z.infer<typeof listImpersonationSessionsSchema>;
