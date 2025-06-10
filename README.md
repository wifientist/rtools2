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
- 💿 postgresql database
- ↔️ Direct access to R1 API via services heirarchy

## Getting Started

### Installation

Install the dependencies:

```bash
npm install
```

Note: may get warnings about dependencies, especially for react-json-view.  Just --force the install per the notes you will see and use at your own peril.  

Create a python virtual environment and install dependencies:
```bash
cd /api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Define local .env variables per the .env.example provided.
Check vite.config.js for frontend variables as well.  
Define local /api/.env variables per the .env.example provided.

### Database

Project uses a postgresql database on the backend.  
Managed via alembic for migrations.

### Development Locally

Start the development server:

```bash
npm run dev
```

Start the API:

```bash
cd /api
source .venv/bin/activate
uvicorn --host 0.0.0.0 --port 4174
```

Your application will be available at `http://localhost:4173` and your IP available at `http://localhost:4174/docs`. 

## Building for Production

If you're familiar with deploying Node applications, the built-in app server is production-ready.

Make sure to deploy the output of `npm run build`

From there, use Caddy (or similar options) to serve the more efficient/static build. 

Again, refer to the .env files for setting behavior appropriately.  

## Styling

This project uses [Tailwind CSS](https://tailwindcss.com/) for a simple default styling experience. You can use whatever CSS framework you prefer.