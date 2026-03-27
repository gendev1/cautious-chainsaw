import { readdir, readFile } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import postgres from 'postgres';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function migrate() {
  const databaseUrl = process.env.DATABASE_URL ?? 'postgres://wealth:wealth_dev@localhost:5432/wealth_advisor';
  const sql = postgres(databaseUrl);

  await sql`
    CREATE TABLE IF NOT EXISTS _migrations (
      id SERIAL PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `;

  const applied = await sql<{ name: string }[]>`SELECT name FROM _migrations ORDER BY id`;
  const appliedSet = new Set(applied.map((r) => r.name));

  const migrationsDir = join(__dirname, 'migrations');
  const files = (await readdir(migrationsDir)).filter((f) => f.endsWith('.sql')).sort();

  for (const file of files) {
    if (appliedSet.has(file)) continue;
    console.log(`Applying migration: ${file}`);
    const content = await readFile(join(migrationsDir, file), 'utf-8');
    await sql.begin(async (tx: any) => {
      await tx.unsafe(content);
      await tx`INSERT INTO _migrations (name) VALUES (${file})`;
    });
    console.log(`  ✓ ${file}`);
  }

  console.log('All migrations applied.');
  await sql.end();
}

migrate().catch((err) => {
  console.error('Migration failed:', err);
  process.exit(1);
});
