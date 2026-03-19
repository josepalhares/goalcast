"""Poisson-based prediction engine for match score prediction."""
import numpy as np
from scipy.stats import poisson
from typing import Tuple, Dict, Optional
import logging

logger = logging.getLogger(__name__)

HOME_ADVANTAGE = 65  # Elo points
BASE_HOME_GOALS = 1.45
BASE_AWAY_GOALS = 1.15
MAX_XG = 3.5

# Calibration adjustments (loaded from DB after refresh)
_calibration: Optional[dict] = None


def set_calibration(cal: Optional[dict]) -> None:
    """Set calibration adjustments from DB."""
    global _calibration
    _calibration = cal
    if cal:
        logger.info(
            f"Calibration active ({cal['matches']} matches): "
            f"home_bias={cal['home_bias']:+.2f}, away_bias={cal['away_bias']:+.2f}"
        )


def elo_to_expected_goals(home_elo: float, away_elo: float) -> Tuple[float, float]:
    """Convert Elo ratings to expected goals with calibration adjustments."""
    elo_diff = (home_elo - away_elo + HOME_ADVANTAGE) / 400

    home_xg = BASE_HOME_GOALS * (10 ** (elo_diff * 0.22))
    away_xg = BASE_AWAY_GOALS * (10 ** (-elo_diff * 0.22))

    # Apply calibration bias if available (nudge xG toward actual averages)
    if _calibration and _calibration.get("matches", 0) >= 10:
        home_xg += _calibration["home_bias"] * 0.5  # Apply half the bias (conservative)
        away_xg += _calibration["away_bias"] * 0.5

    home_xg = min(max(0.4, home_xg), MAX_XG)
    away_xg = min(max(0.3, away_xg), MAX_XG)

    return round(home_xg, 2), round(away_xg, 2)


def predict_score_poisson(home_xg: float, away_xg: float, max_goals: int = 7) -> Dict:
    """Predict match score using Poisson distribution."""
    goal_range = range(max_goals)
    prob_matrix = np.zeros((max_goals, max_goals))

    for hg in goal_range:
        for ag in goal_range:
            prob_matrix[hg, ag] = poisson.pmf(hg, home_xg) * poisson.pmf(ag, away_xg)

    # Start with the pure Poisson mode
    best_idx = np.unravel_index(prob_matrix.argmax(), prob_matrix.shape)
    pred_h, pred_a = int(best_idx[0]), int(best_idx[1])

    # Use rounded xG when the expected value is clearly above the integer threshold
    rounded_h = round(home_xg)
    rounded_a = round(away_xg)
    if home_xg >= 1.45 and rounded_h > pred_h:
        pred_h = rounded_h
    if away_xg >= 1.25 and rounded_a > pred_a:
        pred_a = rounded_a

    pred_h = min(pred_h, max_goals - 1)
    pred_a = min(pred_a, max_goals - 1)

    # Outcome probabilities
    home_win_prob = draw_prob = away_win_prob = 0.0
    for hg in goal_range:
        for ag in goal_range:
            p = prob_matrix[hg, ag]
            if hg > ag:
                home_win_prob += p
            elif hg == ag:
                draw_prob += p
            else:
                away_win_prob += p

    # Determine confidence
    if pred_h > pred_a:
        outcome_prob = home_win_prob
    elif pred_h < pred_a:
        outcome_prob = away_win_prob
    else:
        outcome_prob = draw_prob

    if pred_h == pred_a:
        confidence = "low"
    elif outcome_prob > 0.65:
        confidence = "high"
    elif outcome_prob > 0.50:
        confidence = "medium"
    elif outcome_prob > 0.35:
        confidence = "low"
    else:
        confidence = "very_low"

    return {
        "predicted_home_goals": pred_h,
        "predicted_away_goals": pred_a,
        "home_win_prob": round(home_win_prob, 3),
        "draw_prob": round(draw_prob, 3),
        "away_win_prob": round(away_win_prob, 3),
        "home_xg": home_xg,
        "away_xg": away_xg,
        "confidence": confidence,
        "confidence_pct": round(outcome_prob * 100),
    }


def generate_prediction(
    home_team: str, away_team: str, home_elo: float, away_elo: float
) -> Dict:
    """Generate full prediction for a match."""
    home_xg, away_xg = elo_to_expected_goals(home_elo, away_elo)
    prediction = predict_score_poisson(home_xg, away_xg)

    logger.info(
        f"{home_team} ({home_elo:.0f}) vs {away_team} ({away_elo:.0f}) | "
        f"xG: {home_xg:.2f}-{away_xg:.2f} | "
        f"Pred: {prediction['predicted_home_goals']}-{prediction['predicted_away_goals']} | "
        f"W/D/L: {prediction['home_win_prob']}/{prediction['draw_prob']}/{prediction['away_win_prob']}"
    )

    return prediction
