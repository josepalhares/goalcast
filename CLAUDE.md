# GoalCast — AI-Powered Football Score Predictor

## Project Overview

GoalCast is a football (soccer) match score prediction app that combines Elo ratings, team news/form signals, and expected goals (xG) modeling to predict match outcomes across all major European leagues. It includes a personal prediction tracker where the user can compare AI predictions vs their own bets against actual results.

## The Problem

Football fans and casual bettors rely on gut feeling or scattered stats to predict match outcomes. There's no single tool that combines team strength ratings, current form signals, and statistical models into a clear, actionable score prediction — while also letting you track your own prediction accuracy over time.

## What We're Building

A locally-running web app with three core views:

### 1. Upcoming Matches & Predictions
- Shows upcoming fixtures across all major European leagues
- For each match: displays both teams' Elo ratings, recent form, and any news impacting the teams
- Generates a predicted score using a Poisson-based xG model informed by Elo differentials
- User can accept the AI prediction OR input their own custom prediction
- User can lock in their prediction before the match starts

### 2. Live/Recent Results
- Shows matches that have been played with actual final scores
- Side-by-side comparison: AI prediction vs User prediction vs Actual score

### 3. History & Accuracy Dashboard
- Full history of all predictions (AI and user)
- Accuracy metrics for both:
  - Mean Absolute Error (MAE) on goals
  - Exact score hit rate
  - Correct outcome rate (Win/Draw/Loss)
  - Rolling accuracy over last 10, 30, 90 days
- Visual charts showing accuracy trends over time

## Leagues Covered

- Premier League (England)
- La Liga (Spain)
- Serie A (Italy)
- Bundesliga (Germany)
- Ligue 1 (France)
- Liga Portugal (Portugal)
- Champions League (UEFA)

## Data Sources & APIs

### Primary: API-Football (api-football.com)
- **Free plan**: 100 requests/day, all endpoints accessible, current season only
- Endpoints needed:
  - `GET /fixtures` — upcoming and past matches by league and date
  - `GET /fixtures/statistics` — match-level stats (shots, possession, etc.)
  - `GET /standings` — league tables
  - `GET /teams/statistics` — season-level team stats
  - `GET /predictions` — API-Football's own prediction data (includes form, comparison, etc.)
  - `GET /injuries` — current team injuries
- Register at: https://dashboard.api-football.com/register
- API Key goes in: `.env` file as `API_FOOTBALL_KEY`

### Secondary: ClubElo (clubelo.com)
- **Free, no API key needed**
- Endpoints:
  - `http://api.clubelo.com/YYYY-MM-DD` — all club Elo ratings for a given date
  - `http://api.clubelo.com/CLUBNAME` — historical ratings for one club
- CSV format response, easy to parse
- Use for: team strength comparison, Elo differential calculations

### Tertiary: News/Form (via Claude AI)
- Use Claude API (or OpenAI) to summarize recent team news that could impact performance
- Input: team name + "recent news injuries form"
- Output: structured summary of factors that might affect the prediction
- This is a nice-to-have / Phase 2 feature

## Tech Stack

### Backend: Python (FastAPI)
- FastAPI for REST API and serving the frontend
- SQLite for local data persistence (predictions, match history, accuracy)
- httpx for async API calls to data sources
- Pydantic for data models

### Frontend: HTML + Tailwind CSS + Alpine.js (or vanilla JS)
- Single-page app served by FastAPI
- Tailwind via CDN for styling
- Alpine.js for lightweight reactivity (no build step needed)
- Chart.js for accuracy visualizations

### Why this stack:
- Zero build step — just run `python main.py`
- No Node.js/npm/webpack complexity
- SQLite = no database setup
- Ships fast for a hackathon

## Prediction Model (Simplified)

### Step 1: Get Elo Ratings
- Fetch from ClubElo API for both teams
- Calculate Elo differential (home_elo - away_elo + home_advantage_bonus)

### Step 2: Convert Elo to Expected Goals
- Use Elo differential to estimate win probability
- Map win probability to expected goal rates using historical averages
- Home team xG = base_rate * (1 + elo_advantage_factor)
- Away team xG = base_rate * (1 - elo_advantage_factor)
- Base rates calibrated from historical data (~1.4 goals home, ~1.1 goals away)

### Step 3: Poisson Distribution
- Use each team's xG as the lambda parameter for a Poisson distribution
- Generate probability matrix for all scoreline combinations (0-0 through 5-5)
- Most likely scoreline = the prediction
- Also output: win/draw/loss probabilities

### Step 4: Adjustments (if data available)
- Factor in recent form (last 5 matches) from API-Football
- Adjust for key injuries/suspensions
- Weight home/away recent performance

## Database Schema (SQLite)

```sql
CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_match_id TEXT UNIQUE,
    league TEXT,
    home_team TEXT,
    away_team TEXT,
    match_date DATETIME,
    home_elo REAL,
    away_elo REAL,
    status TEXT DEFAULT 'upcoming',  -- upcoming, live, finished
    actual_home_goals INTEGER,
    actual_away_goals INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER REFERENCES matches(id),
    source TEXT,  -- 'ai' or 'user'
    predicted_home_goals INTEGER,
    predicted_away_goals INTEGER,
    home_win_prob REAL,
    draw_prob REAL,
    away_win_prob REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(match_id, source)
);

CREATE TABLE team_elo_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name TEXT,
    elo_rating REAL,
    fetched_date DATE,
    UNIQUE(team_name, fetched_date)
);
```

## File Structure

```
goalcast/
├── CLAUDE.md                # This file (project context for Claude Code)
├── .env                     # API keys (not committed)
├── .env.example             # Template for API keys
├── main.py                  # FastAPI app entry point
├── requirements.txt         # Python dependencies
├── db.py                    # SQLite setup and helpers
├── models.py                # Pydantic models
├── api/
│   ├── football_api.py      # API-Football client
│   ├── club_elo.py          # ClubElo client
│   └── routes.py            # FastAPI route handlers
├── prediction/
│   ├── engine.py            # Poisson prediction model
│   └── accuracy.py          # Accuracy calculation helpers
├── static/
│   ├── index.html           # Main SPA page
│   ├── app.js               # Frontend logic (Alpine.js)
│   └── style.css            # Custom styles (if any beyond Tailwind)
└── data/
    └── goalcast.db          # SQLite database (auto-created)
```

## API Routes

```
GET  /api/matches/upcoming       # Upcoming matches with predictions
GET  /api/matches/recent         # Recent results with comparisons
GET  /api/matches/history        # Full prediction history
POST /api/predictions            # Save user prediction for a match
GET  /api/accuracy               # Accuracy stats (AI vs User)
POST /api/refresh                # Trigger data refresh from APIs
```

## Build Order (for Claude Code)

### Phase 1: Foundation (get something working)
1. Set up FastAPI project with requirements.txt
2. Create SQLite database and models
3. Build ClubElo API client (no key needed, easiest to start)
4. Build API-Football client (needs API key)
5. Create basic prediction engine (Elo → Poisson)
6. Build `/api/matches/upcoming` endpoint

### Phase 2: Frontend (make it visual)
7. Create index.html with Tailwind + Alpine.js
8. Build upcoming matches view with predictions displayed
9. Add user prediction input (score fields + save button)
10. Build recent results view

### Phase 3: History & Accuracy (the differentiator)
11. Build history view with all past predictions
12. Implement accuracy calculations (MAE, hit rates)
13. Add Chart.js accuracy visualization
14. Add accuracy comparison: AI vs User

### Phase 4: Polish (if time allows)
15. Add team news/form summaries
16. Add league filtering
17. Improve UI/UX
18. Add auto-refresh for live matches

## Key Dependencies (requirements.txt)

```
fastapi>=0.104.0
uvicorn>=0.24.0
httpx>=0.25.0
pydantic>=2.5.0
python-dotenv>=1.0.0
numpy>=1.25.0
scipy>=1.11.0
```

## Running the App

```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env and add your API_FOOTBALL_KEY

# Run
python main.py
# Open http://localhost:8000
```

## Convention Notes for Claude Code

- Use Python 3.10+ type hints everywhere
- Use async/await for all API calls
- Keep functions small and focused
- Use docstrings on public functions
- Error handling: log errors, never crash the server
- Frontend: keep it simple, no build tools, CDN imports only
- Database: use context managers for connections
- All dates in UTC
