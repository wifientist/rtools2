# Production Server Setup Guide

Quick reference for setting up Ruckus Tools in `/opt/rtools2` on a production server.

## Initial Server Setup

### 1. Install Prerequisites

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose V2 (if not included)
sudo apt install docker-compose-plugin -y

# Install Git
sudo apt install git -y

# Logout and login again for docker group to take effect
```

### 2. Create Deployment Directory

```bash
# Create /opt/rtools2 with correct permissions
sudo mkdir -p /opt/rtools2
sudo chown $USER:$USER /opt/rtools2

# Clone repository
git clone https://github.com/wifientist/rtools2.git /opt/rtools2
cd /opt/rtools2
```

### 3. Configure Production Environment

```bash
# Create production env file
cp .env.example .env.production

# Edit with production values
nano .env.production
```

**Required variables:**
```bash
# Database
DB_PASSWORD=$(openssl rand -base64 32)

# Security keys
AUTH_SECRET_KEY=$(openssl rand -hex 32)
FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# CORS - your actual domains
CORS_ORIGINS=https://ruckustools.rossho.me,https://api.ruckustools.rossho.me

# Email (choose SendGrid OR Mailgun)
SMTP_SERVER=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USERNAME=apikey
WEBAPI_KEY=your_actual_sendgrid_key
SMTP_PASSWORD=your_actual_sendgrid_password
FROM_EMAIL=no-reply@ruckus.tools

# Optional: Slack notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
GITHUB_REPO_URL=https://github.com/wifientist/rtools2
API_HOST=api.ruckustools.rossho.me
```

### 4. Make Deploy Script Executable

```bash
chmod +x /opt/rtools2/deploy.sh
```

### 5. Initial Deployment

```bash
cd /opt/rtools2
./deploy.sh
```

This will:
- Build Docker images
- Start all services
- Run database migrations
- Send Slack notification (if configured)

### 6. Verify Deployment

```bash
# Check service status
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production ps

# Check logs
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production logs -f

# Test API
curl http://localhost:4174/api/status
# Should return: {"status":"ok"}

# Test frontend
curl http://localhost
# Should return HTML
```

## Setup Reverse Proxy

### Option 1: Caddy (Recommended - Auto HTTPS)

```bash
# Install Caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy

# Create Caddyfile
sudo nano /etc/caddy/Caddyfile
```

**Caddyfile:**
```
# Frontend
ruckustools.rossho.me {
    reverse_proxy localhost:80
}

# Backend API
api.ruckustools.rossho.me {
    reverse_proxy localhost:4174
}
```

```bash
# Reload Caddy
sudo systemctl reload caddy

# Enable on boot
sudo systemctl enable caddy
```

### Option 2: Nginx (Manual HTTPS with Certbot)

```bash
# Install Nginx
sudo apt install nginx -y

# Create site config
sudo nano /etc/nginx/sites-available/rtools2
```

**Nginx config:**
```nginx
# Frontend
server {
    listen 80;
    server_name ruckustools.rossho.me;

    location / {
        proxy_pass http://localhost:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Backend API
server {
    listen 80;
    server_name api.ruckustools.rossho.me;

    location / {
        proxy_pass http://localhost:4174;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/rtools2 /etc/nginx/sites-enabled/

# Test config
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx

# Install Certbot for HTTPS
sudo apt install certbot python3-certbot-nginx -y

# Get SSL certificates
sudo certbot --nginx -d ruckustools.rossho.me -d api.ruckustools.rossho.me
```

## DNS Configuration

Point your domains to the server:

```
A Record: ruckustools.rossho.me → <server-ip>
A Record: api.ruckustools.rossho.me → <server-ip>
```

## Firewall Setup

```bash
# Allow HTTP, HTTPS, and SSH
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## Optional: Setup Backups

### Database Backups

```bash
# Create backup directory
sudo mkdir -p /backups/rtools2
sudo chown $USER:$USER /backups/rtools2

# Add to crontab
crontab -e
```

Add:
```bash
# Daily backup at 3 AM
0 3 * * * docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production exec -T db pg_dump -U postgres rtools2 | gzip > /backups/rtools2/rtools2_$(date +\%Y\%m\%d).sql.gz

# Delete backups older than 30 days
0 4 * * * find /backups/rtools2 -name "*.sql.gz" -mtime +30 -delete
```

## Optional: Monitoring

### Simple Health Check Script

```bash
nano /opt/rtools2/health-check.sh
```

```bash
#!/bin/bash
API_HOST="https://api.ruckustools.rossho.me"

if ! curl -sf "$API_HOST/api/status" > /dev/null; then
    echo "API health check failed at $(date)" | mail -s "Ruckus Tools Alert" your@email.com
fi
```

```bash
chmod +x /opt/rtools2/health-check.sh

# Add to crontab - check every 5 minutes
*/5 * * * * /opt/rtools2/health-check.sh
```

## Deployment Workflow

### Regular Deployments

```bash
# Simply run from anywhere:
/opt/rtools2/deploy.sh
```

### View Logs

```bash
# All services
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production logs -f

# Specific service
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production logs -f backend
```

### Rollback

```bash
cd /opt/rtools2
git log --oneline -10  # Find commit to rollback to
git reset --hard <commit-sha>
./deploy.sh
```

## Security Checklist

- [ ] Strong database password set
- [ ] Unique AUTH_SECRET_KEY generated
- [ ] Unique FERNET_KEY generated
- [ ] CORS_ORIGINS set to actual domains (not `*`)
- [ ] Firewall configured (UFW)
- [ ] HTTPS enabled (Caddy auto or Certbot)
- [ ] SSH key-based authentication only
- [ ] Regular backups configured
- [ ] `.env.production` file permissions: `chmod 600 .env.production`
- [ ] Docker images updated regularly
- [ ] Monitoring/alerts configured

## Quick Reference

```bash
# Deploy
/opt/rtools2/deploy.sh

# View logs
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production logs -f

# Check status
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production ps

# Restart service
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production restart backend

# Database shell
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production exec db psql -U postgres -d rtools2

# Backend shell
docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production exec backend sh
```

## Aliases (Optional)

Add to `~/.bashrc`:

```bash
# Ruckus Tools aliases
alias rt='cd /opt/rtools2'
alias rtdeploy='/opt/rtools2/deploy.sh'
alias rtlogs='docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production logs -f'
alias rtps='docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production ps'
alias rtrestart='docker compose -f /opt/rtools2/docker-compose.yml --env-file /opt/rtools2/.env.production restart'
```

Then reload: `source ~/.bashrc`

---

**Setup Date:** 2025-11-13
**Documentation:** See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed information
