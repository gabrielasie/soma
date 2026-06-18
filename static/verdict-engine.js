/**
 * Soma Verdict Engine
 * Deterministic rules engine for daily readiness verdicts.
 * No LLM dependency — pure signal → verdict mapping.
 *
 * Usage:
 *   const result = generateVerdict(signals, calendar, weather);
 *   // result.verdict    — "green" | "steady" | "watch" | "red"
 *   // result.headline   — human-readable one-liner
 *   // result.reasoning  — array of plain-language sentences
 *   // result.signalRows — array of { signal, today, baseline, weight, delta, flag }
 *
 * Exports: generateVerdict (attached to globalThis for browser, or module.exports for Node)
 */

/* ── Signal definitions ──────────────────────────────────────
 *  key        — matches API signal name
 *  label      — human-readable name for the table
 *  weight     — contribution to composite score (0–1, all weights sum to ~1.0)
 *  threshold  — z-score thresholds for flag assignment:
 *                 |z| < ok      → "normal"
 *                 ok ≤ |z| < warn → "mild" drag/boost
 *                 |z| ≥ warn    → "notable" drag/boost
 */
const SIGNAL_DEFS = [
  { key: 'sleep_score',       label: 'Sleep score',    weight: 0.25, ok: 0.5, warn: 1.2 },
  { key: 'deep_sleep_score',  label: 'Deep sleep',     weight: 0.15, ok: 0.5, warn: 1.2 },
  { key: 'hrv_balance',       label: 'HRV',            weight: 0.25, ok: 0.5, warn: 1.2 },
  { key: 'readiness_score',   label: 'Readiness',      weight: 0.25, ok: 0.5, warn: 1.2 },
  { key: 'recovery_index',    label: 'Recovery index',  weight: 0.10, ok: 0.5, warn: 1.2 },
];

/* ── Context signals (not from Oura — derived from calendar/weather) ── */
const CALENDAR_WEIGHT = 0.0;   // informational, does not shift composite
const WEATHER_WEIGHT  = 0.0;   // informational, does not shift composite

/* ── Verdict thresholds ──────────────────────────────────────
 *  Composite score is the weighted sum of per-signal scores.
 *  Each signal score:  +1 (above baseline), 0 (normal), −1 (mild drag), −2 (notable drag)
 *
 *  composite ≥  0.3  → green     "Green light"
 *  composite ≥ -0.2  → steady    "Steady"    (yellow)
 *  composite ≥ -0.8  → watch     "Watch"     (orange)
 *  composite <  -0.8 → red       "Recovery day"
 */
const THRESHOLDS = { green: 0.3, steady: -0.2, watch: -0.8 };

/**
 * Score a single biometric signal against its baseline.
 * Returns { score, flag, delta }
 *   score: numeric contribution (−2 to +1)
 *   flag:  "above" | "normal" | "mild_drag" | "notable_drag" | "no_data"
 *   delta: human-readable "+3" / "−4" / "—"
 */
function scoreSignal(def, sig) {
  if (!sig || sig.value == null || sig.baseline_mean == null) {
    return { score: 0, flag: 'no_data', delta: '—', today: null, baseline: null };
  }
  const today = sig.value;
  const baseline = sig.baseline_mean;
  const z = sig.z_score != null ? sig.z_score : (sig.baseline_stdev ? (today - baseline) / sig.baseline_stdev : 0);
  const diff = Math.round(today - baseline);

  let score, flag;
  if (z >= def.ok) {
    score = 1; flag = 'above';
  } else if (z >= -def.ok) {
    score = 0; flag = 'normal';
  } else if (z >= -def.warn) {
    score = -1; flag = 'mild_drag';
  } else {
    score = -2; flag = 'notable_drag';
  }

  const sign = diff >= 0 ? '+' : '';
  return {
    score,
    flag,
    delta: sign + diff,
    today: Math.round(today),
    baseline: Math.round(baseline),
  };
}

/**
 * Assess calendar density.
 * Returns { label, flag, detail }
 */
function scoreCalendar(events, freeBlocks) {
  events = (events || []).filter(e => !e.all_day && e.start_time);
  const totalEvents = events.length;
  const totalFreeMin = (freeBlocks || []).reduce((s, b) => s + (b.duration_min || 0), 0);

  let flag, label;
  if (totalEvents <= 2 && totalFreeMin >= 240) {
    flag = 'light'; label = 'Light';
  } else if (totalEvents <= 4 || totalFreeMin >= 120) {
    flag = 'moderate'; label = 'Moderate';
  } else {
    flag = 'dense'; label = 'Dense';
  }

  return {
    flag,
    label,
    detail: totalEvents + ' event' + (totalEvents !== 1 ? 's' : '') +
            ', ' + Math.round(totalFreeMin / 60) + 'h free',
  };
}

/**
 * Assess weather impact on training/outdoor plans.
 * Returns { label, flag, detail }
 */
function scoreWeather(weather) {
  if (!weather) return { flag: 'unknown', label: '—', detail: 'No data' };
  const temp = weather.temp_high != null ? weather.temp_high : 20;
  const precip = weather.precip_chance_max || 0;
  const cond = (weather.conditions || '').toLowerCase();

  let flag, label;
  if (precip > 60 || temp < -5 || temp > 38) {
    flag = 'poor'; label = 'Poor';
  } else if (precip > 30 || temp < 3 || temp > 33) {
    flag = 'fair'; label = 'Fair';
  } else {
    flag = 'good'; label = 'Good';
  }

  const tempStr = Math.round(temp) + '°C';
  const detail = tempStr + ', ' + (cond || 'unknown') +
                 (precip > 0 ? ', ' + precip + '% precip' : '');
  return { flag, label, detail };
}

/**
 * Generate the full verdict from raw API data.
 *
 * @param {Object} signals     — d.signals from /api/brief
 * @param {Object} calendar    — { events: d.events, freeBlocks: d.free_blocks }
 * @param {Object} weather     — d.weather_summary
 * @returns {{ verdict, headline, reasoning, signalRows }}
 */
function generateVerdict(signals, calendar, weather) {
  signals = signals || {};

  // 1. Score each biometric signal
  const scored = SIGNAL_DEFS.map(def => {
    const result = scoreSignal(def, signals[def.key]);
    return { ...def, ...result };
  });

  // 2. Compute weighted composite
  const totalWeight = scored.reduce((s, r) => s + (r.flag !== 'no_data' ? r.weight : 0), 0) || 1;
  const composite = scored.reduce((s, r) => s + r.score * r.weight, 0) / totalWeight;

  // 3. Determine verdict
  //    Conservative rule: any drag prevents green (matches backend).
  //    Any notable drag forces at least watch.
  const hasDrag = scored.some(s => s.flag === 'mild_drag' || s.flag === 'notable_drag');
  const hasNotableDrag = scored.some(s => s.flag === 'notable_drag');

  let verdict;
  if (composite >= THRESHOLDS.green && !hasDrag) verdict = 'green';
  else if (composite >= THRESHOLDS.steady && !hasNotableDrag) verdict = 'steady';
  else if (composite >= THRESHOLDS.watch) verdict = 'watch';
  else verdict = 'red';

  // 4. Context signals (informational — don't shift the composite)
  const cal = scoreCalendar(calendar.events, calendar.freeBlocks);
  const wx = scoreWeather(weather);

  // 5. Build signal rows for the table
  const signalRows = scored.map(s => ({
    signal: s.label,
    today: s.today != null ? String(s.today) : '—',
    baseline: s.baseline != null ? String(s.baseline) : '—',
    weight: s.weight,
    delta: s.delta,
    flag: s.flag,
  }));
  signalRows.push({
    signal: 'Calendar density',
    today: cal.label,
    baseline: '—',
    weight: CALENDAR_WEIGHT,
    delta: cal.detail,
    flag: cal.flag === 'dense' ? 'mild_drag' : 'normal',
  });
  signalRows.push({
    signal: 'Weather',
    today: wx.label,
    baseline: '—',
    weight: WEATHER_WEIGHT,
    delta: wx.detail,
    flag: wx.flag === 'poor' ? 'mild_drag' : 'normal',
  });

  // 6. Generate reasoning sentences
  const reasoning = buildReasoning(scored, cal, wx, verdict, composite);

  // 7. Headline
  const HEADLINES = {
    green: 'Green light',
    steady: 'Steady',
    watch: 'Watch',
    red: 'Recovery day',
  };

  return {
    verdict,
    headline: HEADLINES[verdict],
    reasoning,
    signalRows,
    composite: Math.round(composite * 100) / 100,
  };
}

/**
 * Build 2–4 plain-language sentences explaining the verdict.
 */
function buildReasoning(scored, cal, wx, verdict, composite) {
  const sentences = [];

  // Drags
  const drags = scored.filter(s => s.flag === 'notable_drag' || s.flag === 'mild_drag');
  const boosts = scored.filter(s => s.flag === 'above');
  const normals = scored.filter(s => s.flag === 'normal');

  if (drags.length === 0 && boosts.length > 0) {
    sentences.push(listNames(boosts) + (boosts.length === 1 ? ' is ' : ' are ') +
                   'above baseline, with nothing pulling down.');
  } else if (drags.length === 0) {
    sentences.push('All signals are within normal range.');
  } else {
    const notable = drags.filter(s => s.flag === 'notable_drag');
    const mild = drags.filter(s => s.flag === 'mild_drag');
    if (notable.length > 0) {
      sentences.push(listNames(notable) + (notable.length === 1 ? ' is ' : ' are ') +
                     'notably below baseline.');
    }
    if (mild.length > 0) {
      sentences.push(listNames(mild) + (mild.length === 1 ? ' is a ' : ' are ') +
                     'mild drag' + (mild.length === 1 ? '' : 's') + '.');
    }
    if (boosts.length > 0) {
      sentences.push(listNames(boosts) + (boosts.length === 1 ? ' is ' : ' are ') +
                     'holding above baseline.');
    }
  }

  // Calendar context
  if (cal.flag === 'dense') {
    sentences.push('Calendar density is high, which limits available training windows.');
  } else if (cal.flag === 'light') {
    sentences.push('Calendar is light today, leaving room for a full training block.');
  }

  // Weather context
  if (wx.flag === 'poor') {
    sentences.push('Weather conditions are poor for outdoor activity.');
  }

  // Verdict explanation — name the specific cause when possible
  const dragNames = drags.map(s => s.label.toLowerCase());
  const VERDICT_REASON = {
    green:  'The verdict is green — body signals support full capacity.',
    steady: drags.length > 0
      ? 'The verdict is steady, not green, because of the ' + dragNames.join(' and ') + ' drag' + (drags.length > 1 ? 's' : '') + '.'
      : 'The verdict is steady — nothing alarming, but not fully clear either.',
    watch:  'The verdict is watch — multiple signals are pulling below baseline.',
    red:    'The verdict is recovery day — prioritize rest and restoration.',
  };
  sentences.push(VERDICT_REASON[verdict]);

  return sentences;
}

function listNames(items) {
  const names = items.map(s => s.label.toLowerCase());
  if (names.length === 1) return capitalize(names[0]);
  if (names.length === 2) return capitalize(names[0]) + ' and ' + names[1];
  return capitalize(names.slice(0, -1).join(', ')) + ', and ' + names[names.length - 1];
}

function capitalize(s) { return s.charAt(0).toUpperCase() + s.slice(1); }

/* ── Export ─────────────────────────────────────────────── */
if (typeof globalThis !== 'undefined') globalThis.generateVerdict = generateVerdict;
if (typeof module !== 'undefined' && module.exports) module.exports = { generateVerdict };
