"""Dixon-Coles prediction engine with per-league home advantage,
form weighting, draw calibration, and xG blending.
"""
import json
import math
import numpy as np
from pathlib import Path
from scipy.stats import poisson
from scipy.optimize import minimize
from typing import Tuple, Dict, Optional, List
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

# ── Elo fallback parameters ──
ELO_HOME_ADVANTAGE = 65
ELO_BASE_HOME = 1.45
ELO_BASE_AWAY = 1.15
MAX_XG = 3.5

# ── Model state ──
_dc_model: Optional[dict] = None
_calibration: Optional[dict] = None
_xg_data: Optional[dict] = None
_form_data: Optional[dict] = None  # team → recent form score
MIN_MATCHES_FOR_DC = 20
XG_DATA_PATH = Path(__file__).parent.parent / "data" / "xg_data.json"
DC_WEIGHT = 0.7
REGULARIZATION = 1.2  # L2 penalty (reduced from 2.0 for more spread)
FORM_BOOST = 0.12  # xG boost per form point above average


# ─── Dixon-Coles core math ─────────────────────────────────────

def _tau(h, a, lam, mu, rho):
    if h == 0 and a == 0:
        return 1 - lam * mu * rho
    elif h == 0 and a == 1:
        return 1 + lam * rho
    elif h == 1 and a == 0:
        return 1 + mu * rho
    elif h == 1 and a == 1:
        return 1 - rho
    return 1.0


def _dc_prob(h, a, lam, mu, rho):
    return max(0, _tau(h, a, lam, mu, rho)) * poisson.pmf(h, lam) * poisson.pmf(a, mu)


def _time_decay_weight(days_ago, half_life=25.0):
    return math.exp(-0.693 * days_ago / half_life)


# ─── Model fitting ──────────────────────────────────────────────

def fit_model(finished_matches: list) -> Optional[dict]:
    """Fit Dixon-Coles with per-league home advantage and form data."""
    if len(finished_matches) < MIN_MATCHES_FOR_DC:
        return None

    # Build team and league indices
    teams = set()
    leagues = set()
    for m in finished_matches:
        teams.add(m["home_team"])
        teams.add(m["away_team"])
        leagues.add(m.get("league", "unknown"))
    team_list = sorted(teams)
    league_list = sorted(leagues)
    team_idx = {t: i for i, t in enumerate(team_list)}
    league_idx = {l: i for i, l in enumerate(league_list)}
    n_teams = len(team_list)
    n_leagues = len(league_list)

    # Build match data with time-decay
    from datetime import datetime
    now = datetime.utcnow()
    match_data = []
    for m in finished_matches:
        hi = team_idx[m["home_team"]]
        ai = team_idx[m["away_team"]]
        hg = m["actual_home_goals"]
        ag = m["actual_away_goals"]
        li = league_idx.get(m.get("league", "unknown"), 0)

        date_str = str(m.get("match_date", ""))[:10]
        try:
            days_ago = (now - datetime.strptime(date_str, "%Y-%m-%d")).days
        except Exception:
            days_ago = 30

        weight = _time_decay_weight(days_ago)
        match_data.append((hi, ai, hg, ag, weight, li))

    # Parameters: attack[n_teams] + defense[n_teams] + home_adv[n_leagues] + rho
    n_params = 2 * n_teams + n_leagues + 1
    x0 = np.zeros(n_params)
    # Init home advantages to 0.3
    x0[2 * n_teams: 2 * n_teams + n_leagues] = 0.3
    x0[-1] = -0.05  # rho

    def _neg_log_lik(params):
        attack = params[:n_teams]
        defense = params[n_teams:2 * n_teams]
        home_advs = params[2 * n_teams:2 * n_teams + n_leagues]
        rho = params[-1]

        ll = 0.0
        for hi, ai, hg, ag, w, li in match_data:
            lam = max(0.01, math.exp(attack[hi] - defense[ai] + home_advs[li]))
            mu = max(0.01, math.exp(attack[ai] - defense[hi]))
            p = _dc_prob(hg, ag, lam, mu, rho)
            ll += w * math.log(max(p, 1e-10))

        # L2 regularization
        reg = REGULARIZATION * np.sum(params[:2 * n_teams] ** 2)
        return -ll + reg

    try:
        bounds = [(-3, 3)] * (2 * n_teams) + [(0, 1.5)] * n_leagues + [(-0.3, 0.3)]
        result = minimize(_neg_log_lik, x0, method='L-BFGS-B', bounds=bounds,
                          options={'maxiter': 1500, 'ftol': 1e-8})

        attack = result.x[:n_teams]
        defense = result.x[n_teams:2 * n_teams]
        home_advs = result.x[2 * n_teams:2 * n_teams + n_leagues]
        rho = result.x[-1]

        # Normalize attack to mean 0
        attack -= np.mean(attack)

        # Calculate form for each team (last 5 matches)
        form = _calculate_form(finished_matches)

        model = {
            "team_list": team_list,
            "team_idx": team_idx,
            "attack": {t: round(float(attack[i]), 4) for i, t in enumerate(team_list)},
            "defense": {t: round(float(defense[i]), 4) for i, t in enumerate(team_list)},
            "home_adv": {l: round(float(home_advs[i]), 4) for i, l in enumerate(league_list)},
            "rho": round(float(rho), 4),
            "form": form,
            "n_teams": n_teams,
            "n_matches": len(match_data),
        }

        # Log
        sorted_att = sorted(model["attack"].items(), key=lambda x: x[1], reverse=True)
        logger.info(f"Dixon-Coles fitted: {n_teams} teams, {len(match_data)} matches, rho={rho:.3f}")
        for l, ha in model["home_adv"].items():
            logger.info(f"  {l}: home_adv={ha:.3f}")
        logger.info(f"  Top attack: {', '.join(f'{t}({v:+.2f})' for t,v in sorted_att[:3])}")
        logger.info(f"  Weak attack: {', '.join(f'{t}({v:+.2f})' for t,v in sorted_att[-3:])}")

        return model

    except Exception as e:
        logger.error(f"Dixon-Coles fitting failed: {e}")
        return None


def _calculate_form(matches: list) -> dict:
    """Calculate form (points per game over last 5 matches) for each team."""
    from datetime import datetime
    team_matches = defaultdict(list)
    for m in matches:
        date = m.get("match_date", "")[:10]
        hg, ag = m["actual_home_goals"], m["actual_away_goals"]
        team_matches[m["home_team"]].append((date, hg, ag, True))
        team_matches[m["away_team"]].append((date, ag, hg, False))

    form = {}
    for team, results in team_matches.items():
        results.sort(key=lambda x: x[0], reverse=True)
        last5 = results[:5]
        if len(last5) < 3:
            continue
        pts = 0
        for _, gf, ga, _ in last5:
            if gf > ga:
                pts += 3
            elif gf == ga:
                pts += 1
        ppg = pts / len(last5)
        form[team] = round(ppg, 2)

    avg_form = sum(form.values()) / len(form) if form else 1.4
    # Normalize to deviation from average
    form_adj = {t: round(v - avg_form, 2) for t, v in form.items()}
    return form_adj


# ─── Accuracy report ────────────────────────────────────────────

def log_accuracy_report(finished_matches: list):
    """Log a detailed accuracy report comparing predictions to actual results."""
    if not finished_matches:
        return

    from collections import Counter
    oc = lambda h, a: 'W' if h > a else ('L' if h < a else 'D')

    n = len(finished_matches)
    exact = outcome = 0
    mae_sum = 0
    by_league = defaultdict(lambda: {'n': 0, 'exact': 0, 'outcome': 0})
    pred_scores = Counter()
    actual_scores = Counter()

    for m in finished_matches:
        ph, pa = m.get("pred_h", 0), m.get("pred_a", 0)
        ah, aa = m["actual_home_goals"], m["actual_away_goals"]

        if ph == ah and pa == aa:
            exact += 1
        if oc(ph, pa) == oc(ah, aa):
            outcome += 1
        mae_sum += abs(ph - ah) + abs(pa - aa)

        lg = m.get("league", "?")
        by_league[lg]['n'] += 1
        if ph == ah and pa == aa:
            by_league[lg]['exact'] += 1
        if oc(ph, pa) == oc(ah, aa):
            by_league[lg]['outcome'] += 1

        pred_scores[f"{ph}-{pa}"] += 1
        actual_scores[f"{ah}-{aa}"] += 1

    mae = mae_sum / (2 * n)
    pred_draws = sum(1 for m in finished_matches if m.get("pred_h", 0) == m.get("pred_a", 0))
    actual_draws = sum(1 for m in finished_matches if m["actual_home_goals"] == m["actual_away_goals"])

    logger.info(f"=== ACCURACY REPORT ({n} matches) ===")
    logger.info(f"  Exact score: {exact}/{n} ({100 * exact / n:.1f}%)")
    logger.info(f"  Correct outcome: {outcome}/{n} ({100 * outcome / n:.1f}%)")
    logger.info(f"  MAE: {mae:.2f} goals")
    logger.info(f"  Draws: predicted {pred_draws} ({100 * pred_draws / n:.0f}%), actual {actual_draws} ({100 * actual_draws / n:.0f}%)")
    logger.info(f"  Top predicted: {pred_scores.most_common(3)}")
    logger.info(f"  Top actual: {actual_scores.most_common(3)}")
    for lg in sorted(by_league):
        d = by_league[lg]
        logger.info(f"  {lg}: {d['outcome']}/{d['n']} outcome ({100 * d['outcome'] / d['n']:.0f}%), {d['exact']} exact")


# ─── Public API ──────────────────────────────────────────────────

def set_dc_model(model: Optional[dict]) -> None:
    global _dc_model
    _dc_model = model


def set_calibration(cal: Optional[dict]) -> None:
    global _calibration
    _calibration = cal


def load_xg_data() -> int:
    global _xg_data
    if not XG_DATA_PATH.exists():
        logger.info("No xg_data.json — xG blending disabled")
        return 0
    try:
        raw = json.loads(XG_DATA_PATH.read_text())
        _xg_data = {t["team_name"]: t for t in raw.get("teams", [])}
        logger.info(f"Loaded xG data: {len(_xg_data)} teams")
        return len(_xg_data)
    except Exception as e:
        logger.error(f"Failed to load xg_data.json: {e}")
        return 0


def _get_xg(team): return _xg_data.get(team) if _xg_data else None


def _elo_expected_goals(home_elo, away_elo, league=""):
    elo_diff = (home_elo - away_elo + ELO_HOME_ADVANTAGE) / 400
    home_xg = ELO_BASE_HOME * (10 ** (elo_diff * 0.22))
    away_xg = ELO_BASE_AWAY * (10 ** (-elo_diff * 0.22))
    if _calibration and _calibration.get("matches", 0) >= 10:
        lc = _calibration.get("by_league", {}).get(league)
        if lc:
            home_xg += lc["home_bias"] * 0.5
            away_xg += lc["away_bias"] * 0.5
        else:
            home_xg += _calibration["home_bias"] * 0.5
            away_xg += _calibration["away_bias"] * 0.5
    return min(max(0.4, home_xg), MAX_XG), min(max(0.3, away_xg), MAX_XG)


def predict_match(home_xg, away_xg, rho=0.0, draw_inflation=1.0, max_goals=7):
    """Predict match with optional draw inflation."""
    goal_range = range(max_goals)
    prob_matrix = np.zeros((max_goals, max_goals))

    for hg in goal_range:
        for ag in goal_range:
            if rho != 0.0:
                prob_matrix[hg, ag] = _dc_prob(hg, ag, home_xg, away_xg, rho)
            else:
                prob_matrix[hg, ag] = poisson.pmf(hg, home_xg) * poisson.pmf(ag, away_xg)

    # Apply draw inflation if needed
    if draw_inflation != 1.0:
        for g in goal_range:
            prob_matrix[g, g] *= draw_inflation
        prob_matrix /= prob_matrix.sum()  # Renormalize

    # Best scoreline
    best = np.unravel_index(prob_matrix.argmax(), prob_matrix.shape)
    pred_h, pred_a = int(best[0]), int(best[1])

    # Nudge toward rounded xG for moderate values
    rh, ra = round(home_xg), round(away_xg)
    if home_xg >= 1.45 and rh > pred_h:
        pred_h = rh
    if away_xg >= 1.25 and ra > pred_a:
        pred_a = ra

    # For close matches (xG diff < 0.3 and predicted W), consider draw
    xg_diff = abs(home_xg - away_xg)
    if xg_diff < 0.3 and pred_h != pred_a:
        draw_score = round((home_xg + away_xg) / 2)
        draw_prob_sum = sum(prob_matrix[g, g] for g in goal_range)
        win_prob = prob_matrix[pred_h, pred_a]
        if draw_prob_sum > win_prob * 0.85:
            pred_h = pred_a = max(1, draw_score)

    pred_h = min(pred_h, max_goals - 1)
    pred_a = min(pred_a, max_goals - 1)

    # Outcome probabilities
    hwp = dp = awp = 0.0
    for hg in goal_range:
        for ag in goal_range:
            p = prob_matrix[hg, ag]
            if hg > ag: hwp += p
            elif hg == ag: dp += p
            else: awp += p

    if pred_h > pred_a: op = hwp
    elif pred_h < pred_a: op = awp
    else: op = dp

    conf = "low" if pred_h == pred_a else ("high" if op > 0.65 else ("medium" if op > 0.50 else ("low" if op > 0.35 else "very_low")))

    return {
        "predicted_home_goals": pred_h,
        "predicted_away_goals": pred_a,
        "home_win_prob": round(hwp, 3),
        "draw_prob": round(dp, 3),
        "away_win_prob": round(awp, 3),
        "home_xg": round(home_xg, 2),
        "away_xg": round(away_xg, 2),
        "confidence": conf,
        "confidence_pct": round(op * 100),
    }


def generate_prediction(home_team, away_team, home_elo, away_elo, league=""):
    """Generate prediction: DC+xG+form > DC > Elo fallback."""
    rho = 0.0
    draw_inflation = 1.0
    model_used = "elo"

    # Calculate draw inflation from calibration
    if _calibration:
        dr_actual = _calibration.get("draw_rate_actual", 0.25)
        dr_predicted = _calibration.get("draw_rate_predicted", 0.25)
        if dr_predicted > 0.05 and dr_actual > dr_predicted:
            draw_inflation = min(dr_actual / dr_predicted, 1.5)

    if _dc_model and home_team in _dc_model["attack"] and away_team in _dc_model["attack"]:
        att_h = _dc_model["attack"][home_team]
        def_h = _dc_model["defense"][home_team]
        att_a = _dc_model["attack"][away_team]
        def_a = _dc_model["defense"][away_team]
        home_adv = _dc_model["home_adv"].get(league, 0.3)
        rho = _dc_model["rho"]

        dc_home_xg = max(0.4, math.exp(att_h - def_a + home_adv))
        dc_away_xg = max(0.3, math.exp(att_a - def_h))
        dc_home_xg = min(dc_home_xg, MAX_XG)
        dc_away_xg = min(dc_away_xg, MAX_XG)

        # Apply form adjustment
        form = _dc_model.get("form", {})
        form_h = form.get(home_team, 0)
        form_a = form.get(away_team, 0)
        dc_home_xg += form_h * FORM_BOOST
        dc_away_xg += form_a * FORM_BOOST
        dc_home_xg = max(0.4, min(dc_home_xg, MAX_XG))
        dc_away_xg = max(0.3, min(dc_away_xg, MAX_XG))

        # Blend with xG data if available
        hxg = _get_xg(home_team)
        axg = _get_xg(away_team)
        if hxg and axg:
            xg_h = (hxg.get("xg_for_per_match", dc_home_xg) + axg.get("xg_against_per_match", dc_home_xg)) / 2
            xg_a = (axg.get("xg_for_per_match", dc_away_xg) + hxg.get("xg_against_per_match", dc_away_xg)) / 2
            home_xg = DC_WEIGHT * dc_home_xg + (1 - DC_WEIGHT) * xg_h
            away_xg = DC_WEIGHT * dc_away_xg + (1 - DC_WEIGHT) * xg_a
            home_xg = min(max(0.4, home_xg), MAX_XG)
            away_xg = min(max(0.3, away_xg), MAX_XG)
            model_used = "dc+xg"
        else:
            home_xg = dc_home_xg
            away_xg = dc_away_xg
            model_used = "dc"
    else:
        home_xg, away_xg = _elo_expected_goals(home_elo, away_elo, league)

    prediction = predict_match(home_xg, away_xg, rho, draw_inflation)

    logger.info(
        f"[{model_used.upper()}] {home_team} vs {away_team} | "
        f"xG: {home_xg:.2f}-{away_xg:.2f} | "
        f"Pred: {prediction['predicted_home_goals']}-{prediction['predicted_away_goals']} | "
        f"W/D/L: {prediction['home_win_prob']}/{prediction['draw_prob']}/{prediction['away_win_prob']}"
    )

    return prediction
