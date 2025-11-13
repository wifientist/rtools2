# Environment Files - Simple Guide

## The Simple Truth

You only need **ONE template** and **TWO actual config files**:

```
.env.example       ← Template (committed to git) - SAFE, no real secrets
.env               ← Development (gitignored) - YOUR DEV SECRETS
.env.production    ← Production (gitignored) - YOUR PROD SECRETS
```

## Why This Way?

### ❌ Old Confusing Way
```
.env.docker
.env.docker.local
.env.dev.example
.env.development
.env.local
```
**Problem:** Too many files, confusing naming!

### ✅ New Simple Way
```
.env.example       → Copy to .env (dev) or .env.production (prod)
```
**Benefit:** One template, clear naming, less confusion!

## Usage

### Development
```bash
# 1. Copy template
cp .env.example .env

# 2. Edit with your dev credentials
nano .env

# 3. Docker Compose auto-loads .env
docker compose -f docker-compose.dev.yml up
```

### Production
```bash
# 1. Copy template
cp .env.example .env.production

# 2. Edit with your STRONG production credentials
nano .env.production

# 3. Explicitly load production file
docker compose --env-file .env.production up -d
```

## What Gets Committed?

| File | Committed? | Why? |
|------|-----------|------|
| `.env.example` | ✅ Yes | Template only, no real secrets |
| `.env` | ❌ Never | Contains your dev secrets |
| `.env.production` | ❌ Never | Contains your prod secrets |

## .gitignore Protection

Your `.gitignore` already protects you:
```gitignore
.env
.env.local
.env.production
```

## Key Takeaway

**One template (`.env.example`), two configs (`.env` for dev, `.env.production` for prod).**

That's it! 🎯
