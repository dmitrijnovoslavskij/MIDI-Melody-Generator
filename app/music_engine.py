import random
import os
import json
import requests
from app.clap_engine import get_embedding, find_similar_tracks

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:14b"

NOTE_MAP = {
    "C": 60, "C#": 61, "D": 62, "D#": 63,
    "E": 64, "F": 65, "F#": 66, "G": 67,
    "G#": 68, "A": 69, "A#": 70, "B": 71,
    "Bb": 70
}

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10]
}

TPB = 480

WHOLE     = TPB * 4
HALF      = TPB * 2
QUARTER   = TPB
EIGHTH    = TPB // 2
SIXTEENTH = TPB // 4
DOTTED_Q  = int(TPB * 1.5)
DOTTED_E  = int(TPB * 0.75)

# ─── Functional harmony tables ────────────────────────────────────────────────
# Degree roles in a 7-note scale (0-based).
# T=tonic, S=subdominant, D=dominant — standard functional harmony.
DEGREE_FUNCTION = {
    "minor": {0: "T", 1: "S", 2: "T", 3: "S", 4: "D", 5: "T", 6: "D"},
    "major": {0: "T", 1: "S", 2: "T", 3: "S", 4: "D", 5: "S", 6: "D"},
}

# Which functions can follow which (classical voice-leading rules)
FUNCTION_GRAPH = {
    "T": ["T", "S", "D"],
    "S": ["S", "D", "T"],
    "D": ["T", "D"],   # dominant resolves to tonic; D→S is weak, avoided
}

# Degrees grouped by function for quick lookup
def _degrees_by_function(mode):
    return {
        fn: [d for d, f in DEGREE_FUNCTION[mode].items() if f == fn]
        for fn in ("T", "S", "D")
    }

def generate_progression(mode: str, length: int = 4) -> list:
    """
    Algorithmically build a chord progression of `length` chords
    following functional harmony rules (T→S→D→T).
    Always starts and ends on tonic (degree 0).
    Returns a list of scale degrees.
    """
    by_fn = _degrees_by_function(mode)
    deg_fn = DEGREE_FUNCTION[mode]

    result = [0]  # start on tonic
    current_fn = "T"

    for _ in range(length - 2):
        next_fns = FUNCTION_GRAPH[current_fn]
        # Weight: prefer moving forward T→S→D, less likely to stay on same function
        fn_weights = []
        for fn in next_fns:
            if fn == current_fn:
                fn_weights.append(0.5)
            elif next_fns.index(fn) > 0:
                fn_weights.append(2.0)
            else:
                fn_weights.append(1.0)
        next_fn = random.choices(next_fns, weights=fn_weights, k=1)[0]
        candidates = by_fn[next_fn]
        # Avoid repeating last degree
        candidates = [d for d in candidates if d != result[-1]] or candidates
        result.append(random.choice(candidates))
        current_fn = next_fn

    # End on tonic — prefer degree 0, occasionally degree 2 (mediant) for colour
    result.append(random.choices([0, 2], weights=[4, 1], k=1)[0])
    return result


# ─── Rhythm generation ────────────────────────────────────────────────────────
# Base atoms: (duration, rest_probability_weight)
_RHYTHM_ATOMS_DENSE = [
    (SIXTEENTH, 0.10),
    (EIGHTH,    0.15),
    (DOTTED_E,  0.20),
    (QUARTER,   0.25),
]
_RHYTHM_ATOMS_SPARSE = [
    (QUARTER,   0.10),
    (DOTTED_Q,  0.15),
    (HALF,      0.20),
    (WHOLE,     0.30),
]
_RHYTHM_ATOMS_NORMAL = _RHYTHM_ATOMS_DENSE + _RHYTHM_ATOMS_SPARSE

def _generate_bar_rhythm(density: str, bar_ticks: int = TPB * 4) -> list:
    """
    Procedurally fill one bar with note/rest slots.
    Returns list of (duration, is_rest) that sum exactly to bar_ticks.
    """
    if density == "dense":
        atoms = _RHYTHM_ATOMS_DENSE
        rest_bias = 0.18   # ~18% of slots become rests
        synco_prob = 0.30  # probability of a syncopation (rest on beat, note off-beat)
    elif density == "sparse":
        atoms = _RHYTHM_ATOMS_SPARSE
        rest_bias = 0.40
        synco_prob = 0.10
    else:
        atoms = _RHYTHM_ATOMS_NORMAL
        rest_bias = 0.25
        synco_prob = 0.20

    durations = [d for d, _ in atoms]
    weights   = [w for _, w in atoms]

    result = []
    remaining = bar_ticks

    while remaining > 0:
        # Only pick durations that fit
        valid = [(d, w) for d, w in zip(durations, weights) if d <= remaining]
        if not valid:
            # Fill remainder with a rest
            result.append((remaining, True))
            break
        vd, vw = zip(*valid)
        dur = random.choices(vd, weights=vw, k=1)[0]

        # Syncopation: occasionally swap a beat-aligned note for rest+shorter note
        is_beat = (bar_ticks - remaining) % QUARTER == 0
        if is_beat and random.random() < synco_prob and dur >= EIGHTH * 2:
            # rest for half the duration, note for the other half
            half = dur // 2
            result.append((half, True))
            result.append((half, False))
        else:
            is_rest = random.random() < rest_bias
            result.append((dur, is_rest))

        remaining -= dur

    # Make sure bar doesn't start or end with all rests — fix first/last slot
    if result and result[0][1]:
        result[0] = (result[0][0], False)
    if result and result[-1][1]:
        result[-1] = (result[-1][0], False)

    return result


# Keep static palettes for chord/bass (they work fine, procedural rhythm only for melody)
CHORD_RHYTHMS_LESS = [
    [(WHOLE, False)],
    [(DOTTED_Q, False), (DOTTED_Q, True), (HALF, True)],
    [(HALF, False), (HALF, True)],
]
CHORD_RHYTHMS_MORE = [
    [(HALF, False), (HALF, False)],
    [(DOTTED_Q, False), (EIGHTH, True), (HALF, False)],
    [(HALF, False), (QUARTER, False), (QUARTER, True)],
]
CHORD_RHYTHMS_NORMAL = CHORD_RHYTHMS_LESS + CHORD_RHYTHMS_MORE

CHORD_RHYTHMS_LESS = [
    [(WHOLE, False)],
    [(DOTTED_Q, False), (DOTTED_Q, True), (HALF, True)],
    [(HALF, False), (HALF, True)],
]
CHORD_RHYTHMS_MORE = [
    [(HALF, False), (HALF, False)],
    [(DOTTED_Q, False), (EIGHTH, True), (HALF, False)],
    [(HALF, False), (QUARTER, False), (QUARTER, True)],
]
CHORD_RHYTHMS_NORMAL = CHORD_RHYTHMS_LESS + CHORD_RHYTHMS_MORE

BASS_PATTERNS = [
    [   # Root + fifth walking
        {"note": "root",  "duration": QUARTER,  "velocity": 90},
        {"note": "rest",  "duration": EIGHTH,   "velocity": 0},
        {"note": "root",  "duration": EIGHTH,   "velocity": 75},
        {"note": "fifth", "duration": QUARTER,  "velocity": 80},
        {"note": "rest",  "duration": QUARTER,  "velocity": 0},
    ],
    [   # Solid half + quarters
        {"note": "root",  "duration": HALF,     "velocity": 85},
        {"note": "root",  "duration": QUARTER,  "velocity": 85},
        {"note": "root",  "duration": QUARTER,  "velocity": 75},
    ],
    [   # Dotted groove
        {"note": "root",  "duration": DOTTED_Q, "velocity": 95},
        {"note": "fifth", "duration": EIGHTH,   "velocity": 80},
        {"note": "rest",  "duration": QUARTER,  "velocity": 0},
        {"note": "root",  "duration": QUARTER,  "velocity": 85},
    ],
    [   # Whole note pedal
        {"note": "root",  "duration": WHOLE,    "velocity": 90}
    ],
    [   # Syncopated offbeat
        {"note": "rest",  "duration": EIGHTH,   "velocity": 0},
        {"note": "root",  "duration": DOTTED_Q, "velocity": 92},
        {"note": "fifth", "duration": EIGHTH,   "velocity": 78},
        {"note": "root",  "duration": QUARTER,  "velocity": 85},
        {"note": "rest",  "duration": EIGHTH,   "velocity": 0},
    ],
    [   # Root on 1 and 3 only
        {"note": "root",  "duration": QUARTER,  "velocity": 88},
        {"note": "rest",  "duration": QUARTER,  "velocity": 0},
        {"note": "root",  "duration": QUARTER,  "velocity": 82},
        {"note": "rest",  "duration": QUARTER,  "velocity": 0},
    ],
    [   # Third as colour
        {"note": "root",  "duration": HALF,     "velocity": 90},
        {"note": "third", "duration": QUARTER,  "velocity": 75},
        {"note": "fifth", "duration": QUARTER,  "velocity": 80},
    ],
    [   # Two-feel
        {"note": "root",  "duration": HALF,     "velocity": 92},
        {"note": "fifth", "duration": HALF,     "velocity": 82},
    ],
]

# ─── Ollama helpers ────────────────────────────────────────────────────────────

# Vibe presets — direct mapping, no Ollama needed for prompt
VIBE_PRESETS = {
    "trap":     {"mode": "minor", "bpm_range": (130, 145)},
    "drill":    {"mode": "minor", "bpm_range": (135, 145)},
    "phonk":    {"mode": "minor", "bpm_range": (135, 155)},
    "flex":     {"mode": "minor", "bpm_range": (140, 155)},
    "dark":     {"mode": "minor", "bpm_range": (65, 85)},
    "lofi":     {"mode": "minor", "bpm_range": (70, 90)},
    "chill":    {"mode": "minor", "bpm_range": (75, 95)},
    "epic":     {"mode": "minor", "bpm_range": (80, 110)},
    "bounce":   {"mode": "major", "bpm_range": (122, 132)},
    "romantic": {"mode": "major", "bpm_range": (80, 100)},
    "summer":   {"mode": "major", "bpm_range": (100, 120)},
    "happy":    {"mode": "major", "bpm_range": (105, 130)},
    "any":      {"mode": None,    "bpm_range": (80, 140)},
}

def get_vibe_hints(vibe: str) -> dict:
    return VIBE_PRESETS.get(vibe, VIBE_PRESETS["any"])




# ─── Scale / chord helpers ─────────────────────────────────────────────────────

def build_scale_full(root="C", mode="minor", octaves=3, base_octave=4):
    root_midi = NOTE_MAP[root] + (base_octave - 4) * 12
    intervals = SCALES[mode]
    notes = []
    for oct in range(octaves):
        for interval in intervals:
            notes.append(root_midi + oct * 12 + interval)
    return notes


def build_chord(root_midi, mode, degree, min_interval=0):
    intervals = SCALES[mode]
    n = len(intervals)
    def scale_note(deg):
        return root_midi + intervals[deg % n] + (deg // n) * 12
    notes = [scale_note(degree), scale_note(degree + 2), scale_note(degree + 4)]
    if min_interval > 0:
        # Spread notes up by octave if they're too close
        for i in range(1, len(notes)):
            while notes[i] - notes[i-1] < min_interval:
                notes[i] += 12
    return notes


def get_progression(root="C", mode="minor", min_interval=0):
    root_midi = NOTE_MAP[root] + (3 - 4) * 12
    # Vary progression length: usually 4 chords, occasionally 3 or 6
    length = random.choices([3, 4, 4, 4, 6], weights=[1, 4, 4, 4, 1], k=1)[0]
    degrees = generate_progression(mode, length=length)
    chords = [build_chord(root_midi, mode, d, min_interval=min_interval) for d in degrees]
    return chords, degrees


def smooth_step(current, scale_notes, max_jump=3):
    if current not in scale_notes:
        current = min(scale_notes, key=lambda x: abs(x - current))
    idx = scale_notes.index(current)
    candidates, weights = [], []
    for i, note in enumerate(scale_notes):
        dist = abs(i - idx)
        if dist == 0 or dist > max_jump:
            continue
        candidates.append(note)
        weights.append(max_jump - dist + 1)
    return random.choices(candidates, weights=weights, k=1)[0] if candidates else current


def chord_tone_or_passing(current, chord, scale_notes, chord_prob=0.55):
    lo, hi = scale_notes[0], scale_notes[-1]
    def clamp(n):
        while n < lo: n += 12
        while n > hi: n -= 12
        return n

    if random.random() < chord_prob:
        clamped = [clamp(n) for n in chord]
        nearby = [n for n in clamped if abs(n - current) <= 12] or clamped
        dists = [1 / (abs(n - current) + 1) for n in nearby]
        return random.choices(nearby, weights=dists, k=1)[0]
    else:
        return smooth_step(current, scale_notes)


# ─── Track generators ──────────────────────────────────────────────────────────

def generate_melody(scale_notes, chords, bars=8, density="normal", variety="normal"):
    melody = []
    melody_scale = [n for n in scale_notes if 60 <= n <= 84] or scale_notes

    root_candidates = [melody_scale[0], melody_scale[2]] if len(melody_scale) > 2 else [melody_scale[0]]
    current = random.choice(root_candidates)
    recent_notes = []

    # Variety hint: more = bigger jumps, lower chord adhesion; less = stepwise, higher chord adhesion
    max_jump_base = 5 if variety == "more" else (2 if variety == "less" else 3)
    chord_prob_base = 0.40 if variety == "more" else (0.70 if variety == "less" else 0.55)
    leap_prob = 0.20 if variety == "more" else (0.04 if variety == "less" else 0.12)

    # ── Motif: capture first 2-3 pitched notes of bar 0, reuse/vary later ──────
    motif_notes = []          # raw MIDI notes of the motif
    motif_captured = False
    # Probability per bar that the motif is replayed (transposed to current chord root)
    motif_replay_prob = 0.30

    for bar in range(bars):
        chord = chords[bar % len(chords)]

        # Procedural rhythm for this bar
        rhythm = _generate_bar_rhythm(density)

        # Decide if we replay the motif this bar (not on bar 0 while capturing)
        replay_motif = (
            motif_captured
            and bar > 0
            and random.random() < motif_replay_prob
        )
        motif_pos = 0  # index into motif_notes when replaying

        for slot_idx, (duration, is_rest) in enumerate(rhythm):
            if is_rest:
                melody.append({"note": 0, "duration": duration, "velocity": 0})
                continue

            # ── Motif replay: use stored interval pattern, transposed ──────────
            if replay_motif and motif_pos < len(motif_notes):
                # Transpose motif relative to current chord root vs original chord root
                original_root = chords[0][0]
                current_root  = chord[0]
                transposed = motif_notes[motif_pos] + (current_root - original_root)
                # Clamp to melody range
                lo, hi = melody_scale[0], melody_scale[-1]
                while transposed < lo: transposed += 12
                while transposed > hi: transposed -= 12
                note = transposed
                motif_pos += 1
                current = note
            else:
                # ── Normal note selection ─────────────────────────────────────
                if len(recent_notes) >= 3 and len(set(recent_notes[-3:])) == 1:
                    note = smooth_step(current, melody_scale, max_jump=max_jump_base + 1)
                else:
                    note = chord_tone_or_passing(current, chord, melody_scale, chord_prob=chord_prob_base)

                if random.random() < leap_prob and len(melody_scale) > 5:
                    leap = random.choice(melody_scale)
                    if abs(leap - current) in (5, 7, 12):
                        note = leap

                current = note

            # ── Capture motif from bar 0 (first 2-3 pitched notes) ───────────
            if bar == 0 and not motif_captured:
                motif_notes.append(note)
                if len(motif_notes) >= random.randint(2, 3):
                    motif_captured = True

            recent_notes.append(note)
            if len(recent_notes) > 8:
                recent_notes.pop(0)

            base_vel = 82
            vel = base_vel + random.randint(-10, 12)
            if not any(e.get("velocity", 0) > 0 for e in melody[-len(rhythm):]):
                vel = min(100, vel + 8)

            melody.append({"note": note, "duration": duration, "velocity": max(60, min(100, vel))})

    return melody


def generate_chords_track(chords, bars=8, density="normal"):
    chord_track = []
    if density == "less":
        rhythms = CHORD_RHYTHMS_LESS
        weights = None
    elif density == "more":
        rhythms = CHORD_RHYTHMS_MORE
        weights = None
    else:
        rhythms = CHORD_RHYTHMS_NORMAL
        weights = [3, 3, 2, 2, 2, 1, 1][:len(rhythms)]

    for bar in range(bars):
        chord = chords[bar % len(chords)]
        rhythm = random.choices(rhythms, weights=weights, k=1)[0] if weights else random.choice(rhythms)

        for duration, is_rest in rhythm:
            if is_rest:
                chord_track.append({"notes": [], "duration": duration, "velocity": 0})
            else:
                chord_track.append({"notes": chord, "duration": duration, "velocity": random.randint(50, 65)})

    return chord_track


def generate_bass(chords, bars=8, activity="normal"):
    bass = []
    last_idx = None

    # Filter pattern pool by activity hint
    if activity == "less":
        pool = [3, 5]   # pedal + sparse root-only
    elif activity == "more":
        pool = [0, 2, 4, 6, 7]  # active patterns
    else:
        pool = list(range(len(BASS_PATTERNS)))

    for bar in range(bars):
        chord = chords[bar % len(chords)]
        root  = chord[0] - 12
        fifth = chord[2] - 12 if len(chord) > 2 else root + 7
        third = chord[1] - 12 if len(chord) > 1 else root + 4

        available = [i for i in pool if i != last_idx] or pool
        idx = random.choice(available)
        last_idx = idx

        for step in BASS_PATTERNS[idx]:
            n = {"root": root, "fifth": fifth, "third": third}.get(step["note"], 0)
            bass.append({"note": n, "duration": step["duration"], "velocity": step["velocity"]})

    return bass


# ─── Feedback-trained parameters ──────────────────────────────────────────────

_DEFAULTS = {
    "keys": ["C", "D", "E", "F", "G", "A", "Bb"],
    "k_weights": None,
    "modes": ["minor", "major"],
    "m_weights": None,
    "bpm_offset": 0,
    "melody_density": "normal",
    "melody_variety": "normal",
    "bass_activity": "normal",
    "chord_density": "normal",
    "chord_min_interval": 0,
}

def get_trained_parameters(vibe: str = "any"):
    """
    Читает feedback.json и вычисляет параметры генерации.

    Логика обучения:
    - Свежие записи весят больше (экспоненциальное затухание по позиции).
    - liked=True  → усиливаем ключ/лад и применяем пожелания из текста.
    - liked=False → ослабляем ключ/лад и ИНВЕРТИРУЕМ пожелания:
        "мелодия монотонная" (melody_variety=more) при дизлайке →
        значит надо ещё МЕНЬШЕ разнообразия? Нет — дизлайк означает,
        что трек в целом плох; текстовые пожелания всё равно применяются
        (пользователь говорит что конкретно поменять), но с меньшим весом.
    - bpm_delta: взвешенное среднее последних N записей.
    - Категориальные параметры (density, variety, etc): взвешенное голосование,
        победитель применяется только если его перевес > порога.
    - Если есть vibe — учитываем только записи с совпадающим промптом (если таких >= 3).
    """
    fb_path = os.path.join(os.path.dirname(__file__), "feedback.json")
    if not os.path.exists(fb_path):
        return dict(_DEFAULTS)

    try:
        with open(fb_path, "r", encoding="utf-8") as f:
            history = json.load(f).get("history", [])
    except Exception:
        return dict(_DEFAULTS)

    # Если последняя запись для этого вайба помечена vibe_mismatch — исключаем этот промпт из CLAP
    vibe_mismatched = any(
        h.get("vibe_mismatch") and h.get("prompt") == vibe
        for h in history[-5:]
    )

    if not history:
        return dict(_DEFAULTS)

    # Фильтрация по вайбу: если есть >= 3 записи с таким промптом — используем только их
    if vibe and vibe != "any":
        vibe_history = [h for h in history if h.get("prompt") == vibe]
        if len(vibe_history) >= 3:
            history = vibe_history
            print(f"[Train] Используем {len(history)} записей для вайба '{vibe}'")
        else:
            print(f"[Train] Для вайба '{vibe}' только {len(vibe_history)} зап. — берём всё ({len(history)})")

    n = len(history)
    # Экспоненциальный decay: самая свежая запись весит в ~5x больше самой старой
    DECAY = 0.85
    weights_by_pos = [DECAY ** (n - 1 - i) for i in range(n)]

    keys_pool  = _DEFAULTS["keys"]
    key_scores  = {k: 0.0 for k in keys_pool}
    mode_scores = {"minor": 0.0, "major": 0.0}
    bpm_weighted_sum = 0.0
    bpm_weight_total = 0.0

    # Для категориальных: score > 0 = "применить", < 0 = "не применять"
    cat_scores = {
        "melody_density_dense":  0.0,
        "melody_density_sparse": 0.0,
        "melody_variety_more":   0.0,
        "melody_variety_less":   0.0,
        "bass_activity_more":    0.0,
        "bass_activity_less":    0.0,
        "chord_density_more":    0.0,
        "chord_density_less":    0.0,
    }
    chord_min_intervals_weighted = []

    for i, item in enumerate(history):
        w     = weights_by_pos[i]
        liked = item.get("liked", True)
        k     = item.get("key")
        m     = item.get("mode")

        # Сила сигнала: лайк усиливает сильнее, дизлайк ослабляет мягче
        like_w = w * 2.0 if liked else w * -0.8

        if k in key_scores:
            key_scores[k] += like_w

        pm = item.get("preferred_mode")
        if pm in mode_scores:
            # Явное пожелание лада из текста — всегда положительный сигнал
            mode_scores[pm] += w * 2.5
        elif m in mode_scores:
            mode_scores[m] += like_w

        # BPM delta — учитываем только если лайк (дизлайк без delta = непонятно почему не понравилось)
        bd = item.get("bpm_delta")
        if bd is not None:
            bpm_weighted_sum += bd * w
            bpm_weight_total += w

        # Категориальные — текстовые жалобы применяются при любом liked
        # (пользователь явно говорит что поменять)
        cat_w = w * (1.5 if liked else 1.0)
        for field, val_map in [
            ("melody_density", {"dense": "melody_density_dense", "sparse": "melody_density_sparse"}),
            ("melody_variety", {"more":  "melody_variety_more",  "less":  "melody_variety_less"}),
            ("bass_activity",  {"more":  "bass_activity_more",   "less":  "bass_activity_less"}),
            ("chord_density",  {"more":  "chord_density_more",   "less":  "chord_density_less"}),
        ]:
            v = item.get(field)
            if v and v in val_map:
                cat_scores[val_map[v]] += cat_w

        cmi = item.get("chord_min_interval")
        if cmi is not None:
            chord_min_intervals_weighted.append((int(cmi), w))

    # ── Ключ: нормализуем в вероятности ──────────────────────────────────────
    # Сдвигаем все scores чтобы минимум был > 0
    min_ks = min(key_scores.values())
    adj_ks = {k: v - min_ks + 0.1 for k, v in key_scores.items()}

    # ── Лад ───────────────────────────────────────────────────────────────────
    min_ms = min(mode_scores.values())
    adj_ms = {m: v - min_ms + 0.1 for m, v in mode_scores.items()}

    # ── BPM offset ────────────────────────────────────────────────────────────
    bpm_offset = int(bpm_weighted_sum / bpm_weight_total) if bpm_weight_total > 0 else 0
    bpm_offset = max(-20, min(20, bpm_offset))  # clamp

    # ── Категориальные: победитель должен иметь перевес >= 0.3 от суммы ──────
    THRESHOLD = 0.3
    def resolve_cat(key_a, key_b, normal="normal"):
        sa, sb = cat_scores[key_a], cat_scores[key_b]
        total = sa + sb
        if total < 0.01:
            return normal
        if sa / total >= (0.5 + THRESHOLD):
            return key_a.split("_")[-1]   # e.g. "dense"
        if sb / total >= (0.5 + THRESHOLD):
            return key_b.split("_")[-1]
        return normal

    melody_density = resolve_cat("melody_density_dense",  "melody_density_sparse")
    melody_variety = resolve_cat("melody_variety_more",   "melody_variety_less")
    bass_activity  = resolve_cat("bass_activity_more",    "bass_activity_less")
    chord_density  = resolve_cat("chord_density_more",    "chord_density_less")

    # ── chord_min_interval: взвешенный максимум ───────────────────────────────
    chord_min_interval = 0
    if chord_min_intervals_weighted:
        # берём взвешенное среднее, округляем вверх
        ws = sum(w for _, w in chord_min_intervals_weighted)
        chord_min_interval = int(
            round(sum(v * w for v, w in chord_min_intervals_weighted) / ws)
        )

    params = {
        "keys":               list(adj_ks.keys()),
        "k_weights":          list(adj_ks.values()),
        "modes":              list(adj_ms.keys()),
        "m_weights":          list(adj_ms.values()),
        "bpm_offset":         bpm_offset,
        "melody_density":     melody_density,
        "melody_variety":     melody_variety,
        "bass_activity":      bass_activity,
        "chord_density":      chord_density,
        "chord_min_interval": chord_min_interval,
        "vibe_mismatched":    vibe_mismatched,
    }

    print(f"[Train] n={n} bpm_offset={bpm_offset:+d} "
          f"density={melody_density} variety={melody_variety} "
          f"bass={bass_activity} chords={chord_density} "
          f"cmi={chord_min_interval}")
    return params


# ─── Main entry point ──────────────────────────────────────────────────────────

def generate_music_plan(vibe: str = "any", mode_hint: str = "auto", bpm: int = 120, bars: int = 8):
    params = get_trained_parameters(vibe=vibe)
    vibe_hints = get_vibe_hints(vibe)

    # 1. CLAP: ищем похожие треки из истории по вайбу (пропускаем если vibe_mismatch)
    prompt_emb = get_embedding(vibe)
    clap_hints = {}
    if prompt_emb and not params.get("vibe_mismatched"):
        fb_path = os.path.join(os.path.dirname(__file__), "feedback.json")
        history = []
        if os.path.exists(fb_path):
            try:
                with open(fb_path, "r", encoding="utf-8") as f:
                    history = json.load(f).get("history", [])
            except Exception:
                pass
        similar = find_similar_tracks(prompt_emb, history, top_k=5)
        good = [t for t in similar if t.get("liked") and t.get("similarity", 0) > 0.75]
        if good:
            key_votes, mode_votes, bpm_deltas = {}, {}, []
            for t in good:
                w = t["similarity"]
                if t.get("key"): key_votes[t["key"]] = key_votes.get(t["key"], 0) + w
                if t.get("mode"): mode_votes[t["mode"]] = mode_votes.get(t["mode"], 0) + w
                if t.get("bpm_delta"): bpm_deltas.append(t["bpm_delta"] * w)
            if key_votes: clap_hints["key"] = max(key_votes, key=key_votes.get)
            if mode_votes: clap_hints["mode"] = max(mode_votes, key=mode_votes.get)
            if bpm_deltas: clap_hints["bpm_delta"] = sum(bpm_deltas) / len(bpm_deltas)

    # 2. Key: CLAP > feedback-weighted random
    key = clap_hints.get("key")
    if not key or key not in NOTE_MAP:
        key = random.choices(params["keys"], weights=params["k_weights"], k=1)[0]

    # 3. Mode: UI explicit > vibe preset > CLAP > feedback-weighted random
    if mode_hint in ("minor", "major"):
        mode = mode_hint
    elif vibe_hints.get("mode"):
        mode = vibe_hints["mode"]
    elif clap_hints.get("mode") in ("minor", "major"):
        mode = clap_hints["mode"]
    else:
        mode = random.choices(params["modes"], weights=params["m_weights"], k=1)[0]

    # 4. BPM: UI value clamped to vibe range + feedback offset
    lo, hi = vibe_hints["bpm_range"]
    bpm_clamped = max(lo, min(hi, bpm))
    bpm = max(55, min(160, bpm_clamped + params["bpm_offset"]))

    scale_notes = build_scale_full(key, mode, octaves=3, base_octave=3)
    chords, degrees = get_progression(key, mode, min_interval=params["chord_min_interval"])

    return {
        "prompt":      vibe,
        "key":         key,
        "mode":        mode,
        "bpm":         bpm,
        "scale":       scale_notes,
        "chords":      chords,
        "degrees":     degrees,
        "melody":      generate_melody(scale_notes, chords, bars=bars,
                                       density=params["melody_density"],
                                       variety=params["melody_variety"]),
        "chord_track": generate_chords_track(chords, bars=bars,
                                             density=params["chord_density"]),
        "bass":        generate_bass(chords, bars=bars,
                                     activity=params["bass_activity"]),
        "tpb":         TPB,
    }