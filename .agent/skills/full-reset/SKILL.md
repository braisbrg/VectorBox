---
name: full-reset
description: Execute a hard reset of the environment
---
# Full Reset Protocol

## Instructions
Execute the 'Hard Reset' protocol safely.

1. **Teardown**:
   - `docker-compose down -v`
2. **Rebuild & Start**:
   - Build containers.
   - Migrate database.
   - Seed database.
   - Scrape data (if applicable).

**Critical**: Ensure database is ready and schemas applied before seeding.
