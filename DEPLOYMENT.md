# Deployment Guide

Complete guide for deploying Ruckus Tools to production using the automated deployment script.

## Prerequisites

1. **Server Requirements:**
   - Docker Engine 20.10+
   - Docker Compose V2
   - Git
   - `flock` utility (usually pre-installed on Linux)
   - 2GB+ RAM recommended
   - 10GB+ disk space

2. **Repository Setup:**
   - Git repository cloned on server
   - SSH key or credentials configured for git access

3. **Environment Configuration:**
   - `.env.production` file created and configured

## Quick Start

### First-Time Setup

1. **Clone repository to `/opt/rtools2` on server:**
   ```bash
   sudo mkdir -p /opt/rtools2
   sudo chown $USER:$USER /opt/rtools2
   git clone <your-repo-url> /opt/rtools2
   cd /opt/rtools2
   ```

2. **Create production environment:**
   ```bash
   cp .env.example .env.production
   nano .env.production
   ```

3. **Configure required variables:**
   ```bash
   # Database
   DB_PASSWORD=<strong-random-password>

   # Security keys
   AUTH_SECRET_KEY=$(openssl rand -hex 32)
   FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

   # CORS (your production domains)
   CORS_ORIGINS=https://ruckustools.rossho.me,https://api.ruckustools.rossho.me

   # Email (SendGrid or Mailgun)
   SMTP_SERVER=smtp.sendgrid.net
   WEBAPI_KEY=<your-sendgrid-key>
   # ... etc

   # Optional: Slack notifications
   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
   GITHUB_REPO_URL=https://github.com/yourusername/rtools2
   API_HOST=api.ruckustools.rossho.me
   ```

4. **Make deploy script executable:**
   ```bash
   chmod +x deploy.sh
   ```

5. **Run first deployment:**
   ```bash
   ./deploy.sh
   ```

### Subsequent Deployments

Simply run:
```bash
cd /opt/rtools2
./deploy.sh
```

Or from anywhere:
```bash
/opt/rtools2/deploy.sh
```

The script will:
- Pull latest code from `main` branch
- Build Docker images
- Start/restart services
- Run database migrations
- Clean up old Docker resources
- Send Slack notification (if configured)

## Deployment Script Features

### 🔒 Safety Features

1. **Concurrency Lock:** Prevents multiple deployments running simultaneously
2. **Commit Tracking:** Saves previous commit for easy rollback
3. **Retry Logic:** Attempts database migrations up to 3 times
4. **Health Checks:** Verifies API is responding after deployment

### 🔧 What It Does

```
1. Locks deployment (prevents concurrent runs)
2. Saves current commit (for rollback)
3. Pulls latest code from main branch
4. Builds Docker images with --pull
5. Starts services in detached mode
6. Cleans up old Docker resources (>24h)
7. Waits for services to stabilize (15s)
8. Shows migration diagnostics
9. Runs database migrations (with 3 retries)
10. Verifies API health
11. Sends Slack notification
12. Shows final service status
```

### 📱 Slack Notifications

If `SLACK_WEBHOOK_URL` is configured, you'll receive rich notifications with:
- ✅ Deployment status
- 📦 Commit SHA (with GitHub link if configured)
- 💬 Commit message
- 👤 Committer name
- 🕒 Deployment timestamp

**Example notification:**
```
🚀 Ruckus Tools Deployed
━━━━━━━━━━━━━━━━━━━━━━━━
Status:        ✅ Successfully deployed to production
Environment:   Production
Commit:        abc123d (linked to GitHub)
Committer:     John Doe
Message:       Fix authentication bug
━━━━━━━━━━━━━━━━━━━━━━━━
🕒 2025-11-13 14:30:00 UTC
```

## Manual Operations

### View Logs
```bash
docker compose --env-file .env.production logs -f
docker compose --env-file .env.production logs -f backend
docker compose --env-file .env.production logs -f frontend
```

### Check Service Status
```bash
docker compose --env-file .env.production ps
```

### Restart a Service
```bash
docker compose --env-file .env.production restart backend
```

### Run Migrations Manually
```bash
docker compose --env-file .env.production exec backend alembic upgrade head
```

### Database Access
```bash
# PostgreSQL shell
docker compose --env-file .env.production exec db psql -U postgres -d rtools2

# Backend shell
docker compose --env-file .env.production exec backend sh
```

### Stop All Services
```bash
docker compose --env-file .env.production down
```

## Rollback Procedure

If a deployment fails or causes issues:

### Quick Rollback
```bash
# The deploy script shows the previous commit at the start
# Example output: "Previous commit: abc123d (for rollback)"

git reset --hard <previous-commit>
./deploy.sh
```

### Manual Rollback Steps
```bash
# 1. Find the commit to roll back to
git log --oneline -10

# 2. Reset to that commit
git reset --hard <commit-sha>

# 3. Rebuild and restart
docker compose --env-file .env.production down
docker compose --env-file .env.production up -d --build

# 4. Run migrations if needed
docker compose --env-file .env.production exec backend alembic upgrade head
```

## Troubleshooting

### Deploy Script Won't Run
```bash
# Check if lock file exists
ls -la /tmp/rtools2-deploy.lock

# Remove stale lock if needed
rm /tmp/rtools2-deploy.lock
```

### Migrations Fail
```bash
# Check migration files exist
docker compose --env-file .env.production exec backend ls -la /app/alembic/versions/

# View current migration state
docker compose --env-file .env.production exec backend alembic current

# View migration history
docker compose --env-file .env.production exec backend alembic history

# Manually run specific migration
docker compose --env-file .env.production exec backend alembic upgrade <revision>

# Force to head (use with caution)
docker compose --env-file .env.production exec backend alembic stamp head
```

### Services Won't Start
```bash
# Check logs for errors
docker compose --env-file .env.production logs

# Check environment variables loaded
docker compose --env-file .env.production config

# Verify ports not in use
sudo lsof -i :80
sudo lsof -i :4174
sudo lsof -i :5435

# Rebuild from scratch
docker compose --env-file .env.production down -v
docker compose --env-file .env.production up -d --build
```

### Disk Space Issues
```bash
# Check disk usage
df -h

# Clean Docker resources (more aggressive)
docker system prune -a --volumes
```

### Health Check Fails
```bash
# Test API manually
curl -v https://api.ruckustools.rossho.me/api/status

# Check backend logs
docker compose --env-file .env.production logs backend

# Check nginx logs (if using reverse proxy)
sudo journalctl -u nginx -f
```

## Automation

### Cron Job (Scheduled Deploys)

**⚠️ Not recommended for production** - deploy on-demand is safer.

But if you need it:
```bash
# Add to crontab
crontab -e

# Deploy every night at 2 AM
0 2 * * * /opt/rtools2/deploy.sh >> /var/log/rtools2-deploy.log 2>&1
```

### GitHub Actions / CI/CD

The deploy script can be triggered via SSH:

```yaml
# .github/workflows/deploy.yml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            /opt/rtools2/deploy.sh
```

## Post-Deployment Checklist

- [ ] Services are running: `docker compose --env-file .env.production ps`
- [ ] API is responding: `curl https://api.ruckustools.rossho.me/api/status`
- [ ] Frontend is accessible: `curl https://ruckustools.rossho.me`
- [ ] Database migrations completed: Check deploy logs
- [ ] No errors in logs: `docker compose --env-file .env.production logs --tail=50`
- [ ] Slack notification received (if configured)
- [ ] Test critical functionality (login, data fetch, etc.)

## Security Best Practices

1. **Never commit** `.env.production` to version control
2. **Use strong passwords** for database (20+ characters, random)
3. **Rotate secrets regularly** (quarterly minimum)
4. **Different credentials** for dev/staging/prod environments
5. **Limit SSH access** to deployment server
6. **Use SSH keys** instead of passwords
7. **Keep Docker updated** regularly
8. **Monitor disk space** to prevent service disruption
9. **Backup database** before major deployments
10. **Test in staging** before production deployment

## Backup Strategy

### Automated Backups
```bash
# Add to crontab for daily backups at 3 AM
0 3 * * * docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production exec -T db pg_dump -U postgres rtools2 | gzip > /backups/rtools2_$(date +\%Y\%m\%d).sql.gz
```

### Manual Backup
```bash
# Backup database
docker compose --env-file .env.production exec -T db pg_dump -U postgres rtools2 > backup_$(date +%Y%m%d_%H%M%S).sql

# Backup environment (encrypted)
tar -czf backup_env_$(date +%Y%m%d).tar.gz .env.production
gpg --symmetric backup_env_$(date +%Y%m%d).tar.gz
```

### Restore Database
```bash
# Stop backend first
docker compose --env-file .env.production stop backend

# Restore
cat backup_20251113.sql | docker compose --env-file .env.production exec -T db psql -U postgres rtools2

# Restart backend
docker compose --env-file .env.production start backend
```

## Support

For issues or questions:
1. Check logs: `docker compose --env-file .env.production logs -f`
2. Check service health: `docker compose --env-file .env.production ps`
3. Review [README-DOCKER.md](README-DOCKER.md) for Docker troubleshooting
4. Review [DOCKER-READY.md](DOCKER-READY.md) for quick reference

---

**Last Updated:** 2025-11-13
