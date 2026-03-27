import { z } from 'zod';

const householdStatusSchema = z.enum(['active', 'inactive', 'closed']);

const serviceTeamMemberSchema = z.object({
  userId: z.string().uuid(),
  role: z.string().min(1).max(100),
});

export const createHouseholdSchema = z.object({
  name: z.string().trim().min(1, 'Household name is required').max(255),
  primaryAdvisorId: z.string().uuid('Primary advisor ID must be a valid UUID'),
  serviceTeam: z.array(serviceTeamMemberSchema).optional().default([]),
  notes: z.string().max(5000).optional().nullable(),
});

export const updateHouseholdSchema = z.object({
  name: z.string().trim().min(1, 'Household name is required').max(255).optional(),
  primaryAdvisorId: z.string().uuid('Primary advisor ID must be a valid UUID').optional(),
  serviceTeam: z.array(serviceTeamMemberSchema).optional(),
  notes: z.string().max(5000).optional().nullable(),
});

export const listHouseholdsQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(25),
  offset: z.coerce.number().int().min(0).default(0),
  status: householdStatusSchema.optional(),
  primaryAdvisorId: z.string().uuid().optional(),
});

export const householdResponseSchema = z.object({
  id: z.string().uuid(),
  tenantId: z.string().uuid(),
  name: z.string(),
  status: householdStatusSchema,
  primaryAdvisorId: z.string().uuid(),
  serviceTeam: z.array(serviceTeamMemberSchema),
  notes: z.string().nullable(),
  createdAt: z.string(),
  updatedAt: z.string(),
  createdBy: z.string(),
});

export type CreateHouseholdInput = z.infer<typeof createHouseholdSchema>;
export type UpdateHouseholdInput = z.infer<typeof updateHouseholdSchema>;
export type ListHouseholdsQuery = z.infer<typeof listHouseholdsQuerySchema>;
