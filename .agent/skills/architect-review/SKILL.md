---
name: architect-review
description: Architecture review enforcing STACK_RULES
---
# Architect Review

When asked to review code, you must first read `STACK_RULES.md` to identify the core constraints.

**Enforce the following:**
1. **Async Patterns**: Ensure AsyncQdrant usage.
2. **Pydantic V2**: Verify Pydantic V2 compatibility.
3. **Next.js 16**: Check for RCE patches and Next.js 16 compatibility.

Reject legacy patterns immediately.

## Output Requirement
When performing a review, you must output a **Compliance Checklist**:

| Rule ID | Check | Status | Notes |
| :--- | :--- | :--- | :--- |
| **ASYNC** | Is `AsyncQdrantClient` used? | ✅/❌ | |
| **PYDANTIC** | Is `model_dump()` used? | ✅/❌ | |
| **NEXTJS** | Are RCE patches applied? | ✅/❌ | |

If any item is ❌, provide the refactored code block immediately below.
