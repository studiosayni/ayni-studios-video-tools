"""Shared rules for the media-root tools: naming pattern, what counts as a batch, where things may sit."""
import os, re

BATCH = re.compile(r"^\d\d\.\d\d\.\d\d\.\d\d-[a-z0-9]+(?:-[a-z0-9]+)*$")
DATED = re.compile(r"^\d\d\.\d\d\.\d\d")
CLIENT = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mxf", ".avi", ".mkv"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".aif", ".aiff", ".flac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".svg"}
ALLOWED_AT_BATCH = {"directors_note.md", "sources.json", "sync_map.json"}     # besides videos and their JSONs
# every batch carries the same skeleton; outputs/ appears when a skill first writes to it
SKELETON = ("audio", "images", "people", "transcripts")
BATCH_SUBDIRS = set(SKELETON) | {"outputs"}
SUBTITLE_EXT = {".srt", ".itt", ".vtt"}
# Agreed exceptions: shot before the convention. Structure checks are skipped, file-level checks still run.
EXCEPTION_CLIENTS = set()

def visible(names): return sorted(n for n in names if not n.startswith("."))

def clients(media):
    return [c for c in visible(os.listdir(media)) if os.path.isdir(os.path.join(media, c))]

def batches(client_dir):
    """Dated folders under a client, including a nested media/ directory when present."""
    out = []
    for d in visible(os.listdir(client_dir)):
        p = os.path.join(client_dir, d)
        if not os.path.isdir(p): continue
        if DATED.match(d): out.append(p)
        elif d == "media": out += batches(p)
    return out
