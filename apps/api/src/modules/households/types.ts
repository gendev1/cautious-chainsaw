export type HouseholdStatus = 'active' | 'inactive' | 'closed';

export interface ServiceTeamMember {
  userId: string;
  role: string;
}

export interface HouseholdRow {
  id: string;
  tenant_id: string;
  name: string;
  status: HouseholdStatus;
  primary_advisor_id: string;
  service_team_json: ServiceTeamMember[];
  notes: string | null;
  created_at: Date;
  updated_at: Date;
  created_by: string;
}

export interface HouseholdDto {
  id: string;
  tenantId: string;
  name: string;
  status: HouseholdStatus;
  primaryAdvisorId: string;
  serviceTeam: ServiceTeamMember[];
  notes: string | null;
  createdAt: string;
  updatedAt: string;
  createdBy: string;
}

export function toHouseholdDto(row: HouseholdRow): HouseholdDto {
  return {
    id: row.id,
    tenantId: row.tenant_id,
    name: row.name,
    status: row.status,
    primaryAdvisorId: row.primary_advisor_id,
    serviceTeam: row.service_team_json ?? [],
    notes: row.notes,
    createdAt: row.created_at.toISOString(),
    updatedAt: row.updated_at.toISOString(),
    createdBy: row.created_by,
  };
}
