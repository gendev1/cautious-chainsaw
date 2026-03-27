import { ValidationError } from '../../shared/errors.js';
import * as repo from './repository.js';
import { toAuditEventDto, type AuditEventDto, type AuditQueryFilters, type PaginatedAuditResult } from './types.js';

const MAX_DATE_RANGE_DAYS = 90;

function daysBetween(from: string, to: string): number {
  const msPerDay = 1000 * 60 * 60 * 24;
  return (new Date(to).getTime() - new Date(from).getTime()) / msPerDay;
}

export interface AuditQueryResult {
  events: AuditEventDto[];
  nextCursor: string | undefined;
  hasMore: boolean;
}

export async function queryAuditEvents(filters: AuditQueryFilters): Promise<AuditQueryResult> {
  // Validate date range does not exceed 90 days
  if (filters.from && filters.to) {
    const days = daysBetween(filters.from, filters.to);
    if (days > MAX_DATE_RANGE_DAYS) {
      throw new ValidationError(
        `Date range must not exceed ${MAX_DATE_RANGE_DAYS} days`,
        { from: filters.from, to: filters.to },
      );
    }
  }

  const result = await repo.query(filters);

  return {
    events: result.events.map(toAuditEventDto),
    nextCursor: result.nextCursor,
    hasMore: result.hasMore,
  };
}
