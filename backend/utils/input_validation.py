from fastapi import HTTPException
import re

MAX_QUERY_LENGTH = 500

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?instructions",
    r"act\s+as\s+",
    r"you\s+are\s+now",
    r"jailbreak",
    r"system\s+prompt",
    r"forget\s+(all\s+)?previous",
    r"disregard\s+(all\s+)?",
    r"new\s+persona",
    r"pretend\s+(you\s+are|to\s+be)",
    r"override\s+(your\s+)?instructions",
    r"<\s*script",           # XSS attempt
    r";\s*(drop|delete|insert|update|select)\s+",  # SQLi attempt
]

# Compile patterns for better performance
COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

def validate_user_query(query: str) -> str:
    """
    Validates a user-supplied query string before passing to LLM.
    Raises HTTPException 400 if the query is too long or matches
    known injection patterns. Returns the stripped query if clean.
    """
    if not query:
        return ""
        
    query = query.strip()
    
    if len(query) > MAX_QUERY_LENGTH:
        raise HTTPException(status_code=400, detail="Query too long")
        
    for pattern in COMPILED_PATTERNS:
        if pattern.search(query):
            raise HTTPException(status_code=400, detail="Invalid query format detected")
            
    return query
