# 🤖 Premium Bot

A **production-ready Telegram bot** built with enterprise-grade architecture.

## Tech Stack

| Layer        | Technology                          |
| ------------ | ----------------------------------- |
| Framework    | **Aiogram 3.x** (async)             |
| Language     | **Python 3.11+**                     |
| Database     | **PostgreSQL 16** + SQLAlchemy 2.0   |
| Cache / FSM  | **Redis 7**                          |
| Config       | **pydantic-settings**                |
| Migrations   | **Alembic** (async-aware)            |
| Logging      | **structlog** (JSON in production)   |
| Containers   | **Docker** + Docker Compose          |

## Project Structure

```
Premium Bot/
├── bot/                        # ← Telegram bot application
│   ├── __main__.py             #    Entrypoint (python -m bot)
│   ├── handlers/               #    Message & callback handlers
│   │   ├── common.py           #      /start, /help, /me, fallback
│   │   ├── admin.py            #      /stats, /ban, /unban (admin-only)
│   │   └── user.py             #      /feedback (regular users)
│   ├── middlewares/            #    Aiogram middleware
│   │   ├── database.py         #      Auto session + repo injection
│   │   └── throttling.py       #      Redis-backed rate limiter
│   ├── filters/                #    Custom Aiogram filters
│   │   └── admin.py            #      AdminFilter (checks ADMIN_IDS)
│   ├── keyboards/              #    Keyboard builders
│   │   ├── inline.py           #      InlineKeyboard factories
│   │   └── reply.py            #      ReplyKeyboard factories
│   ├── services/               #    Business logic layer
│   │   └── user.py             #      UserService
│   ├── states/                 #    FSM state groups
│   │   └── user.py             #      FeedbackForm, RegistrationForm
│   └── utils/                  #    Shared utilities
│       └── logging.py          #      structlog setup
├── config/                     # ← Configuration
│   └── settings.py             #    pydantic-settings (env vars)
├── database/                   # ← Data layer
│   ├── base.py                 #    Declarative base + audit columns
│   ├── engine.py               #    Async engine + session factory
│   ├── models/                 #    SQLAlchemy ORM models
│   │   └── user.py             #      User model
│   └── repositories/           #    Repository pattern (CRUD)
│       └── user.py             #      UserRepository
├── migrations/                 # ← Alembic migrations
│   ├── env.py                  #    Async-aware migration env
│   ├── script.py.mako          #    Migration template
│   └── versions/               #    Auto-generated migration files
├── tests/                      # ← Test suite
│   └── test_config.py          #    Config validation tests
├── .env.example                # ← Environment variable template
├── .gitignore
├── alembic.ini
├── docker-compose.yml          # ← Bot + PostgreSQL + Redis
├── Dockerfile
├── requirements.txt
└── README.md
```

## Quick Start

### 1. Clone & Configure

```bash
git clone <your-repo-url>
cd "Premium Bot"
cp .env.example .env
# Edit .env with your actual BOT_TOKEN, DB credentials, etc.
```

### 2. Run with Docker (Recommended)

```bash
docker-compose up -d --build
```

This starts the bot, PostgreSQL 16, and Redis 7 with health checks.

### 3. Run Locally (Development)

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Make sure PostgreSQL and Redis are running, then:
python -m bot
```

### 4. Database Migrations

```bash
# Generate a new migration after model changes
alembic revision --autogenerate -m "add_user_table"

# Apply all pending migrations
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

### 5. Run Tests

```bash
pytest tests/ -v
```

## Architecture Highlights

- **Config** — `pydantic-settings` loads `.env` with type validation and `SecretStr` for sensitive values
- **Repository Pattern** — All SQL is encapsulated in `database/repositories/`, never in handlers
- **Middleware DI** — `DatabaseMiddleware` auto-injects a `UserRepository` into every handler
- **Rate Limiting** — Redis-backed per-user throttling via `ThrottlingMiddleware`
- **Admin Guard** — `AdminFilter` applied at the router level restricts admin commands
- **FSM Storage** — Redis-backed FSM for multi-step conversation flows
- **Structured Logging** — Pretty console in dev, JSON in production via `structlog`

## License

MIT
