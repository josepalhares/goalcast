"""Pydantic models for GoalCast."""
from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class EloRating(BaseModel):
    """Elo rating for a team."""
    team_name: str
    elo_rating: float
    fetched_date: str


class Match(BaseModel):
    """Match model."""
    id: Optional[int] = None
    api_match_id: Optional[str] = None
    league: str
    home_team: str
    away_team: str
    match_date: datetime
    home_elo: Optional[float] = None
    away_elo: Optional[float] = None
    status: str = "upcoming"
    actual_home_goals: Optional[int] = None
    actual_away_goals: Optional[int] = None
    created_at: Optional[datetime] = None


class Prediction(BaseModel):
    """Prediction model."""
    id: Optional[int] = None
    match_id: int
    source: str  # 'ai' or 'user'
    predicted_home_goals: int
    predicted_away_goals: int
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    created_at: Optional[datetime] = None


class MatchWithPrediction(BaseModel):
    """Match with AI prediction."""
    match: Match
    prediction: Optional[Prediction] = None
