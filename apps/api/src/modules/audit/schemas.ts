import { z } from 'zod';

const isoDateRegex = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?$/;

export const auditQuerySchema = z
  .object({
    actor_id: z.string().uuid().optional(),
    action: z.string().max(255).optional(),
    resource_type: z.string().max(128).optional(),
    resource_id: z.string().max(255).optional(),
    from: z
      .string()
      .regex(isoDateRegex, 'Must be an ISO 8601 date')
      .optional(),
    to: z
      .string()
      .regex(isoDateRegex, 'Must be an ISO 8601 date')
      .optional(),
    cursor: z.string().max(512).optional(),
    limit: z.coerce.number().int().min(1).max(100).default(50),
  })
  .refine(
    (data) => {
      if (data.from && data.to) {
        return new Date(data.to) >= new Date(data.from);
      }
      return true;
    },
    { message: '"to" must be on or after "from"', path: ['to'] },
  );

export type AuditQueryInput = z.infer<typeof auditQuerySchema>;
