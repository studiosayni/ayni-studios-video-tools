"""Model layers for asset-describe.
Listening layer: Gemini (audio in, JSON out) for music and sfx. Seeing layer: Claude for graphics.
Only the file bytes, its name and the fields to fill go to the model. No client names, projects or rights."""
import base64, json, mimetypes, os, sys, time

GEMINI_DEFAULT = os.environ.get("ASSET_DESCRIBE_GEMINI_MODEL", "gemini-3.8-flash")
CLAUDE_DEFAULT = os.environ.get("ASSET_DESCRIBE_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.environ.get("ASSET_DESCRIBE_CLAUDE_EFFORT", "high")

# Gemini list prices per MTok (cached 2026-09-10; check ai.google.dev/pricing). Flash tier, prompts under 200k tokens.
GEMINI_PRICE = {"input": 0.30, "output": 2.50, "audio_input": 1.00}
CLAUDE_PRICE = {"claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (2.0, 10.0)}

MUSIC_VOCAB = {"pacing": ["slow", "medium", "fast", "variable"], "energy": ["low", "medium", "high", "building"]}
SFX_VOCAB = {
    "kind": ["one-shot", "bed", "loop", "sequence"],
    "source_type": ["recorded", "designed", "library"],
    "category": ["ambience", "foley", "impact", "whoosh", "ui", "nature", "animals", "water", "vehicles", "crowd", "mechanical", "other"],
}
GFX_VOCAB = {"type": ["logo", "lower-third", "title", "transition", "overlay", "template", "icon", "map", "other"]}

MUSIC_SCHEMA = {
    "type": "object",
    "properties": {
        "genre": {"type": "array", "items": {"type": "string"}, "description": "one to three lowercase genre tags"},
        "mood": {"type": "array", "items": {"type": "string"}, "description": "two to five lowercase mood words"},
        "pacing": {"type": "string", "enum": MUSIC_VOCAB["pacing"]},
        "energy": {"type": "string", "enum": MUSIC_VOCAB["energy"]},
        "vocal": {"type": "boolean"},
        "language": {"type": "string", "nullable": True, "description": "ISO 639-1 code of sung language, null if instrumental"},
        "instrumentation": {"type": "array", "items": {"type": "string"}},
        "structure": {"type": "array", "items": {"type": "object", "properties": {
            "section": {"type": "string"}, "start_seconds": {"type": "number"}, "end_seconds": {"type": "number"}}, "required": ["section", "start_seconds", "end_seconds"]},
            "description": "sections such as intro, build, drop, verse, chorus, breakdown, outro with times"},
        "edit_points_seconds": {"type": "array", "items": {"type": "number"}, "description": "moments where a cut or transition would land naturally"},
        "bpm_estimate": {"type": "number", "nullable": True},
        "key_estimate": {"type": "string", "nullable": True},
        "description": {"type": "string", "description": "one or two sentences an editor would read to decide whether to audition it"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["genre", "mood", "pacing", "energy", "vocal", "instrumentation", "structure", "edit_points_seconds", "description", "tags"],
}

SFX_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": SFX_VOCAB["category"]},
        "subcategory": {"type": "string", "nullable": True},
        "kind": {"type": "string", "enum": SFX_VOCAB["kind"]},
        "source_type": {"type": "string", "enum": SFX_VOCAB["source_type"]},
        "description": {"type": "string", "description": "what is heard, plainly, one or two sentences"},
        "perspective": {"type": "string", "nullable": True, "description": "close, medium, distant, interior, exterior, or null"},
        "environment": {"type": "string", "nullable": True},
        "loopable": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["category", "kind", "source_type", "description", "loopable", "tags"],
}

GFX_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": {"type": "string", "enum": GFX_VOCAB["type"]},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "colors": {"type": "array", "items": {"type": "string"}, "description": "dominant colours as hex strings, up to five"},
        "text_present": {"type": "boolean"},
        "text_content": {"type": "array", "items": {"type": "string"}, "description": "any readable text, verbatim"},
        "safe_for_dark_background": {"type": "boolean"},
        "safe_for_light_background": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type", "title", "description", "colors", "text_present", "text_content", "safe_for_dark_background", "safe_for_light_background", "tags"],
}

MUSIC_PROMPT = """You are cataloguing a music track for a documentary edit library. Listen to the whole file and fill the schema.
Rules: lowercase tags; mood words an editor would search for (tense, warm, hopeful, melancholic, playful, epic); genre broad, not sub-sub-genres;
pacing is how fast it feels to cut to, energy is intensity over the track (use "building" when it rises); structure sections with times in seconds;
edit points are moments a cut would land well (downbeats after a build, section changes). Say only what you hear. Do not guess a title or artist."""

SFX_PROMPT = """You are cataloguing a sound effect for a documentary edit library. Listen to the whole file and fill the schema.
Rules: category from the list; kind is one-shot for single hits, bed for a continuous texture, loop if it loops cleanly, sequence for a series of events;
source_type recorded if it sounds like a real location recording, designed if processed or synthetic, library if it sounds like a generic stock effect;
description in plain words; lowercase tags an editor would search for. Say only what you hear."""

GFX_SYSTEM = """You catalogue graphics for a documentary studio's asset library. Describe exactly what is in the image or frames: type, readable text verbatim,
dominant colours as hex, and whether it would read cleanly on a dark and on a light background. Lowercase tags. No guesses about brand or client beyond visible text."""


def gemini_client():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key: return None
    from google import genai
    return genai.Client(api_key=key)


def resolve_gemini_model(client, wanted):
    """Use the model as given; if the API does not know it, pick the newest Flash model whose name contains the wanted version."""
    try:
        names = [m.name.split("/")[-1] for m in client.models.list()]
    except Exception as e:
        print(f"  could not list Gemini models ({type(e).__name__}); using {wanted}", file=sys.stderr); return wanted
    if wanted in names: return wanted
    ver = "".join(ch for ch in wanted if ch.isdigit() or ch == ".").strip(".")
    cands = [n for n in names if "flash" in n and ver and ver in n and "lite" not in n and "tts" not in n and "image" not in n and "live" not in n]
    if cands:
        pick = sorted(cands)[-1]; print(f"  Gemini model {wanted} not listed; using {pick}", file=sys.stderr); return pick
    raise SystemExit(f"Gemini model {wanted} not available to this key. Models with 'flash': {[n for n in names if 'flash' in n][:12]}")


def gemini_listen(client, model, path, schema, prompt, max_inline_mb=18):
    """One Gemini request with the audio attached and a JSON schema enforced. Returns (parsed, usage, wall, cost)."""
    from google.genai import types
    mime = mimetypes.guess_type(path)[0] or "audio/wav"
    if mime in ("audio/x-wav",): mime = "audio/wav"
    size_mb = os.path.getsize(path) / 1e6
    t0 = time.time()
    if size_mb <= max_inline_mb:
        part = types.Part.from_bytes(data=open(path, "rb").read(), mime_type=mime); uploaded = None
    else:
        uploaded = client.files.upload(file=path, config={"mime_type": mime})
        while getattr(uploaded, "state", None) and str(uploaded.state).endswith("PROCESSING"):
            time.sleep(2); uploaded = client.files.get(name=uploaded.name)
        part = uploaded
    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0.2)
    resp = None
    for attempt in range(6):
        try:
            resp = client.models.generate_content(model=model, contents=[part, prompt], config=cfg); break
        except Exception as e:
            code = getattr(e, "code", None) or (str(e)[:3] if str(e)[:3].isdigit() else None)
            if str(code) in ("503", "429", "500") and attempt < 5:
                wait = 5 * (2 ** attempt); print(f"  gemini {code}, retry in {wait}s", file=sys.stderr); time.sleep(wait); continue
            raise
    wall = time.time() - t0
    if uploaded is not None:
        try: client.files.delete(name=uploaded.name)
        except Exception: pass
    text = resp.text or ""
    if not text.strip(): raise RuntimeError("empty Gemini response")
    um = getattr(resp, "usage_metadata", None)
    usage = {"input_tokens": getattr(um, "prompt_token_count", 0) or 0, "output_tokens": getattr(um, "candidates_token_count", 0) or 0,
             "thinking_tokens": getattr(um, "thoughts_token_count", 0) or 0}
    cost = usage["input_tokens"] / 1e6 * GEMINI_PRICE["audio_input"] + (usage["output_tokens"] + usage["thinking_tokens"]) / 1e6 * GEMINI_PRICE["output"]
    return json.loads(text), usage, wall, round(cost, 5)


def anthropic_client():
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception as e:
        print(f"  Anthropic client unavailable ({type(e).__name__})", file=sys.stderr); return None


def claude_see(client, model, effort, images, file_name, kind_hint):
    """One Claude request with the image(s) attached and a JSON schema enforced. Returns (parsed, usage, wall, cost)."""
    content = []
    for p, label in images:
        mime = mimetypes.guess_type(p)[0] or "image/png"
        if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"): continue
        content.append({"type": "text", "text": label})
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(open(p, "rb").read()).decode()}})
    content.append({"type": "text", "text": f"File name: {file_name}. It is a {kind_hint}. Fill the schema."})
    t0 = time.time()
    with client.messages.stream(model=model, max_tokens=4000, system=GFX_SYSTEM, messages=[{"role": "user", "content": content}],
                                output_config={"effort": effort, "format": {"type": "json_schema", "schema": GFX_SCHEMA}}) as s:
        msg = s.get_final_message()
    wall = time.time() - t0
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if not text.strip(): raise RuntimeError(f"empty Claude response (stop_reason {msg.stop_reason})")
    u = msg.usage; inp, out = CLAUDE_PRICE.get(model, (4.0, 20.0))
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
    cost = u.input_tokens / 1e6 * inp + u.output_tokens / 1e6 * out
    return json.loads(text), usage, wall, round(cost, 5)
