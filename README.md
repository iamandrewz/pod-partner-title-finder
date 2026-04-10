# Pod Partner Title Finder — Standalone

AI-powered YouTube title generation for podcasters. Extracts transcripts, generates winning titles, and researches patterns on YouTube.

## What This Does

- **Title Finder** (`/title-finder`): Submit a YouTube URL + podcast type → get Gold/Silver/Bronze ranked titles with YouTube view data
- **Title Lab V3** (`/v3`): Interactive title generation with topic extraction, YouTube search, and title mimicry

## Architecture

```
frontend/          Next.js 14 (port 3102)
  app/
    title-finder/  Title Finder page
    v3/            V3 Title Lab page
    login/         Login page
    api/           Proxy routes → backend

backend/           Flask (port 5003)
  app.py           Main Flask app
  title_finder.py  Core title finding logic
  v3_optimizer.py  V3 optimize/mimic logic
  episode_optimizer_v3.py  Episode optimizer
  youtube_transcript.py  Transcript extraction
  title_scorer.py  YouTube scoring
  optimizer/       AI client (MiniMax/OpenAI)
```

## Setup

### Prerequisites
- Python 3.9+
- Node.js 18+
- API keys (see `.env`)

### Backend Setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your OPENAI_API_KEY, GEMINI_API_KEY, etc.

# Start
python app.py
# Runs on http://localhost:5003
```

### Frontend Setup

```bash
cd frontend
npm install

# Start dev server
npm run dev
# Runs on http://localhost:3102
```

### Start Both (from project root)

```bash
./start-local.sh
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Backend port | `5003` |
| `OPENAI_API_KEY` | OpenAI API key | Required |
| `GEMINI_API_KEY` | Gemini API key | Optional |
| `SESSION_SECRET` | Auth session secret | `pursue-podcasting-secret-2024` |
| `ALLOWED_EMAILS` | Comma-separated allowed emails | Default list |
| `SHARED_PASSWORD` | Login password | `PursuePodcasting!Team1` |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/title-finder` | Submit a title-finding job |
| GET | `/api/title-finder/status/<job_id>` | Poll job status/results |
| POST | `/api/v3/optimize` | V3 title optimization |
| POST | `/api/v3/mimic` | Mimic a YouTube title pattern |
| POST | `/api/auth/login` | Login |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/users` | Get allowed users |
| GET | `/api/health` | Health check |

## Deploy

### Railway (Recommended)

1. Create new Railway project
2. Add `backend/` as a Python service on port 5003
3. Add `frontend/` as a Node service on port 3102
4. Set environment variables
5. Deploy

### Docker

```bash
# Backend
cd backend
docker build -t pod-partner-title-finder .
docker run -p 5003:5003 --env-file .env pod-partner-title-finder

# Frontend
cd frontend
docker build -t pod-partner-title-finder-frontend .
docker run -p 3102:3000 pod-partner-title-finder-frontend
```

## Podcasts Supported

- `spp` — Savage Perspective Podcast
- `jpi` — Just Pursue It
- `sbs` — Silverback Summit
- `wow` — Wisdom of Wrench
- `agp` — Ali Gilbert Podcast
- `generic` — No guardrails (pure AI)
