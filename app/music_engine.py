"""
music_engine.py — гибридный генератор (Алгоритмика + локальная LLM)
"""
import random
import json
import requests

# ─── Note / Scale tables ───────────────────────────────────────────────────────
NOTE_MAP = {
    "C": 60, "C#": 61, "Db": 61, "D": 62, "D#": 63, "Eb": 63,
    "E": 64, "F": 65, "F#": 66, "Gb": 66, "G": 67,
    "G#": 68, "Ab": 68, "A": 69, "A#": 70, "Bb": 70, "B": 71,
}

SCALES = {
    "major":      [0, 2, 4, 5, 7, 9, 11],
    "minor":      [0, 2, 3, 5, 7, 8, 10],
    "dorian":     [0, 2, 3, 5, 7, 9, 10],
    "phrygian":   [0, 1, 3, 5, 7, 8, 10],
    "lydian":     [0, 2, 4, 6, 7, 9, 11],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian":    [0, 1, 3, 5, 6, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
    "blues":      [0, 3, 5, 6, 7, 10],
    "whole_tone": [0, 2, 4, 6, 8, 10],
}

TPB = 480
WHOLE     = TPB * 4
HALF      = TPB * 2
QUARTER   = TPB
EIGHTH    = TPB // 2
SIXTEENTH = TPB // 4
DOTTED_Q  = int(TPB * 1.5)
DOTTED_E  = int(TPB * 0.75)

# ─── Chord progressions (presets by name) ─────────────────────────────────────
PROGRESSIONS = {
    "I-IV-V-I":      [0, 3, 4, 0],
    "I-V-vi-IV":     [0, 4, 5, 3],
    "I-IV-vi-V":     [0, 3, 5, 4],
    "I-vi-IV-V":     [0, 5, 3, 4],
    "ii-V-I":        [1, 4, 0],
    "I-VII-VI-VII":  [0, 6, 5, 6],
    "i-VII-VI-VII":  [0, 6, 5, 6],
    "i-iv-V-i":      [0, 3, 4, 0],
    "i-VI-III-VII":  [0, 5, 2, 6],
    "i-iv-i-V":      [0, 3, 0, 4],
    "I-III-IV-iv":   [0, 2, 3, 3],
    "random":        None,
}

# ─── Bass patterns ─────────────────────────────────────────────────────────────
BASS_PATTERNS = {
    "root_only": [
        [{"note": "root", "duration": WHOLE, "velocity": 85}],
    ],
    "root_fifth": [
        [{"note": "root",  "duration": HALF,    "velocity": 88},
         {"note": "fifth", "duration": HALF,    "velocity": 78}],
        [{"note": "root",  "duration": DOTTED_Q,"velocity": 90},
         {"note": "fifth", "duration": EIGHTH,  "velocity": 78},
         {"note": "root",  "duration": QUARTER, "velocity": 82},
         {"note": "rest",  "duration": QUARTER, "velocity": 0}],
    ],
    "walking": [
        [{"note": "root",  "duration": QUARTER, "velocity": 88},
         {"note": "third", "duration": QUARTER, "velocity": 78},
         {"note": "fifth", "duration": QUARTER, "velocity": 82},
         {"note": "third", "duration": QUARTER, "velocity": 75}],
        [{"note": "root",  "duration": QUARTER, "velocity": 90},
         {"note": "root",  "duration": EIGHTH,  "velocity": 75},
         {"note": "fifth", "duration": EIGHTH,  "velocity": 78},
         {"note": "fifth", "duration": QUARTER, "velocity": 80},
         {"note": "rest",  "duration": QUARTER, "velocity": 0}],
    ],
    "off_beat": [
        [{"note": "rest",  "duration": EIGHTH,  "velocity": 0},
         {"note": "root",  "duration": DOTTED_Q,"velocity": 92},
         {"note": "fifth", "duration": EIGHTH,  "velocity": 78},
         {"note": "root",  "duration": QUARTER, "velocity": 85},
         {"note": "rest",  "duration": EIGHTH,  "velocity": 0}],
    ],
    "alberti": [
        [{"note": "root",  "duration": EIGHTH,  "velocity": 85},
         {"note": "fifth", "duration": EIGHTH,  "velocity": 72},
         {"note": "third", "duration": EIGHTH,  "velocity": 72},
         {"note": "fifth", "duration": EIGHTH,  "velocity": 72},
         {"note": "root",  "duration": EIGHTH,  "velocity": 85},
         {"note": "fifth", "duration": EIGHTH,  "velocity": 72},
         {"note": "third", "duration": EIGHTH,  "velocity": 72},
         {"note": "fifth", "duration": EIGHTH,  "velocity": 72}],
    ],
}

# ─── Scale helpers ─────────────────────────────────────────────────────────────
def build_scale_full(root="C", mode="minor", octaves=3, base_octave=3):
    root_midi = NOTE_MAP[root] + (base_octave - 4) * 12
    intervals = SCALES[mode]
    notes = []
    for oct in range(octaves):
        for interval in intervals:
            notes.append(root_midi + oct * 12 + interval)
    return sorted(set(notes))

def build_chord(root_midi, mode, degree, voicing="close", min_interval=0):
    intervals = SCALES[mode]
    n = len(intervals)
    def scale_note(deg):
        return root_midi + intervals[deg % n] + (deg // n) * 12
    notes = [scale_note(degree), scale_note(degree + 2), scale_note(degree + 4)]
    if voicing in ("seventh", "open_seventh"):
        notes.append(scale_note(degree + 6))
    if voicing in ("open", "open_seventh"):
        if len(notes) >= 3:
            notes[1] += 12
    if min_interval > 0:
        for i in range(1, len(notes)):
            while notes[i] - notes[i-1] < min_interval:
                notes[i] += 12
    return notes

def get_progression_chords(root="C", mode="minor", progression_name="I-V-vi-IV",
                           voicing="close", min_interval=0):
    root_midi = NOTE_MAP[root] + (3 - 4) * 12
    degrees = PROGRESSIONS.get(progression_name)
    if degrees is None:
        from_pool = [0, 2, 3, 4, 5]
        length = random.choice([3, 4, 4, 6])
        degrees = [0] + [random.choice(from_pool) for _ in range(length - 2)] + [0]
    chords = [build_chord(root_midi, mode, d, voicing=voicing, min_interval=min_interval)
              for d in degrees]
    return chords, degrees

# ─── LLM Integration ───────────────────────────────────────────────────────────
def get_llm_motif(mode, bpm):
    """
    Обращается к локальной LLM через Ollama для генерации осмысленного мотива.
    """
    prompt = f"""You are a multi-platinum producer creating a melodic trap beat (Juice Wrld style).
    Generate a catchy, emotional lead melody motif (4 to 8 notes) for a {mode} scale at {bpm} BPM.
    Respond strictly with valid JSON representing a list of notes. Do not include any markdown formatting or extra text.
    Use 'degree' (1-7 for scale degree) and 'dur' (choose from: "sixteenth", "eighth", "dotted_eighth", "quarter", "half").
    Example:
    [
      {{"degree": 1, "dur": "eighth"}},
      {{"degree": 3, "dur": "sixteenth"}},
      {{"degree": 2, "dur": "dotted_eighth"}}
    ]"""
    
    try:
        # Стучимся в локальную Ollama. Модель llama3 обычно стоит по умолчанию.
        resp = requests.post("http://localhost:11434/api/generate", json={
            "model": "llama3",
            "prompt": prompt,
            "stream": False,
            "format": "json"
        }, timeout=8)
        
        if resp.status_code == 200:
            data = resp.json()
            motif = json.loads(data["response"])
            if isinstance(motif, list) and len(motif) > 0:
                return motif
    except Exception as e:
        print(f"[LLM Motif] Ошибка или Ollama не запущена. Откат на алгоритмику: {e}")
    return None

def parse_llm_motif(llm_motif, scale_notes):
    """Превращает JSON от LLM во внутренний формат ритма и нот"""
    dur_map = {
        "sixteenth": SIXTEENTH,
        "eighth": EIGHTH,
        "dotted_eighth": DOTTED_E,
        "quarter": QUARTER,
        "half": HALF
    }
    rhythm = []
    notes = []
    
    for item in llm_motif:
        deg = max(1, min(7, item.get("degree", 1))) - 1
        note = scale_notes[deg % len(scale_notes)]
        dur_str = item.get("dur", "eighth")
        dur_ticks = dur_map.get(dur_str, EIGHTH)
        
        rhythm.append((dur_ticks, False))
        notes.append(note)
        
    return rhythm, notes

# ─── Rhythm generators ──────────────────────────────────────────────────────────
def _make_rhythm(style: str, bar_ticks=WHOLE) -> list:
    if style == "whole":
        return [(WHOLE, False)]
    if style == "half":
        return [(HALF, False), (HALF, False)]
    if style == "quarter":
        return [(QUARTER, False)] * 4
    if style == "eighth":
        return [(EIGHTH, False)] * 8
    if style == "dotted":
        return [(DOTTED_Q, False), (EIGHTH, False), (DOTTED_Q, False), (EIGHTH, False)]
    if style == "syncopated":
        return [(EIGHTH, True), (DOTTED_Q, False), (DOTTED_Q, False), (EIGHTH, False)]
    if style == "modern_trap":
        TRIPLET_8 = TPB // 3       
        TRIPLET_16 = (TPB // 2) // 3 
        atoms = [EIGHTH, DOTTED_E, SIXTEENTH, QUARTER, TRIPLET_8]
        weights = [0.25, 0.40, 0.15, 0.10, 0.10]
        rest_prob = 0.30
    elif style == "mixed":
        atoms = [SIXTEENTH, EIGHTH, DOTTED_E, QUARTER, DOTTED_Q, HALF]
        weights = [0.05, 0.20, 0.15, 0.30, 0.15, 0.15]
        rest_prob = 0.20
    elif style == "dense":
        atoms = [SIXTEENTH, EIGHTH, DOTTED_E, QUARTER]
        weights = [0.15, 0.30, 0.25, 0.30]
        rest_prob = 0.15
    elif style == "sparse":
        atoms = [QUARTER, DOTTED_Q, HALF, WHOLE]
        weights = [0.15, 0.20, 0.35, 0.30]
        rest_prob = 0.35
    else:  # "normal"
        atoms = [EIGHTH, DOTTED_E, QUARTER, DOTTED_Q, HALF]
        weights = [0.15, 0.15, 0.35, 0.20, 0.15]
        rest_prob = 0.22

    result = []
    remaining = bar_ticks
    while remaining > 0:
        valid = [(d, w) for d, w in zip(atoms, weights) if d <= remaining]
        if not valid:
            result.append((remaining, True))
            break
        vd, vw = zip(*valid)
        dur = random.choices(vd, weights=vw, k=1)[0]
        is_rest = random.random() < rest_prob
        result.append((dur, is_rest))
        remaining -= dur

    if result and result[0][1]:   result[0]  = (result[0][0],  False)
    if result and result[-1][1]:  result[-1] = (result[-1][0], False)
    return result

# ─── Melody note selection ──────────────────────────────────────────────────────
def _smooth_step(current, scale_notes, max_jump=3):
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

def _chord_tone_or_passing(current, chord, scale_notes, chord_prob=0.55):
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
        return _smooth_step(current, scale_notes)

# ─── Arpeggio generator ────────────────────────────────────────────────────────
def generate_arpeggio(chords, bars=8, pattern="up", note_duration=EIGHTH, velocity=72):
    arp_orders = {
        "up":       lambda c: c,
        "down":     lambda c: list(reversed(c)),
        "up_down":  lambda c: c + list(reversed(c[1:-1])),
        "random":   lambda c: random.sample(c, len(c)),
        "outside_in": lambda c: [c[0], c[-1], c[1], c[-2]] if len(c) >= 4 else c,
    }
    order_fn = arp_orders.get(pattern, arp_orders["up"])
    result = []
    bar_ticks = WHOLE
    for bar in range(bars):
        chord = chords[bar % len(chords)]
        extended = chord + [n + 12 for n in chord]
        sequence = order_fn(extended)
        ticks = 0
        idx = 0
        while ticks < bar_ticks:
            note = sequence[idx % len(sequence)]
            dur = min(note_duration, bar_ticks - ticks)
            result.append({"note": note, "duration": dur, "velocity": velocity})
            ticks += dur
            idx += 1
    return result

# ─── Melody generator ──────────────────────────────────────────────────────────
def generate_melody(scale_notes, chords, bars=8, bpm=90, mode="minor",
                    rhythm_style="modern_trap",
                    max_jump=3,
                    chord_prob=0.55,
                    leap_prob=0.10,
                    octave_range=(60, 84),
                    motif_repeat=True,
                    velocity_base=82,
                    velocity_variance=12,
                    use_llm=True):
    melody = []
    lo, hi = octave_range
    melody_scale = [n for n in scale_notes if lo <= n <= hi] or scale_notes
    if not melody_scale:
        return []

    root_candidates = [melody_scale[0], melody_scale[2]] if len(melody_scale) > 2 else [melody_scale[0]]
    current = random.choice(root_candidates)
    recent_notes = []

    motif_notes = []
    llm_rhythm = None
    motif_captured = False

    # Если включен LLM, пробуем получить умный мотив
    if use_llm:
        raw_motif = get_llm_motif(mode, bpm)
        if raw_motif:
            llm_rhythm, motif_notes = parse_llm_motif(raw_motif, melody_scale)
            motif_captured = True

    for bar in range(bars):
        chord = chords[bar % len(chords)]
        
        # Применяем ритм: либо от LLM (в первый такт или при повторе), либо генерим случайно
        if motif_captured and (bar == 0 or (motif_repeat and bar % 4 in [0, 1, 2])):
            rhythm = llm_rhythm if llm_rhythm else _make_rhythm(rhythm_style)
        else:
            rhythm = _make_rhythm(rhythm_style)

        bar_in_loop = bar % 4
        replay = (motif_repeat and motif_captured and bar_in_loop in [1, 2])
        motif_pos = 0

        for i, (duration, is_rest) in enumerate(rhythm):
            if is_rest:
                melody.append({"note": 0, "duration": duration, "velocity": 0})
                continue

            # Если мы проигрываем мотив (от LLM или захваченный ранее)
            if replay and motif_pos < len(motif_notes):
                orig_root = chords[0][0]
                curr_root = chord[0]
                note = motif_notes[motif_pos] + (curr_root - orig_root)
                while note < lo: note += 12
                while note > hi: note -= 12
                motif_pos += 1
                current = note
            else:
                # Алгоритмическая генерация (или продолжение после мотива)
                if len(recent_notes) >= 3 and len(set(recent_notes[-3:])) == 1:
                    note = _smooth_step(current, melody_scale, max_jump=max_jump + 1)
                else:
                    note = _chord_tone_or_passing(current, chord, melody_scale,
                                                  chord_prob=chord_prob)
                if random.random() < leap_prob and len(melody_scale) > 5:
                    leap = random.choice(melody_scale)
                    if abs(leap - current) in (5, 7, 12):
                        note = leap
                current = note

            # Захватываем мотив, если LLM была выключена или недоступна
            if bar == 0 and not motif_captured:
                motif_notes.append(note)
                if len(motif_notes) >= random.randint(3, 5):
                    motif_captured = True

            recent_notes.append(note)
            if len(recent_notes) > 8:
                recent_notes.pop(0)

            # Гуманизация велосити
            is_downbeat = (i % 2 == 0)
            vel_mod = velocity_variance if is_downbeat else -velocity_variance
            vel = velocity_base + vel_mod + random.randint(-5, 5)
            vel = max(55, min(110, vel))
            
            melody.append({"note": note, "duration": duration, "velocity": vel})

    return melody

# ─── Chord track generator ──────────────────────────────────────────────────────
CHORD_RHYTHMS = {
    "whole":     [(WHOLE, False)],
    "half":      [(HALF, False), (HALF, False)],
    "half_rest": [(HALF, False), (HALF, True)],
    "quarter":   [(QUARTER, False)] * 4,
    "offbeat":   [(QUARTER, True), (QUARTER, False), (QUARTER, True), (QUARTER, False)],
    "sparse":    [(WHOLE, False)],
    "normal":    None,
    "dense":     None,
}

def generate_chords_track(chords, bars=8, chord_rhythm="normal", velocity=58):
    track = []
    rhythm_normal = [
        [(WHOLE, False)],
        [(HALF, False), (HALF, True)],
        [(HALF, False), (HALF, False)],
        [(DOTTED_Q, False), (EIGHTH, True), (HALF, False)],
    ]
    rhythm_dense = [
        [(HALF, False), (HALF, False)],
        [(QUARTER, False)] * 4,
        [(DOTTED_Q, False), (EIGHTH, False), (QUARTER, False), (QUARTER, False)],
    ]

    for bar in range(bars):
        chord = chords[bar % len(chords)]
        if chord_rhythm in CHORD_RHYTHMS and CHORD_RHYTHMS[chord_rhythm] is not None:
            rhythm = CHORD_RHYTHMS[chord_rhythm]
        elif chord_rhythm == "dense":
            rhythm = random.choice(rhythm_dense)
        else:
            rhythm = random.choice(rhythm_normal)

        for duration, is_rest in rhythm:
            if is_rest:
                track.append({"notes": [], "duration": duration, "velocity": 0})
            else:
                v = velocity + random.randint(-8, 8)
                track.append({"notes": chord, "duration": duration, "velocity": max(40, min(80, v))})
    return track

# ─── Bass generator ─────────────────────────────────────────────────────────────
def generate_bass(chords, bars=8, pattern="root_fifth", velocity_scale=1.0):
    bass = []
    patterns = BASS_PATTERNS.get(pattern, BASS_PATTERNS["root_fifth"])
    last_idx = None
    for bar in range(bars):
        chord = chords[bar % len(chords)]
        root  = chord[0] - 12
        fifth = chord[2] - 12 if len(chord) > 2 else root + 7
        third = chord[1] - 12 if len(chord) > 1 else root + 4
        available = [i for i in range(len(patterns)) if i != last_idx] or list(range(len(patterns)))
        idx = random.choice(available)
        last_idx = idx
        for step in patterns[idx]:
            n = {"root": root, "fifth": fifth, "third": third}.get(step["note"], 0)
            v = int(step["velocity"] * velocity_scale)
            bass.append({"note": n, "duration": step["duration"], "velocity": max(0, min(127, v))})
    return bass

# ─── Main entry point ──────────────────────────────────────────────────────────
def generate_music_plan(
    key:             str  = "C",
    mode:            str  = "minor",
    progression:     str  = "I-V-vi-IV",
    chord_voicing:   str  = "close",
    chord_min_interval: int = 0,
    bpm:             int  = 90,
    bars:            int  = 8,
    melody_rhythm:   str  = "modern_trap",
    melody_max_jump: int  = 3,
    melody_chord_prob: float = 0.55,
    melody_leap_prob:  float = 0.10,
    melody_octave_lo:  int  = 60,
    melody_octave_hi:  int  = 84,
    melody_motif_repeat: bool = True,
    melody_velocity:   int  = 82,
    melody_vel_var:    int  = 12,
    use_llm:         bool = True,
    chord_rhythm:    str  = "normal",
    chord_velocity:  int  = 58,
    bass_pattern:    str  = "root_fifth",
    bass_velocity_scale: float = 1.0,
    arp_enabled:     bool = False,
    arp_pattern:     str  = "up",
    arp_note_dur:    int  = EIGHTH,
    arp_velocity:    int  = 68,
):
    scale_notes = build_scale_full(key, mode, octaves=3, base_octave=3)
    chords, degrees = get_progression_chords(
        key, mode, progression, voicing=chord_voicing,
        min_interval=chord_min_interval
    )

    melody = generate_melody(
        scale_notes, chords, bars=bars, bpm=bpm, mode=mode,
        rhythm_style=melody_rhythm,
        max_jump=melody_max_jump,
        chord_prob=melody_chord_prob,
        leap_prob=melody_leap_prob,
        octave_range=(melody_octave_lo, melody_octave_hi),
        motif_repeat=melody_motif_repeat,
        velocity_base=melody_velocity,
        velocity_variance=melody_vel_var,
        use_llm=use_llm
    )

    arp = None
    if arp_enabled:
        arp = generate_arpeggio(chords, bars=bars,
                                pattern=arp_pattern,
                                note_duration=arp_note_dur,
                                velocity=arp_velocity)

    return {
        "key":         key,
        "mode":        mode,
        "bpm":         bpm,
        "degrees":     degrees,
        "scale":       scale_notes,
        "chords":      chords,
        "melody":      melody,
        "chord_track": generate_chords_track(chords, bars=bars,
                                             chord_rhythm=chord_rhythm,
                                             velocity=chord_velocity),
        "bass":        generate_bass(chords, bars=bars,
                                     pattern=bass_pattern,
                                     velocity_scale=bass_velocity_scale),
        "arp":         arp,
        "tpb":         TPB,
    }