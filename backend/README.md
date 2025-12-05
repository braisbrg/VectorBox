# CineMatch AI Backend

Production-grade FastAPI backend for movie recommendations.

## Security Features
- Rate limiting (SlowAPI)
- CORS with strict origin control
- Trusted Host middleware
- Input validation (Pydantic)
- File upload sanitization
- Non-root Docker container
- Security headers (X-Frame-Options, CSP, etc.)
- SQL injection prevention (SQLAlchemy ORM)

## Setup

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

## Environment Variables
See `.env.example` in project root.
