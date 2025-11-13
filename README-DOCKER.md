# Docker Setup for Ruckus Tools

This guide explains how to run the Ruckus Tools application using Docker and Docker Compose.

## Architecture

The application consists of three services:
- **frontend**: React + Vite app served by Nginx (production) or Vite dev server (development)
- **backend**: FastAPI application running with Uvicorn
- **db**: PostgreSQL 15 database

All services communicate via a Docker bridge network.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose V2
- Git

## Quick Start

### Development Mode

1. **Clone the repository** (if not already done):
   ```bash
   cd /home/omni/code/rtools2
   ```

2. **Create environment file**:
   ```bash
   cp .env.example .env
   # Edit .env with your actual development credentials
   # Docker Compose automatically loads .env file
   ```

3. **Start all services**:
   ```bash
   docker compose -f docker-compose.dev.yml up
   ```

4. **Access the application**:
   - Frontend: http://localhost:4173
   - Backend API: http://localhost:4174
   - API Docs: http://localhost:4174/api/docs

5. **Stop services**:
   ```bash
   docker compose -f docker-compose.dev.yml down
   ```

### Production Mode

1. **Configure environment**:
   ```bash
   cp .env.example .env.production
   # Edit with production values (use strong passwords!)
   ```

2. **Build and start services**:
   ```bash
   docker compose --env-file .env.production up -d --build
   ```

3. **Access the application**:
   - Frontend: http://localhost (port 80)
   - Backend API: http://localhost:4174

4. **View logs**:
   ```bash
   docker compose logs -f
   ```

5. **Stop services**:
   ```bash
   docker compose down
   ```

## Environment Variables

### Required Variables

Create `.env.docker.local` from `.env.docker` template and configure:

- `DB_PASSWORD`: PostgreSQL password
- `AUTH_SECRET_KEY`: JWT secret key (generate a strong random string)
- `FERNET_KEY`: Encryption key (generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
- `CORS_ORIGINS`: Comma-separated allowed origins

### Email Configuration

Choose either SendGrid or Mailgun:

**SendGrid:**
- `SMTP_SERVER`: smtp.sendgrid.net
- `SMTP_PORT`: 587
- `SMTP_USERNAME`: apikey
- `WEBAPI_KEY`: Your SendGrid API key
- `SMTP_PASSWORD`: Your SendGrid password
- `FROM_EMAIL`: Sender email address

**Mailgun:**
- `MAILGUN_API_KEY`: Your Mailgun API key
- `MAILGUN_SENDING_KEY`: Your Mailgun sending key
- `MAILGUN_DOMAIN`: Your Mailgun domain

## Service Management

### Rebuild a specific service
```bash
docker compose build backend
docker compose up -d backend
```

### View service logs
```bash
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f db
```

### Execute commands in a running container
```bash
# Access backend shell
docker compose exec backend sh

# Run migrations manually
docker compose exec backend alembic upgrade head

# Access database
docker compose exec db psql -U postgres -d rtools2
```

### Scale services (production only)
```bash
# Note: Database cannot be scaled
docker compose up -d --scale backend=3
```

## Database Management

### Run migrations
Migrations run automatically on container startup, but can be run manually:
```bash
docker compose exec backend alembic upgrade head
```

### Create a new migration
```bash
docker compose exec backend alembic revision --autogenerate -m "Description"
```

### Access PostgreSQL shell
```bash
docker compose exec db psql -U postgres -d rtools2
```

### Backup database
```bash
docker compose exec db pg_dump -U postgres rtools2 > backup_$(date +%Y%m%d_%H%M%S).sql
```

### Restore database
```bash
cat backup.sql | docker compose exec -T db psql -U postgres rtools2
```

## Development Workflow

### Hot Reload
Both frontend and backend support hot reload in development mode:
- Frontend: Changes to `.tsx`, `.ts`, `.css` files trigger automatic reload
- Backend: Changes to `.py` files trigger Uvicorn auto-reload

### Install new dependencies

**Frontend:**
```bash
# Option 1: Install locally then rebuild
npm install <package>
docker compose -f docker-compose.dev.yml build frontend
docker compose -f docker-compose.dev.yml up -d frontend

# Option 2: Install inside container
docker compose -f docker-compose.dev.yml exec frontend npm install <package>
```

**Backend:**
```bash
# Add to api/requirements.txt, then:
docker compose -f docker-compose.dev.yml build backend
docker compose -f docker-compose.dev.yml up -d backend
```

### Running tests
```bash
# Backend tests
docker compose exec backend pytest

# Frontend tests
docker compose exec frontend npm test
```

## Production Deployment

### Using Docker Compose on a server

1. **Install Docker & Docker Compose** on your server

2. **Clone repository**:
   ```bash
   git clone <your-repo-url>
   cd rtools2
   ```

3. **Configure environment**:
   ```bash
   cp .env.docker .env.docker.local
   # Edit with production values
   ```

4. **Deploy**:
   ```bash
   docker compose --env-file .env.docker.local up -d --build
   ```

5. **Setup reverse proxy** (Nginx/Caddy) for HTTPS

### Using with Caddy (Recommended)

Create a `Caddyfile`:
```
ruckustools.rossho.me {
    reverse_proxy localhost:80
}

api.ruckustools.rossho.me {
    reverse_proxy localhost:4174
}
```

Run Caddy:
```bash
caddy run
```

### Health Checks

All services have health checks configured:
- Frontend: HTTP check on port 80
- Backend: HTTP check on `/api/status`
- Database: `pg_isready` command

View health status:
```bash
docker compose ps
```

## Troubleshooting

### Database connection errors
```bash
# Check if database is running
docker compose ps db

# View database logs
docker compose logs db

# Restart database
docker compose restart db
```

### Backend won't start
```bash
# Check logs
docker compose logs backend

# Common issues:
# 1. Database not ready - wait a few seconds and retry
# 2. Missing environment variables - check .env (dev) or .env.production (prod)
# 3. Port already in use - change port mapping in docker-compose.yml
```

### Frontend build failures
```bash
# Clear node_modules and rebuild
docker compose down
docker volume rm rtools2_frontend_node_modules
docker compose build frontend --no-cache
docker compose up frontend
```

### Reset everything
```bash
# WARNING: This deletes all data!
docker compose down -v
docker compose up -d --build
```

## File Structure
```
rtools2/
├── docker-compose.yml          # Production configuration
├── docker-compose.dev.yml      # Development configuration
├── .env.example               # Environment template (committed - safe)
├── .env                       # Development config (gitignored - YOUR SECRETS)
├── .env.production            # Production config (gitignored - YOUR SECRETS)
├── Dockerfile                  # Frontend Dockerfile
├── nginx.conf                  # Nginx configuration
├── .dockerignore              # Docker ignore rules
├── api/
│   ├── Dockerfile             # Backend Dockerfile
│   ├── .dockerignore          # Backend ignore rules
│   ├── wait-for-db.py        # Database readiness script
│   └── ... (FastAPI code)
└── ... (React code)
```

## Security Notes

### ⚠️ CRITICAL: Never Commit Secrets

1. **Environment files to NEVER commit**:
   - `.env` (development credentials)
   - `.env.local`
   - `.env.production` (production credentials)
   - `api/.env`

2. **Safe to commit** (template only):
   - `.env.example` (template with placeholder values only)

### Best Practices

1. **Use strong passwords** for all databases (dev AND prod)
2. **Generate unique keys**:
   ```bash
   # AUTH_SECRET_KEY (random string)
   openssl rand -hex 32

   # FERNET_KEY (specific format)
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. **Configure CORS** properly - never use `*` in production
4. **Use HTTPS** in production with a reverse proxy (Caddy/Nginx)
5. **Regular backups** of the database volume
6. **Rotate secrets regularly** (quarterly minimum)
7. **Different credentials** for dev/staging/production
8. **Use Docker secrets** for production deployments (swarm/kubernetes)

## Performance Tips

1. Use `.dockerignore` to exclude unnecessary files
2. Multi-stage builds reduce final image size
3. Volume mounts for node_modules prevent conflicts
4. Use `--build` flag only when dependencies change
5. Consider using Docker BuildKit for faster builds:
   ```bash
   DOCKER_BUILDKIT=1 docker compose build
   ```

## Additional Resources

- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [React Router Documentation](https://reactrouter.com/)
- [PostgreSQL Docker Image](https://hub.docker.com/_/postgres)

## Support

For issues or questions:
1. Check logs: `docker compose logs -f`
2. Review this documentation
3. Check Docker and service health: `docker compose ps`
4. Create an issue in the repository
