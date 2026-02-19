---
name: qa-certification
description: Run the full QA certification protocol
---
# QA Certification

Follow the `QA_TESTING_MANUAL.md` protocol for detailed steps.

## Instructions
1. **Infrastructure Check**: 
   - Run `docker ps` to verify container health.
2. **Magic Box Test**: 
   - Run the `test_magic_box.py` script.
   - Verify logic as per `QA_TESTING_MANUAL.md` Phase 4.
3. **Frontend Build**: 
   - Run `pnpm build` to verify the frontend builds successfully.

Report pass/fail for each step.
