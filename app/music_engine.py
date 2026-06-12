"""
music_engine.py — REMI + Ollama гибрид
REMI = Relative MIDI Representation (Bar, Position, Pitch, Duration, Velocity токены)
LLM генерирует реальную мелодию в REMI токенах -> мы декодируем обратно в ноты+ритм
Если Ollama недоступна -> алгоритмический fallback (как раньше)
"""
import random
import json
import re
import requests

# ─── Note / Scale tables ───────────────────────────────────────────────────────
NOTE_MAP = {
    "C": 60, "C#": 61, "Db": 61, "D": 62, "D#": 63, "Eb": 63,
    "E": 64, "F": 65, "F#": 66, "Gb": 66, "G": 67,
    "G#": 68, "Ab": 68, "A": 69, "A#": 70, "Bb": 70, "B": 71,
}

NOTE_NAMES = {v: k for k, v in NOTE_MAP.items()}

SCALES = {
    "major":          [0, 2, 4, 5, 7, 9, 11],
    "minor":          [0, 2, 3, 5, 7, 8, 10],
    "dorian":         [0, 2, 3, 5, 7, 9, 10],
    "phrygian":       [0, 1, 3, 5, 7, 8, 10],
    "lydian":         [0, 2, 4, 6, 7, 9, 11],
    "mixolydian":     [0, 2, 4, 5, 7, 9, 10],
    "locrian":        [0, 1, 3, 5, 6, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
    "blues":          [0, 3, 5, 6, 7, 10],
    "whole_tone":     [0, 2, 4, 6, 8, 10],
}

TPB = 480
WHOLE     = TPB * 4
HALF      = TPB * 2
QUARTER   = TPB
EIGHTH    = TPB // 2
SIXTEENTH = TPB // 4
DOTTED_Q  = int(TPB * 1.5)
DOTTED_E  = int(TPB * 0.75)
T8        = TPB // 3        # triole eighth (trap triplet)

# ─── Chord progressions ────────────────────────────────────────────────────────
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

# ─── REMI tokenizer ────────────────────────────────────────────────────────────
# REMI токены: Bar_N, Pos_N (1/16 позиция внутри такта 0-15),
# Pitch_NN (MIDI нота), Dur_N (в 1/16 единицах 1-8), Vel_N (low/mid/high)

DUR_TICKS = {1: SIXTEENTH, 2: EIGHTH, 3: T8*2, 4: QUARTER,
             6: DOTTED_Q, 8: HALF, 12: DOTTED_Q+HALF, 16: WHOLE}
TICKS_TO_DUR = {v: k for k, v in DUR_TICKS.items()}

def _snap_to_dur(ticks):
    """Найти ближайшее допустимое значение длительности"""
    best = min(DUR_TICKS.values(), key=lambda x: abs(x - ticks))
    return TICKS_TO_DUR[best]

def melody_to_remi(melody_events, tpb=TPB):
    """Конвертирует список событий мелодии в REMI токены (строка)"""
    tokens = []
    tick = 0
    bar_ticks = tpb * 4
    for ev in melody_events:
        note = ev.get("note", 0)
        dur_ticks = ev.get("duration", EIGHTH)
        vel = ev.get("velocity", 80)

        if note == 0 or vel == 0:
            tick += dur_ticks
            continue

        bar_num = tick // bar_ticks
        pos_16  = (tick % bar_ticks) // SIXTEENTH
        dur_16  = _snap_to_dur(dur_ticks)
        vel_cls = "low" if vel < 60 else ("high" if vel > 90 else "mid")

        tokens.append(f"Bar_{bar_num}")
        tokens.append(f"Pos_{pos_16}")
        tokens.append(f"Pitch_{note}")
        tokens.append(f"Dur_{dur_16}")
        tokens.append(f"Vel_{vel_cls}")
        tick += dur_ticks

    return " ".join(tokens)

def remi_to_melody(remi_str, total_bars=8, tpb=TPB, lo=60, hi=84):
    """
    Декодирует REMI строку обратно в список событий мелодии.
    Заполняет пробелы (позиции без нот) тишиной.
    """
    bar_ticks = tpb * 4
    tokens = remi_str.split()

    events_by_tick = {}  # tick -> (note, dur_ticks, vel)
    cur_bar = 0
    cur_pos = 0
    cur_pitch = None
    cur_dur = 2
    cur_vel = "mid"

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("Bar_"):
            try: cur_bar = int(t.split("_")[1])
            except: pass
        elif t.startswith("Pos_"):
            try: cur_pos = int(t.split("_")[1])
            except: pass
        elif t.startswith("Pitch_"):
            try: cur_pitch = int(t.split("_")[1])
            except: pass
        elif t.startswith("Dur_"):
            try: cur_dur = int(t.split("_")[1])
            except: pass
        elif t.startswith("Vel_"):
            cur_vel = t.split("_")[1]
            # Вот здесь — у нас есть полный event, записываем
            if cur_pitch is not None and 0 < cur_pitch <= 127:
                pitch = max(lo, min(hi, cur_pitch))
                tick = cur_bar * bar_ticks + cur_pos * SIXTEENTH
                dur_ticks = DUR_TICKS.get(cur_dur, EIGHTH)
                vel_val = {"low": 55, "mid": 78, "high": 100}.get(cur_vel, 78)
                vel_val += random.randint(-8, 8)
                vel_val = max(40, min(110, vel_val))
                events_by_tick[tick] = (pitch, dur_ticks, vel_val)
            cur_pitch = None
        i += 1

    if not events_by_tick:
        return None  # LLM не вернула ничего полезного

    # Собираем финальный список с паузами между нотами
    melody = []
    total_ticks = total_bars * bar_ticks
    sorted_ticks = sorted(events_by_tick.keys())

    # Фильтруем события вне диапазона
    sorted_ticks = [t for t in sorted_ticks if t < total_ticks]
    if not sorted_ticks:
        return None

    cur_tick = 0
    for tick in sorted_ticks:
        pitch, dur_ticks, vel = events_by_tick[tick]
        # Пауза если нужна
        if tick > cur_tick:
            melody.append({"note": 0, "duration": tick - cur_tick, "velocity": 0})
        # Не выходим за пределы
        remaining = total_ticks - tick
        actual_dur = min(dur_ticks, remaining)
        melody.append({"note": pitch, "duration": actual_dur, "velocity": vel})
        cur_tick = tick + actual_dur

    # Хвостовая пауза
    if cur_tick < total_ticks:
        melody.append({"note": 0, "duration": total_ticks - cur_tick, "velocity": 0})

    return melody

# ─── Ollama LLM: генерация мелодии в REMI токенах ─────────────────────────────

def _note_name(midi):
    names = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
    return f"{names[midi % 12]}{midi // 12 - 1}"


def _detect_ollama_model():
    """Определяет какая модель доступна в Ollama. Предпочитает llama3/mistral/gemma."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        # Приоритет: llama3 > mistral > gemma > любая
        for pref in ["llama3", "mistral", "gemma", "llama2", "phi"]:
            for m in models:
                if pref in m.lower():
                    return m
        return models[0] if models else None
    except Exception:
        return None

def get_llm_melody_remi(key, mode, bpm, bars, scale_notes, chords, degrees,
                        lo=60, hi=84, model="llama3"):
    """
    Просим LLM сгенерировать мелодию в REMI токенах.
    Даём ей: тональность, лад, BPM, аккорды, диапазон, примеры токенов.
    """
    # Автодетект модели
    detected = _detect_ollama_model()
    if detected is None:
        return None
    model = detected
    print(f"[LLM] Используем модель: {model}")

    # Формируем список нот гаммы для промпта (только в диапазоне lo..hi)
    scale_in_range = [n for n in scale_notes if lo <= n <= hi]
    scale_names = [_note_name(n) for n in scale_in_range[:12]]

    # Строим описание прогрессии аккордов
    chord_desc = []
    for i, ch in enumerate(chords):
        chord_names = [_note_name(n) for n in ch]
        chord_desc.append(f"bar {i % len(chords)}: {', '.join(chord_names)}")
    chord_str = " | ".join(chord_desc)

    # Пример правильных токенов для ориентира
    example = (
        "Bar_0 Pos_0 Pitch_67 Dur_4 Vel_high "
        "Bar_0 Pos_4 Pitch_65 Dur_2 Vel_mid "
        "Bar_0 Pos_6 Pitch_63 Dur_2 Vel_mid "
        "Bar_0 Pos_8 Pitch_65 Dur_4 Vel_high "
        "Bar_1 Pos_0 Pitch_67 Dur_2 Vel_mid "
        "Bar_1 Pos_3 Pitch_70 Dur_2 Vel_high "
        "Bar_1 Pos_6 Pitch_67 Dur_4 Vel_mid"
    )

    prompt = f"""You are a professional trap music producer (think Juice WRLD, Rod Wave, Polo G style).
Generate a {bars}-bar lead melody for a trap beat.

SPECS:
- Key: {key} {mode}
- BPM: {bpm}
- Scale notes available (MIDI numbers): {', '.join(str(n) for n in scale_in_range)}
- Scale note names: {', '.join(scale_names)}
- Chord progression: {chord_str}
- Melody pitch range: MIDI {lo} to {hi}

REMI TOKEN FORMAT (output ONLY these tokens, nothing else):
- Bar_N  — bar number 0-{bars-1}
- Pos_N  — 1/16th position inside bar 0-15
- Pitch_N — MIDI note number ({lo}-{hi}), MUST be from scale: {', '.join(str(n) for n in scale_in_range)}
- Dur_N  — duration in 1/16ths: 1=16th, 2=8th, 4=quarter, 6=dotted-quarter, 8=half
- Vel_N  — velocity: low, mid, high

RULES (CRITICAL):
1. Only use Pitch values from this exact list: {', '.join(str(n) for n in scale_in_range)}
2. Every note needs all 5 tokens: Bar Pos Pitch Dur Vel
3. Leave gaps (no token) for rests — do NOT write rest tokens
4. Use triplet feel: place notes at Pos 0,3,6,9,12 for trap triplets
5. Use repetition — repeat short motifs every 2 bars
6. End notes on chord tones (Pitch values that match bar's chord)
7. Generate {bars} bars minimum, 6-12 notes per bar

EXAMPLE of correct output:
{example}

Now generate the full {bars}-bar melody. Output ONLY the REMI tokens:"""

    try:
        resp = requests.post("http://localhost:11434/api/generate", json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.75,
                "top_p": 0.92,
                "repeat_penalty": 1.1,
                "num_predict": 800,
            }
        }, timeout=30)

        if resp.status_code != 200:
            print(f"[LLM] HTTP {resp.status_code}")
            return None

        raw = resp.json().get("response", "")
        # Чистим: убираем лишний текст, оставляем только REMI токены
        # LLM иногда добавляет пояснения до/после — вырезаем
        remi_tokens = re.findall(
            r'\b(?:Bar_\d+|Pos_\d+|Pitch_\d+|Dur_\d+|Vel_(?:low|mid|high))\b',
            raw
        )
        if len(remi_tokens) < 10:
            print(f"[LLM] Слишком мало токенов: {len(remi_tokens)}")
            return None

        remi_str = " ".join(remi_tokens)
        print(f"[LLM] Получено {len(remi_tokens)} REMI токенов")

        # Декодируем в мелодию
        melody = remi_to_melody(remi_str, total_bars=bars, lo=lo, hi=hi)
        if melody and len(melody) > 0:
            print(f"[LLM] Успешно декодировано {len(melody)} событий")
            return melody
        else:
            print("[LLM] Декодирование дало пустую мелодию")
            return None

    except requests.exceptions.ConnectionError:
        print("[LLM] Ollama не запущена, используем алгоритмику")
        return None
    except Exception as e:
        print(f"[LLM] Ошибка: {e}")
        return None

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
        atoms = [EIGHTH, DOTTED_E, SIXTEENTH, QUARTER, T8]
        weights = [0.25, 0.40, 0.15, 0.10, 0.10]
        rest_prob = 0.28
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

# ─── Algorithmic melody (fallback) ─────────────────────────────────────────────
def generate_melody_algorithmic(scale_notes, chords, bars=8, bpm=90, mode="minor",
                                rhythm_style="modern_trap",
                                max_jump=3,
                                chord_prob=0.55,
                                leap_prob=0.10,
                                octave_range=(60, 84),
                                motif_repeat=True,
                                velocity_base=82,
                                velocity_variance=12):
    melody = []
    lo, hi = octave_range
    melody_scale = [n for n in scale_notes if lo <= n <= hi] or scale_notes
    if not melody_scale:
        return []

    root_candidates = [melody_scale[0], melody_scale[2]] if len(melody_scale) > 2 else [melody_scale[0]]
    current = random.choice(root_candidates)
    recent_notes = []
    motif_notes = []
    motif_captured = False

    for bar in range(bars):
        chord = chords[bar % len(chords)]
        rhythm = _make_rhythm(rhythm_style)

        bar_in_loop = bar % 4
        replay = (motif_repeat and motif_captured and bar_in_loop in [1, 2])
        motif_pos = 0

        for i, (duration, is_rest) in enumerate(rhythm):
            if is_rest:
                melody.append({"note": 0, "duration": duration, "velocity": 0})
                continue

            if replay and motif_pos < len(motif_notes):
                orig_root = chords[0][0]
                curr_root = chord[0]
                note = motif_notes[motif_pos] + (curr_root - orig_root)
                while note < lo: note += 12
                while note > hi: note -= 12
                motif_pos += 1
                current = note
            else:
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

            if bar == 0 and not motif_captured:
                motif_notes.append(note)
                if len(motif_notes) >= random.randint(3, 5):
                    motif_captured = True

            recent_notes.append(note)
            if len(recent_notes) > 8:
                recent_notes.pop(0)

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

# ─── Main entry point ──────────────────────────────────────────────────────────
def generate_music_plan(
    key="C", mode="minor", progression="I-V-vi-IV",
    chord_voicing="close", chord_min_interval=0,
    bpm=90, bars=8,
    melody_rhythm="modern_trap",
    melody_max_jump=3,
    melody_chord_prob=0.55,
    melody_leap_prob=0.10,
    melody_octave_lo=60,
    melody_octave_hi=84,
    melody_motif_repeat=True,
    melody_velocity=82,
    melody_vel_var=12,
    use_llm=True,
    chord_rhythm="normal",
    chord_velocity=58,
    bass_pattern="root_fifth",
    bass_velocity_scale=1.0,
    arp_enabled=False,
    arp_pattern="up",
    arp_note_dur=EIGHTH,
    arp_velocity=68,
):
    scale_notes = build_scale_full(key, mode, octaves=3, base_octave=3)
    chords, degrees = get_progression_chords(
        key, mode, progression, voicing=chord_voicing,
        min_interval=chord_min_interval
    )

    melody = None

    # ── REMI + LLM гибрид ──
    if use_llm:
        melody = get_llm_melody_remi(
            key=key, mode=mode, bpm=bpm, bars=bars,
            scale_notes=scale_notes, chords=chords, degrees=degrees,
            lo=melody_octave_lo, hi=melody_octave_hi,
        )
        if melody:
            print("[LLM] Используем LLM мелодию")
        else:
            print("[LLM] Fallback на алгоритмику")

    # ── Алгоритмический fallback ──
    if melody is None:
        melody = generate_melody_algorithmic(
            scale_notes, chords, bars=bars, bpm=bpm, mode=mode,
            rhythm_style=melody_rhythm,
            max_jump=melody_max_jump,
            chord_prob=melody_chord_prob,
            leap_prob=melody_leap_prob,
            octave_range=(melody_octave_lo, melody_octave_hi),
            motif_repeat=melody_motif_repeat,
            velocity_base=melody_velocity,
            velocity_variance=melody_vel_var,
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
