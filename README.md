# GoalCast — AI-Powered Football Score Predictor

GoalCast predicts football match scores using Elo ratings and a Poisson model, then lets you track your own predictions against the AI across all major European leagues.

## Features

- **Real match data** from API-Football across 7 leagues (Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Liga Portugal, Champions League)
- **AI predictions** using Elo-based expected goals and Poisson distribution with confidence indicators
- **Personal predictions** — enter your own scores and compare against the AI
- **History & accuracy dashboard** with charts, stats, and dynamic insights
- **Global filters** by competition, team, and date — apply to both Matches and History views
- **SQLite persistence** — match history accumulates over time, survives restarts
- **Apple-clean design** — light/dark mode, responsive layout

## Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/goalcast.git
cd goalcast

# Install
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env and add your FOOTBALL_DATA_KEY from https://www.football-data.org/client/register

# Run
python main.py
# Open http://localhost:8000
```

## Deploy on Railway

1. Push to GitHub
2. Connect repo on [Railway](https://railway.app)
3. Add environment variable: `FOOTBALL_DATA_KEY=your_key`
4. Deploy — Railway reads the `Procfile` automatically

### Keep data fresh (recommended)

Railway's free tier sleeps containers after inactivity. To keep data updated, set up a free cron job:

1. Go to [cron-job.org](https://cron-job.org) and create a free account
2. Create a new cron job pointing to: `https://YOUR-APP.up.railway.app/api/cron-refresh`
3. Set schedule to every 4 hours
4. This wakes the container and triggers a data refresh if data is stale (>3 hours old)

### Preserve history across redeploys

Railway's free tier has an ephemeral filesystem — SQLite is wiped on redeploy. To preserve history:

```bash
# Export current DB to seed file
curl -s https://YOUR-APP.up.railway.app/api/export > data/seed.json
git add data/seed.json && git commit -m "update seed" && git push
```

The app automatically loads `data/seed.json` into the DB on startup if the DB is empty.

## Tech Stack

- **Backend:** Python, FastAPI, SQLite, httpx
- **Frontend:** HTML, Tailwind CSS (CDN), Alpine.js, Chart.js
- **Data:** football-data.org (fixtures), ClubElo (Elo ratings)
- **Model:** Poisson distribution with Elo-to-xG conversion

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/matches` | All matches from DB (instant) |
| POST | `/api/refresh` | Fetch fresh data from APIs → save to DB |
| POST | `/api/accuracy` | Accuracy stats for AI vs user predictions |
| GET | `/api/health` | Health check with DB match count |

## License

MIT
