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
# Edit .env and add your API_FOOTBALL_KEY from https://dashboard.api-football.com

# Run
python main.py
# Open http://localhost:8000
```

## Deploy on Railway

1. Push to GitHub
2. Connect repo on [Railway](https://railway.app)
3. Add environment variable: `API_FOOTBALL_KEY=your_key`
4. Deploy — Railway reads the `Procfile` automatically

## Tech Stack

- **Backend:** Python, FastAPI, SQLite, httpx
- **Frontend:** HTML, Tailwind CSS (CDN), Alpine.js, Chart.js
- **Data:** API-Football (fixtures), ClubElo (Elo ratings)
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
