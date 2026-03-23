"""Dixon-Coles prediction engine with xG blending and Elo fallback.

The Dixon-Coles model extends Poisson regression by:
1. Estimating per-team attack/defense strength parameters from historical goals
2. Applying a low-scoring correction (rho) that boosts 0-0, 1-0, 0-1, 1-1 draws
3. Using home advantage as a fitted parameter
4. Applying time-decay so recent matches matter more
5. Optionally blending with Understat xG data (70% DC, 30% xG)

When fewer than 20 finished matches exist, falls back to Elo-based Poisson.
"""
import json
import math
import numpy as np
from pathlib import Path
from scipy.stats import poisson
from scipy.optimize import minimize
from typing import Tuple, Dict, Optional
import logging

logger = logging.getLogger(__name__)

# ── Elo fallback parameters ──
ELO_HOME_ADVANTAGE = 65
ELO_BASE_HOME = 1.45
ELO_BASE_AWAY = 1.15
MAX_XG = 3.5

# ── Dixon-Coles fitted model (populated by fit_model) ──
_dc_model: Optional[dict] = None
_calibration: Optional[dict] = None
_xg_data: Optional[dict] = None  # team_name → {xg_for_per_match, xg_against_per_match, ...}
MIN_MATCHES_FOR_DC = 20
XG_DATA_PATH = Path(__file__).parent.parent / "data" / "xg_data.json"
DC_WEIGHT = 0.7  # 70% Dixon-Coles, 30% xG when both available


# ─── Dixon-Coles core math ────────────────────────────────────

def _tau(h: int, a: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles low-scoring correction factor."""
    if h == 0 and a == 0:
        return 1 - lam * mu * rho
    elif h == 0 and a == 1:
        return 1 + lam * rho
    elif h == 1 and a == 0:
        return 1 + mu * rho
    elif h == 1 and a == 1:
        return 1 - rho
    return 1.0


def _dc_prob(h: int, a: int, lam: float, mu: float, rho: float) -> float:
    """Probability of score h-a under Dixon-Coles model."""
    return _tau(h, a, lam, mu, rho) * poisson.pmf(h, lam) * poisson.pmf(a, mu)


def _dc_log_likelihood(params, matches, team_idx, n_teams):
    """Negative log-likelihood for Dixon-Coles parameter estimation."""
    attack = params[:n_teams]
    defense = params[n_teams:2*n_teams]
    home_adv = params[2*n_teams]
    rho = params[2*n_teams + 1]

    log_lik = 0.0
    for home_i, away_i, hg, ag, weight in matches:
        lam = max(0.01, math.exp(attack[home_i] - defense[away_i] + home_adv))
        mu = max(0.01, math.exp(attack[away_i] - defense[home_i]))

        p = _dc_prob(hg, ag, lam, mu, rho)
        if p > 0:
            log_lik += weight * math.log(p)
        else:
            log_lik += weight * (-20)  # penalty for impossible scores

    return -log_lik  # Minimize negative


def _time_decay_weight(days_ago: float, half_life: float = 30.0) -> float:
    """Exponential decay: recent matches weighted more heavily."""
    return math.exp(-0.693 * days_ago / half_life)


# ─── Model fitting ─────────────────────────────────────────────

def fit_model(finished_matches: list) -> Optional[dict]:
    """Fit Dixon-Coles model from finished matches.

    Args:
        finished_matches: list of dicts with keys:
            home_team, away_team, actual_home_goals, actual_away_goals, match_date, league

    Returns:
        Fitted model dict or None if insufficient data.
    """
    if len(finished_matches) < MIN_MATCHES_FOR_DC:
        logger.info(f"Only {len(finished_matches)} matches — need {MIN_MATCHES_FOR_DC} for Dixon-Coles")
        return None

    # Build team index
    teams = set()
    for m in finished_matches:
        teams.add(m["home_team"])
        teams.add(m["away_team"])
    team_list = sorted(teams)
    team_idx = {t: i for i, t in enumerate(team_list)}
    n_teams = len(team_list)

    # Build match data with time-decay weights
    from datetime import datetime
    now = datetime.utcnow()
    match_data = []
    for m in finished_matches:
        hi = team_idx[m["home_team"]]
        ai = team_idx[m["away_team"]]
        hg = m["actual_home_goals"]
        ag = m["actual_away_goals"]

        # Calculate days ago for time decay
        date_str = str(m.get("match_date", ""))[:10]
        try:
            match_date = datetime.strptime(date_str, "%Y-%m-%d")
            days_ago = (now - match_date).days
        except Exception:
            days_ago = 30  # default

        weight = _time_decay_weight(days_ago)
        match_data.append((hi, ai, hg, ag, weight))

    # Initial parameters: attack=0, defense=0, home_adv=0.25, rho=-0.1
    x0 = np.zeros(2 * n_teams + 2)
    x0[2 * n_teams] = 0.25  # home advantage
    x0[2 * n_teams + 1] = -0.1  # rho (low-scoring correction)

    # Add L2 regularization to prevent extreme parameters
    def _regularized_nll(params, matches, team_idx, n_teams):
        nll = _dc_log_likelihood(params, matches, team_idx, n_teams)
        # Penalize large attack/defense values (shrink toward 0)
        reg = 0.5 * np.sum(params[:2*n_teams] ** 2)
        return nll + reg

    # Optimize with regularization
    try:
        result = minimize(
            _regularized_nll,
            x0,
            args=(match_data, team_idx, n_teams),
            method='L-BFGS-B',
            bounds=[(-3.0, 3.0)] * (2 * n_teams) + [(0.0, 1.0), (-0.5, 0.5)],
            options={'maxiter': 1000, 'ftol': 1e-8},
        )

        if not result.success:
            logger.warning(f"Dixon-Coles optimization warning: {result.message}")

        attack = result.x[:n_teams]
        defense = result.x[n_teams:2*n_teams]
        home_adv = result.x[2*n_teams]
        rho = result.x[2*n_teams + 1]

        # Normalize: set mean attack to 0
        mean_att = np.mean(attack)
        attack -= mean_att

        model = {
            "team_list": team_list,
            "team_idx": team_idx,
            "attack": {t: round(float(attack[i]), 4) for i, t in enumerate(team_list)},
            "defense": {t: round(float(defense[i]), 4) for i, t in enumerate(team_list)},
            "home_adv": round(float(home_adv), 4),
            "rho": round(float(rho), 4),
            "n_teams": n_teams,
            "n_matches": len(match_data),
        }

        # Log top/bottom teams by attack strength
        sorted_att = sorted(model["attack"].items(), key=lambda x: x[1], reverse=True)
        top3 = ", ".join(f"{t}({v:+.2f})" for t, v in sorted_att[:3])
        bot3 = ", ".join(f"{t}({v:+.2f})" for t, v in sorted_att[-3:])
        logger.info(
            f"Dixon-Coles fitted: {n_teams} teams, {len(match_data)} matches, "
            f"home_adv={home_adv:.3f}, rho={rho:.3f}"
        )
        logger.info(f"  Top attack: {top3}")
        logger.info(f"  Weak attack: {bot3}")

        return model

    except Exception as e:
        logger.error(f"Dixon-Coles fitting failed: {e}")
        return None


# ─── Public API ─────────────────────────────────────────────────

def set_dc_model(model: Optional[dict]) -> None:
    """Set the fitted Dixon-Coles model."""
    global _dc_model
    _dc_model = model


def set_calibration(cal: Optional[dict]) -> None:
    """Set calibration adjustments from DB (used as Elo fallback)."""
    global _calibration
    _calibration = cal


def load_xg_data() -> int:
    """Load xG data from data/xg_data.json if it exists. Returns team count."""
    global _xg_data
    if not XG_DATA_PATH.exists():
        logger.info("No xg_data.json found — xG blending disabled")
        return 0

    try:
        raw = json.loads(XG_DATA_PATH.read_text())
        teams = raw.get("teams", [])
        _xg_data = {}
        for t in teams:
            _xg_data[t["team_name"]] = t
        scraped = raw.get("scraped_at", "unknown")
        logger.info(f"Loaded xG data: {len(_xg_data)} teams (scraped {scraped})")
        return len(_xg_data)
    except Exception as e:
        logger.error(f"Failed to load xg_data.json: {e}")
        _xg_data = None
        return 0


def _get_xg_for_team(team_name: str) -> Optional[dict]:
    """Look up xG data for a team."""
    if not _xg_data:
        return None
    return _xg_data.get(team_name)


def _elo_expected_goals(home_elo: float, away_elo: float, league: str = "") -> Tuple[float, float]:
    """Elo-based xG fallback when Dixon-Coles can't predict a team."""
    elo_diff = (home_elo - away_elo + ELO_HOME_ADVANTAGE) / 400

    home_xg = ELO_BASE_HOME * (10 ** (elo_diff * 0.22))
    away_xg = ELO_BASE_AWAY * (10 ** (-elo_diff * 0.22))

    if _calibration and _calibration.get("matches", 0) >= 10:
        league_cal = _calibration.get("by_league", {}).get(league)
        if league_cal:
            home_xg += league_cal["home_bias"] * 0.5
            away_xg += league_cal["away_bias"] * 0.5
        else:
            home_xg += _calibration["home_bias"] * 0.5
            away_xg += _calibration["away_bias"] * 0.5

    return min(max(0.4, home_xg), MAX_XG), min(max(0.3, away_xg), MAX_XG)


def predict_match(home_xg: float, away_xg: float, rho: float = 0.0, max_goals: int = 7) -> Dict:
    """Predict match outcome from expected goals using Dixon-Coles probability matrix."""
    goal_range = range(max_goals)
    prob_matrix = np.zeros((max_goals, max_goals))

    for hg in goal_range:
        for ag in goal_range:
            if rho != 0.0:
                prob_matrix[hg, ag] = _dc_prob(hg, ag, home_xg, away_xg, rho)
            else:
                prob_matrix[hg, ag] = poisson.pmf(hg, home_xg) * poisson.pmf(ag, away_xg)

    # Most likely scoreline
    best_idx = np.unravel_index(prob_matrix.argmax(), prob_matrix.shape)
    pred_h, pred_a = int(best_idx[0]), int(best_idx[1])

    # Nudge toward rounded xG when Poisson mode is too conservative
    rounded_h, rounded_a = round(home_xg), round(away_xg)
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

    # Confidence
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
        "home_xg": round(home_xg, 2),
        "away_xg": round(away_xg, 2),
        "confidence": confidence,
        "confidence_pct": round(outcome_prob * 100),
    }


def generate_prediction(
    home_team: str, away_team: str, home_elo: float, away_elo: float, league: str = ""
) -> Dict:
    """Generate prediction: Dixon-Coles + xG blend > Dixon-Coles > Elo fallback."""
    rho = 0.0
    model_used = "elo"

    if _dc_model and home_team in _dc_model["attack"] and away_team in _dc_model["attack"]:
        # Use Dixon-Coles fitted parameters
        att_h = _dc_model["attack"][home_team]
        def_h = _dc_model["defense"][home_team]
        att_a = _dc_model["attack"][away_team]
        def_a = _dc_model["defense"][away_team]
        home_adv = _dc_model["home_adv"]
        rho = _dc_model["rho"]

        dc_home_xg = max(0.4, math.exp(att_h - def_a + home_adv))
        dc_away_xg = max(0.3, math.exp(att_a - def_h))
        dc_home_xg = min(dc_home_xg, MAX_XG)
        dc_away_xg = min(dc_away_xg, MAX_XG)

        # Try to blend with xG data
        home_xg_data = _get_xg_for_team(home_team)
        away_xg_data = _get_xg_for_team(away_team)

        if home_xg_data and away_xg_data:
            # Blend: 70% Dixon-Coles, 30% xG-based expected goals
            # xG-based: home team's xG per home match vs away team's xGA per away match
            xg_home = (home_xg_data.get("xg_for_per_match", dc_home_xg) +
                       away_xg_data.get("xg_against_per_match", dc_home_xg)) / 2
            xg_away = (away_xg_data.get("xg_for_per_match", dc_away_xg) +
                       home_xg_data.get("xg_against_per_match", dc_away_xg)) / 2

            home_xg = DC_WEIGHT * dc_home_xg + (1 - DC_WEIGHT) * xg_home
            away_xg = DC_WEIGHT * dc_away_xg + (1 - DC_WEIGHT) * xg_away
            home_xg = min(max(0.4, home_xg), MAX_XG)
            away_xg = min(max(0.3, away_xg), MAX_XG)
            model_used = "dc+xg"
        else:
            home_xg = dc_home_xg
            away_xg = dc_away_xg
            model_used = "dc"
    else:
        # Elo fallback
        home_xg, away_xg = _elo_expected_goals(home_elo, away_elo, league)

    prediction = predict_match(home_xg, away_xg, rho)

    logger.info(
        f"[{model_used.upper()}] {home_team} vs {away_team} | "
        f"xG: {home_xg:.2f}-{away_xg:.2f} | "
        f"Pred: {prediction['predicted_home_goals']}-{prediction['predicted_away_goals']} | "
        f"W/D/L: {prediction['home_win_prob']}/{prediction['draw_prob']}/{prediction['away_win_prob']}"
    )

    return prediction
