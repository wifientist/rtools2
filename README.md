# Welcome to the new Ruckus Tools

Frontend built with Vite using React Router.
Backend built with FastAPI using Python.

## Frontend Features

- 🚀 Server-side rendering
- 🔒 TypeScript by default
- 🎉 TailwindCSS for styling
- 📖 [React Router docs](https://reactrouter.com/)

## Backend Features

- 1️⃣ OTP based logins
- 🔐 JWT auth for user session and management
- 💿 PostgreSQL database
- ↔️ Direct access to R1 API via services hierarchy

## Architecture

The application runs as a multi-container Docker setup:
- **Frontend**: React/Vite app (development) or Nginx (production)
- **Backend**: FastAPI with hot-reload
- **Database**: PostgreSQL 15
- **fossFLOW**: Integrated diagramming tool
- **Nginx**: Reverse proxy routing all traffic

## Getting Started

### Prerequisites

- Docker and Docker Compose installed
- `.env` file configured (see below)

### Configuration

Create your environment configuration:

```bash
cp .env.example .env
```

Edit `.env` and configure the following required variables:
- `DB_PASSWORD` - Secure password for PostgreSQL
- `AUTH_SECRET_KEY` - Generate with: `openssl rand -hex 32`
- `FERNET_KEY` - Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- Email provider settings (SendGrid or Mailgun)
- `CORS_ORIGINS` - Set to your domain(s)

See `.env.example` for complete configuration options and examples.

### Database

Project uses PostgreSQL managed via Alembic for migrations.
- Migrations run automatically on container startup
- Database persists via Docker volume `postgres_data` (production) or `postgres_data_dev` (development)

### Development

Start all services with hot-reload:

```bash
docker compose -f docker-compose.dev.yml up
```

This starts:
- Frontend at `http://localhost:4173` (with hot-reload)
- Backend API at `http://localhost:4174` (with hot-reload)
- Database at `http://localhost:5432`
- fossFLOW at `http://localhost:3000`
- Nginx proxy at `http://localhost:80`

Access API docs at `http://localhost:4174/docs`

**Hot-reload features:**
- Frontend code changes automatically reload in browser
- Backend code changes automatically restart uvicorn
- Database migrations run automatically on startup

To stop all services:

```bash
docker compose -f docker-compose.dev.yml down
```

To rebuild after dependency changes:

```bash
docker compose -f docker-compose.dev.yml up --build
```

### Production

Deploy to production:

```bash
docker compose up -d
```

This uses optimized production builds:
- Frontend: Pre-built static assets served by Nginx
- Backend: Uvicorn running in production mode
- All services behind Nginx reverse proxy on port 80

View logs:

```bash
docker compose logs -f
```

Stop production:

```bash
docker compose down
```

## Manual Development (Without Docker)

If you prefer to run services individually:

### Install Dependencies

```bash
npm install
```

Note: May get warnings about dependencies, especially for react-json-view. Use `npm install --force` if needed.

```bash
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run Services

Start the frontend:
```bash
npm run dev
```

Start the backend:
```bash
cd api
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 4174 --reload
```

Ensure PostgreSQL is running separately and configure `DATABASE_URL` in `api/.env`.  

## Styling

This project uses [Tailwind CSS](https://tailwindcss.com/) for a simple default styling experience. You can use whatever CSS framework you prefer.

## Integrated Tools

### fossFLOW Diagramming Tool

This project integrates [fossFLOW](https://github.com/stan-smith/fossFLOW), an open-source network diagramming tool forked and customized for Ruckus network visualization.

- **Original Project**: [stan-smith/fossFLOW](https://github.com/stan-smith/fossFLOW)
- **Runs on**: `http://localhost:3000` (development)
- **Features**: Network diagram creation, persistent storage, real-time collaboration