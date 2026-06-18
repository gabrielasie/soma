"""
Soma Pattern Analysis Engine
Detects statistically significant patterns in Oura biometric data.
Only surfaces observations that pass a confidence threshold.

Candidate patterns:
  1. HRV by day of week (weekend vs weekday training effects)
  2. Readiness vs activity level (high-step days → next-day readiness)
  3. Sleep efficiency variance over the period
  4. Deep sleep trends (improving, declining, or stable)
  5. Bedtime variance (consistency of sleep schedule)
"""

import statistics
from datetime import datetime


# Minimum effect size (points) and max p-value to surface a pattern
MIN_EFFECT = 2.0
MAX_PVALUE = 0.15  # relaxed for small N; we show confidence language accordingly
MIN_DAYS = 5       # minimum data points to attempt analysis


def _mean(vals):
    return sum(vals) / len(vals) if vals else None


def _stdev(vals):
    return statistics.stdev(vals) if len(vals) >= 2 else 0


def _cohens_d(group_a, group_b):
    """Effect size between two groups."""
    if len(group_a) < 2 or len(group_b) < 2:
        return 0
    mean_a, mean_b = _mean(group_a), _mean(group_b)
    pooled_std = ((_stdev(group_a) ** 2 + _stdev(group_b) ** 2) / 2) ** 0.5
    if pooled_std == 0:
        return 0
    return (mean_a - mean_b) / pooled_std


def _welch_t_approx(group_a, group_b):
    """
    Approximate p-value using Welch's t-test.
    Returns (t_stat, approx_p, effect_size).
    For small samples this is approximate but honest.
    """
    n_a, n_b = len(group_a), len(group_b)
    if n_a < 2 or n_b < 2:
        return 0, 1.0, 0
    mean_a, mean_b = _mean(group_a), _mean(group_b)
    var_a = _stdev(group_a) ** 2
    var_b = _stdev(group_b) ** 2
    se = ((var_a / n_a) + (var_b / n_b)) ** 0.5
    if se == 0:
        return 0, 1.0, 0
    t = (mean_a - mean_b) / se
    # Approximate p-value from t using conservative df
    df = min(n_a, n_b) - 1
    if df < 1:
        df = 1
    # Simple approximation: p ≈ 2 * (1 - Φ(|t| * sqrt(df/(df+1))))
    # Good enough for our threshold checking
    import math
    z_approx = abs(t) * (df / (df + 1)) ** 0.5
    # Using error function approximation for normal CDF
    p = 2 * (1 - _norm_cdf(z_approx))
    effect = _cohens_d(group_a, group_b)
    return t, p, effect


def _norm_cdf(x):
    """Approximation of the normal CDF."""
    import math
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _trend_slope(values):
    """Simple linear regression slope over ordered values."""
    n = len(values)
    if n < 3:
        return 0, 0
    x_mean = (n - 1) / 2
    y_mean = _mean(values)
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0, 0
    slope = num / den
    # R-squared
    ss_res = sum((v - (y_mean + slope * (i - x_mean))) ** 2 for i, v in enumerate(values))
    ss_tot = sum((v - y_mean) ** 2 for v in values)
    r_sq = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    return slope, r_sq


def _confidence_label(p_value, n):
    """Human-readable confidence level."""
    if n < 7:
        return "preliminary"
    if p_value < 0.05:
        return "strong"
    if p_value < 0.10:
        return "moderate"
    return "suggestive"


def analyze_patterns(data, keys):
    """
    Run all candidate analyses and return observations that pass thresholds.
    Each observation: {id, headline, context, chart_data, confidence}
    """
    observations = []

    obs = _hrv_by_day_of_week(data, keys)
    if obs:
        observations.append(obs)

    obs = _readiness_vs_activity(data, keys)
    if obs:
        observations.append(obs)

    obs = _deep_sleep_trend(data, keys)
    if obs:
        observations.append(obs)

    obs = _sleep_efficiency_consistency(data, keys)
    if obs:
        observations.append(obs)

    obs = _sleep_score_by_day_of_week(data, keys)
    if obs:
        observations.append(obs)

    return observations


def _hrv_by_day_of_week(data, keys):
    """Compare weekday vs weekend HRV balance."""
    weekday_vals = []
    weekend_vals = []
    day_buckets = {i: [] for i in range(7)}  # 0=Mon, 6=Sun

    for k in keys:
        d = data[k]
        hrv = d.get("hrv_balance")
        if hrv is None:
            continue
        dt = datetime.strptime(k, "%Y-%m-%d")
        dow = dt.weekday()
        day_buckets[dow].append(hrv)
        if dow < 5:
            weekday_vals.append(hrv)
        else:
            weekend_vals.append(hrv)

    if len(weekday_vals) < 3 or len(weekend_vals) < 2:
        return None

    t, p, effect = _welch_t_approx(weekend_vals, weekday_vals)
    diff = _mean(weekend_vals) - _mean(weekday_vals)

    if abs(diff) < MIN_EFFECT or p > MAX_PVALUE:
        return None

    direction = "higher" if diff > 0 else "lower"
    abs_diff = abs(round(diff, 1))
    wkend_mean = round(_mean(weekend_vals), 1)
    wkday_mean = round(_mean(weekday_vals), 1)

    # Chart data: average HRV per day of week
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    chart_points = []
    for i in range(7):
        vals = day_buckets[i]
        chart_points.append({
            "label": day_names[i],
            "value": round(_mean(vals), 1) if vals else None,
            "n": len(vals),
            "is_weekend": i >= 5,
        })

    conf = _confidence_label(p, len(keys))
    headline = f"Your HRV runs {abs_diff} points {direction} on weekends than weekdays."
    context = (
        f"Weekend average is {wkend_mean}, weekday average is {wkday_mean}. "
        f"This may reflect different training loads or sleep patterns across the week."
    )

    return {
        "id": "hrv_by_dow",
        "headline": headline,
        "context": context,
        "chart_type": "bar",
        "chart_data": chart_points,
        "baseline": round(_mean(weekday_vals + weekend_vals), 1),
        "confidence": conf,
        "stat_basis": f"Welch's t-test, t={round(t, 2)}, p={round(p, 3)}, d={round(effect, 2)}, n={len(weekday_vals)+len(weekend_vals)}",
    }


def _readiness_vs_activity(data, keys):
    """Does a high-activity day predict next-day readiness change?"""
    pairs = []  # (activity_level, next_day_readiness)
    for i in range(len(keys) - 1):
        d = data[keys[i]]
        d_next = data[keys[i + 1]]
        steps = d.get("steps")
        readiness = d_next.get("readiness_score")
        if steps is None or readiness is None:
            continue
        pairs.append((steps, readiness))

    if len(pairs) < MIN_DAYS:
        return None

    # Split into high/low activity days by median
    pairs.sort(key=lambda x: x[0])
    mid = len(pairs) // 2
    low_activity_readiness = [p[1] for p in pairs[:mid]]
    high_activity_readiness = [p[1] for p in pairs[mid:]]

    t, p, effect = _welch_t_approx(high_activity_readiness, low_activity_readiness)
    diff = _mean(high_activity_readiness) - _mean(low_activity_readiness)

    if abs(diff) < MIN_EFFECT or p > MAX_PVALUE:
        return None

    # Chart: scatter-like — show pairs as points
    chart_points = [{"steps": s, "readiness": r} for s, r in pairs]
    high_mean = round(_mean(high_activity_readiness), 1)
    low_mean = round(_mean(low_activity_readiness), 1)
    median_steps = pairs[mid][0]

    direction = "higher" if diff > 0 else "lower"
    abs_diff = abs(round(diff, 1))

    conf = _confidence_label(p, len(pairs))
    headline = f"Next-day readiness is {abs_diff} points {direction} after high-activity days."
    context = (
        f"After days above {median_steps:,} steps, readiness averages {high_mean}. "
        f"After quieter days, it averages {low_mean}. "
        f"Based on {len(pairs)} day-pairs."
    )

    return {
        "id": "readiness_vs_activity",
        "headline": headline,
        "context": context,
        "chart_type": "scatter",
        "chart_data": chart_points,
        "baseline": round(_mean([p[1] for p in pairs]), 1),
        "confidence": conf,
        "stat_basis": f"Welch's t-test (median split), t={round(t, 2)}, p={round(p, 3)}, d={round(effect, 2)}, n={len(pairs)}",
    }


def _deep_sleep_trend(data, keys):
    """Is deep sleep trending up or down over the period?"""
    values = []
    dates = []
    for k in keys:
        v = data[k].get("deep_sleep_score")
        if v is not None:
            values.append(v)
            dates.append(k)

    if len(values) < MIN_DAYS:
        return None

    slope, r_sq = _trend_slope(values)
    total_change = slope * (len(values) - 1)

    # Only surface if the trend is meaningful
    if abs(total_change) < 3.0 or r_sq < 0.08:
        return None

    direction = "improving" if slope > 0 else "declining"
    abs_change = abs(round(total_change, 1))

    chart_points = [{"date": d, "value": v} for d, v in zip(dates, values)]

    conf = "moderate" if r_sq > 0.2 else "suggestive"
    headline = f"Deep sleep has been {direction} — {abs_change} points over {len(values)} days."
    context = (
        f"Started around {round(values[0])}, now around {round(values[-1])}. "
        f"The trend explains {round(r_sq * 100)}% of the variance in your deep sleep scores."
    )

    return {
        "id": "deep_sleep_trend",
        "headline": headline,
        "context": context,
        "chart_type": "trend",
        "chart_data": chart_points,
        "baseline": round(_mean(values), 1),
        "trend_slope": round(slope, 2),
        "confidence": conf,
        "stat_basis": f"Linear regression, slope={round(slope, 2)}/day, R²={round(r_sq, 3)}, n={len(values)}",
    }


def _sleep_efficiency_consistency(data, keys):
    """How consistent is sleep efficiency? High variance = problem."""
    values = []
    dates = []
    for k in keys:
        v = data[k].get("efficiency_score")
        if v is not None:
            values.append(v)
            dates.append(k)

    if len(values) < MIN_DAYS:
        return None

    mean = _mean(values)
    sd = _stdev(values)
    cv = (sd / mean * 100) if mean > 0 else 0

    # Only surface if variance is notable
    if sd < 3.0:
        return None

    low_days = sum(1 for v in values if v < mean - sd)
    chart_points = [{"date": d, "value": v} for d, v in zip(dates, values)]

    headline = f"Sleep efficiency swings by ±{round(sd, 1)} points around your {round(mean)} average."
    context = (
        f"{low_days} of {len(values)} nights fell more than one standard deviation below your mean. "
        f"Tightening bedtime consistency may reduce this variance."
    )

    return {
        "id": "efficiency_variance",
        "headline": headline,
        "context": context,
        "chart_type": "trend",
        "chart_data": chart_points,
        "baseline": round(mean, 1),
        "confidence": "moderate" if len(values) >= 14 else "preliminary",
        "stat_basis": f"Descriptive, mean={round(mean, 1)}, SD={round(sd, 1)}, CV={round(cv, 1)}%, n={len(values)}",
    }


def _sleep_score_by_day_of_week(data, keys):
    """Sleep score patterns across the week (catches Friday/Saturday effects)."""
    day_buckets = {i: [] for i in range(7)}
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for k in keys:
        v = data[k].get("sleep_score")
        if v is None:
            continue
        dow = datetime.strptime(k, "%Y-%m-%d").weekday()
        day_buckets[dow].append(v)

    # Find best and worst days
    day_means = {}
    for i in range(7):
        if day_buckets[i]:
            day_means[i] = _mean(day_buckets[i])

    if len(day_means) < 5:
        return None

    best_day = max(day_means, key=day_means.get)
    worst_day = min(day_means, key=day_means.get)
    spread = day_means[best_day] - day_means[worst_day]

    if spread < 4.0:
        return None

    chart_points = []
    for i in range(7):
        vals = day_buckets[i]
        chart_points.append({
            "label": day_names[i],
            "value": round(_mean(vals), 1) if vals else None,
            "n": len(vals),
        })

    overall_mean = _mean([v for vals in day_buckets.values() for v in vals])

    headline = (
        f"You sleep best on {day_names[best_day]}s ({round(day_means[best_day])}) "
        f"and worst on {day_names[worst_day]}s ({round(day_means[worst_day])})."
    )
    context = (
        f"A {round(spread)}-point spread across the week suggests "
        f"your schedule or habits affect sleep quality on specific days. "
        f"Overall average: {round(overall_mean)}."
    )

    return {
        "id": "sleep_by_dow",
        "headline": headline,
        "context": context,
        "chart_type": "bar",
        "chart_data": chart_points,
        "baseline": round(overall_mean, 1),
        "confidence": "suggestive",
        "stat_basis": f"Descriptive, spread={round(spread, 1)}, n={sum(len(v) for v in day_buckets.values())}",
    }
