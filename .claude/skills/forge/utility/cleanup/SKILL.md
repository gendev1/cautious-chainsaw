---
name: cleanup
description: Remove or archive old .forge/features/ directories and reset state. Use when the user asks to clean up, remove old features, or archive completed work.
argument-hint: optional slug to delete, or "all"
user-invokable: true
---

# Forge Cleanup

Remove accumulated `.forge/features/` directories.

## Process

1. List all directories under `.forge/features/`:
   - Run `ls .forge/features/` and collect directory names
   - If none: "No feature directories found."
2. Determine target(s):
   - If `--feature {slug}` provided: target only that directory
   - If no argument: list all directories and ask user which to delete (by name or "all")
   - Additionally offer: "archive" — compress to `.forge/archive/{slug}-{date}.tar.gz` instead of deleting. This preserves history without cluttering the working directory.
3. Preview what will be deleted:
   - Show full path(s) of directories to remove
   - Show file count inside each: `find .forge/features/{slug} -type f | wc -l` files
4. Confirm:
   - If `--force`: skip confirmation, proceed
   - Otherwise: ask "Delete these directories? (yes/no)"
   - If user says no: abort, print "Cleanup cancelled."
5. Delete or archive:
   - If action is "delete": `rm -rf .forge/features/{slug}` — print: "Deleted .forge/features/{slug}"
   - If action is "archive": `mkdir -p .forge/archive && tar -czf .forge/archive/{slug}-$(date +%Y%m%d).tar.gz -C .forge/features {slug} && rm -rf .forge/features/{slug}` — print: "Archived .forge/features/{slug} to .forge/archive/{slug}-{date}.tar.gz"
6. Update state after deletion:
   ```bash
   ./skills/forge/tools/forge-state remove "{slug}"
   # Removes feature from state. Reassigns active if it was the deleted slug.
   ```

## CRITICAL

- ALWAYS show what will be deleted before deleting
- NEVER delete without confirmation unless `--force` is set
- After deletion, respond with: "Cleanup complete. Deleted N feature director(ies)."
