# Multi-stage build for React frontend

# Stage 1: Build
FROM node:20-alpine as build

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --legacy-peer-deps

# Copy source code
COPY . .

# Build application
RUN npm run build

# Stage 2: Production with Nginx
FROM nginx:alpine as production

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built files from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Expose port
EXPOSE 80

# Health check
#
# 127.0.0.1, not localhost. In this image localhost resolves to ::1 first and
# nginx.conf says `listen 80;` -- IPv4 only -- so every check got a connection
# refused and the container reported unhealthy for its entire life while serving
# the site perfectly well. Measured before the fix: FailingStreak 22 on a
# five-minute-old container, and never a single pass on any deploy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]

# Stage 3: Development (optional, can be used with docker-compose override)
FROM node:20-alpine as development

WORKDIR /app

COPY package*.json ./
RUN npm install --legacy-peer-deps

COPY . .

EXPOSE 4173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "4173"]
