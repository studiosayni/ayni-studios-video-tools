#!/usr/bin/env python3
"""Text-only refresh of a clip's transcript, audio description and speaker flags from the chosen audio source.
Used by sync.py --refresh-transcripts after a batch was described before its audio was synced. No frames involved."""
import json

PRICING = {"claude-sonnet-5": (2.0, 10.0), "claude-opus-5": (5.0, 25.0), "claude-haiku-4-5": (1.0, 5.0)}
SYSTEM = """You clean up footage transcripts for a video production company's media library. You receive raw whisper output for one clip (already in video time), the directors note, the clip's audio statistics, the existing visual description, and the list of people keys already in the clip's JSON. Use the note's spelling of names and places; do not replace a proper noun with a guess. You cannot hear the audio.

Return:
- transcript_segments: the real speech as timed segments in video time (seconds), corrected for obvious transcription errors using context, in its original language with correct orthography. Empty when there is no real speech. Whisper invents short phrases on silence or ambient noise and sometimes repeats one phrase in a loop; drop those. Runs are labelled with their audio source (device name and role); prefer the main source and use the others only where the main is unusable or missing. Set speaker to a people id when you can tell who is talking.
- transcript_language: ISO code of the speech, or null.
- source_used: the device name of the source the transcript relies on, or null when there is no real speech.
- audio_description: what an editor would hear, built from the transcript, the audio statistics and what the visual description implies. Say which parts are inferred.
- speakers: for each provided people id, whether that person's voice is heard, judged from the transcript and the visual description. Ids not provided must not be invented.
- notes: one sentence on anything uncertain."""
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"transcript_segments": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "properties": {"start": {"type": "number"}, "end": {"type": "number"}, "text": {"type": "string"}, "speaker": {"type": ["string", "null"]}},
                                                 "required": ["start", "end", "text", "speaker"]}},
                         "transcript_language": {"type": ["string", "null"]}, "source_used": {"type": ["string", "null"]}, "audio_description": {"type": "string"},
                         "speakers": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"id": {"type": "string"}, "voice_heard": {"type": "boolean"}}, "required": ["id", "voice_heard"]}},
                         "notes": {"type": "string"}},
          "required": ["transcript_segments", "transcript_language", "source_used", "audio_description", "speakers", "notes"]}

def cost_of(usage, model):
    pin, pout = PRICING.get(model, (None, None))
    if pin is None: return None
    return round(((usage.input_tokens or 0) * pin + (usage.cache_creation_input_tokens or 0) * pin * 1.25 + (usage.cache_read_input_tokens or 0) * pin * 0.1 + (usage.output_tokens or 0) * pout) / 1e6, 4)

def refresh_clip(client, model, effort, runs, source, stats_text, visual_description, people_keys, expected_langs, note_text=None):
    body = [f"Main audio source for this batch: {source}", "", "Directors note (context, names and places are spelled correctly here):", (note_text or "(none)").strip(), "", f"Expected languages: {', '.join(expected_langs) if expected_langs else 'not specified'}", "", "Audio statistics (camera track):", stats_text or "(none)", "", "Existing visual description:", visual_description or "(none)", "", "People ids in the JSON: " + (", ".join(people_keys) if people_keys else "(none)"), ""]
    for lab, text in runs: body += [f"[whisper run: {lab}]", text.strip() or "(empty)", ""]
    body.append("Return the transcript segments, language, source used, audio description, speaker flags and notes as JSON matching the schema.")
    # streamed: the SDK refuses a non-streaming request this large ("may take longer than 10 minutes")
    with client.messages.stream(model=model, max_tokens=32000, system=SYSTEM, messages=[{"role": "user", "content": "\n".join(body)}],
                                output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}}) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal": raise RuntimeError("the model declined the refresh request")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if not text.strip(): raise RuntimeError(f"empty response (stop_reason {msg.stop_reason}); raise max_tokens or lower effort")
    return json.loads(text), msg.usage, cost_of(msg.usage, model)
