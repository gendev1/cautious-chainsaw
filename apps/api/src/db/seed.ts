import postgres from 'postgres';

const ROLES = [
  { name: 'firm_admin', description: 'Full firm access', is_system: true },
  { name: 'advisor', description: 'Client and portfolio management', is_system: true },
  { name: 'trader', description: 'Order submission and management', is_system: true },
  { name: 'operations', description: 'Transfers, onboarding, documents', is_system: true },
  { name: 'billing_admin', description: 'Fee and billing management', is_system: true },
  { name: 'viewer', description: 'Read-only access', is_system: true },
  { name: 'support_impersonator', description: 'Support impersonation access', is_system: true },
];

const PERMISSIONS = [
  'client.read', 'client.write',
  'account.open', 'account.read', 'account.write',
  'transfer.submit', 'transfer.cancel',
  'order.submit', 'order.cancel',
  'billing.read', 'billing.post', 'billing.reverse',
  'report.read', 'report.publish',
  'document.read', 'document.read_sensitive', 'document.write',
  'user.read', 'user.invite', 'user.manage_roles',
  'firm.read', 'firm.update',
  'support.impersonate',
  'mfa.manage',
];

const ROLE_PERMISSIONS: Record<string, string[]> = {
  firm_admin: [...PERMISSIONS], // all
  advisor: [
    'client.read', 'client.write', 'account.open', 'account.read', 'account.write',
    'transfer.submit', 'order.submit', 'billing.read', 'report.read',
    'document.read', 'document.write', 'user.read', 'firm.read',
  ],
  trader: ['client.read', 'account.read', 'order.submit', 'order.cancel', 'document.read'],
  operations: [
    'client.read', 'client.write', 'account.read', 'account.write',
    'transfer.submit', 'transfer.cancel', 'document.read', 'document.write', 'user.read',
  ],
  billing_admin: ['client.read', 'account.read', 'billing.read', 'billing.post', 'billing.reverse', 'report.read'],
  viewer: ['client.read', 'account.read', 'billing.read', 'report.read', 'document.read'],
  support_impersonator: ['support.impersonate', 'client.read', 'account.read', 'user.read'],
};

async function seed() {
  const databaseUrl = process.env.DATABASE_URL ?? 'postgres://wealth:wealth_dev@localhost:5432/wealth_advisor';
  const sql = postgres(databaseUrl);

  // Upsert roles
  for (const role of ROLES) {
    await sql`
      INSERT INTO roles (name, description, is_system)
      VALUES (${role.name}, ${role.description}, ${role.is_system})
      ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
    `;
  }
  console.log(`✓ Seeded ${ROLES.length} roles`);

  // Upsert permissions
  for (const perm of PERMISSIONS) {
    await sql`
      INSERT INTO permissions (name) VALUES (${perm})
      ON CONFLICT (name) DO NOTHING
    `;
  }
  console.log(`✓ Seeded ${PERMISSIONS.length} permissions`);

  // Map roles to permissions
  for (const [roleName, perms] of Object.entries(ROLE_PERMISSIONS)) {
    const [role] = await sql<{ id: string }[]>`SELECT id FROM roles WHERE name = ${roleName}`;
    if (!role) continue;

    // Clear existing mappings for this role
    await sql`DELETE FROM role_permissions WHERE role_id = ${role.id}`;

    for (const permName of perms) {
      const [perm] = await sql<{ id: string }[]>`SELECT id FROM permissions WHERE name = ${permName}`;
      if (!perm) continue;
      await sql`
        INSERT INTO role_permissions (role_id, permission_id)
        VALUES (${role.id}, ${perm.id})
        ON CONFLICT DO NOTHING
      `;
    }
  }
  console.log(`✓ Seeded role-permission mappings`);

  await sql.end();
  console.log('Seed complete.');
}

seed().catch((err) => {
  console.error('Seed failed:', err);
  process.exit(1);
});
