import os
import requests
import time
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional

from app.music_engine import generate_music_plan, SCALES, PROGRESSIONS, BASS_PATTERNS, EIGHTH, SIXTEENTH, QUARTER
from app.midi_gen import create_midi

app = FastAPI()

GUI_PATH = os.path.join(os.path.dirname(__file__), "gui.html")

@app.get("/", response_class=HTMLResponse)
def index():
    with open(GUI_PATH, "r", encoding="utf-8") as f:
        return f.read()

# ─── Schema ────────────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    # Harmony
    key:                str   = "C"
    mode:               str   = "minor"
    progression:        str   = "I-V-vi-IV"
    chord_voicing:      str   = "close"
    chord_min_interval: int   = 0
    # Tempo & structure
    bpm:                int   = 90
    bars:               int   = 8
    # Melody
    melody_rhythm:      str   = "modern_trap"
    melody_max_jump:    int   = 3
    melody_chord_prob:  float = 0.55
    melody_leap_prob:   float = 0.10
    melody_octave_lo:   int   = 60
    melody_octave_hi:   int   = 84
    melody_motif_repeat: bool = True
    melody_velocity:    int   = 82
    melody_vel_var:     int   = 12
    use_llm:            bool  = True  # Новый флаг для гибрида
    # Chords
    chord_rhythm:       str   = "normal"
    chord_velocity:     int   = 58
    # Bass
    bass_pattern:       str   = "root_fifth"
    bass_velocity_scale: float = 1.0
    # Arpeggio
    arp_enabled:        bool  = False
    arp_pattern:        str   = "up"
    arp_note_dur:       int   = EIGHTH
    arp_velocity:       int   = 68

@app.post("/generate")
def generate(req: GenerateRequest):
    music = generate_music_plan(
        key=req.key, mode=req.mode,
        progression=req.progression,
        chord_voicing=req.chord_voicing,
        chord_min_interval=req.chord_min_interval,
        bpm=req.bpm, bars=req.bars,
        melody_rhythm=req.melody_rhythm,
        melody_max_jump=req.melody_max_jump,
        melody_chord_prob=req.melody_chord_prob,
        melody_leap_prob=req.melody_leap_prob,
        melody_octave_lo=req.melody_octave_lo,
        melody_octave_hi=req.melody_octave_hi,
        melody_motif_repeat=req.melody_motif_repeat,
        melody_velocity=req.melody_velocity,
        melody_vel_var=req.melody_vel_var,
        use_llm=req.use_llm,
        chord_rhythm=req.chord_rhythm,
        chord_velocity=req.chord_velocity,
        bass_pattern=req.bass_pattern,
        bass_velocity_scale=req.bass_velocity_scale,
        arp_enabled=req.arp_enabled,
        arp_pattern=req.arp_pattern,
        arp_note_dur=req.arp_note_dur,
        arp_velocity=req.arp_velocity,
    )

    tracks = {
        "melody":      music["melody"],
        "chord_track": music["chord_track"],
        "bass":        music["bass"],
    }
    if music.get("arp"):
        tracks["arp"] = music["arp"]

    path = create_midi(
        melody=music["melody"],
        chord_track=music["chord_track"],
        bass=music["bass"],
        arp=music.get("arp"),
        tpb=music["tpb"],
        bpm=music["bpm"],
    )

    return {
        "file": path,
        "music": {
            "key":      music["key"],
            "mode":     music["mode"],
            "bpm":      music["bpm"],
            "degrees":  music["degrees"],
            "tracks":   tracks,
            "tpb":      music["tpb"],
        }
    }

@app.get("/download")
def download_midi(path: str):
    abs_path = os.path.abspath(path)
    output_dir = os.path.abspath("midi_output")
    if not abs_path.startswith(output_dir):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    filename = os.path.basename(abs_path)
    return FileResponse(
        abs_path,
        media_type="audio/midi",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

@app.get("/llm_status")
def llm_status():
    """Проверяет доступность Ollama."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        models = [m["name"] for m in r.json().get("models", [])]
        # Ищем llama3 или любую другую модель
        llm_model = next((m for m in models if "llama" in m.lower()), None)
        if llm_model is None and models:
            llm_model = models[0]
        return {"available": bool(models), "model": llm_model, "all_models": models}
    except Exception as e:
        return {"available": False, "model": None, "error": str(e)}

@app.get("/options")
def options():
    """Return all available parameter options for the GUI."""
    return {
        "keys": ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "Bb", "B"],
        "modes": list(SCALES.keys()),
        "progressions": list(PROGRESSIONS.keys()),
        "chord_voicings": ["close", "open", "seventh", "open_seventh"],
        "melody_rhythms": ["modern_trap", "normal", "dense", "sparse", "mixed", "syncopated", "dotted",
                           "whole", "half", "quarter", "eighth"],
        "chord_rhythms": ["whole", "half", "half_rest", "quarter", "offbeat",
                          "sparse", "normal", "dense"],
        "bass_patterns": list(BASS_PATTERNS.keys()),
        "arp_patterns": ["up", "down", "up_down", "random", "outside_in"],
    }