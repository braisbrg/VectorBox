---
name: security-audit
description: Run backend and frontend security audits
---
# Security Audit

## Instructions
1. **Backend Audit**: 
   - Run `python backend/scripts/security_audit.py` inside the backend container or locally if environment matches.
2. **Frontend Audit**: 
   - Run `pnpm audit` in the frontend directory.
3. **Auto-Fix Protocol**: 
   - If `minimum-release-age` errors occur for whitelisted packages, auto-fix the `.npmrc` or approve the build.
