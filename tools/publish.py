#!/usr/bin/env python3
"""Copy the text records from the media drive into this repo clone, mirroring the folder tree. Media never enters git.

Copied: clip and recording JSONs, directors_note.md, sources.json, sync_map.json, sync_timeline.fcpxml, people/people.json, the client's
client_note.md and people.json, library index.json. Skipped: every media file, stills, outputs/, _quarantine/, _index/,
the people sheet, dot files. Files are copied only when new or changed. Nothing is ever deleted from the repo and
nothing is written to the drive.

usage:
  publish.py /Volumes/media-root --repo /path/to/private-catalogue                                  dry run for the whole drive
  publish.py /Volumes/media-root --repo /path/to/private-catalogue --client example-client --apply        copy one client's records, then commit yourself
  publish.py /Volumes/media-root --repo /path/to/private-catalogue --batch media/example-client/25.03.25.15-fair-care --pr
      copy one batch (plus its client-level files), commit it on a branch named after the batch, push, open the PR,
      print the link. The commit message and PR carry who ran it, the clip count and the cost from the run logs.
      Refuses to run when the clone has uncommitted changes. Several --batch values make one PR.
"""
import argparse, datetime, filecmp, glob, json, os, shutil, subprocess, sys

TOPS = ("media", "stock", "music", "sfx", "gfx", "exports")
SKIP_DIRS = {"outputs", "_quarantine", "_index", "_describe_work"}
KEEP_EXT = {".json", ".md", ".fcpxml"}
LIB = {"music": "libraries/music_library", "sfx": "libraries/sfx", "gfx": "libraries/gfx", "stock": "libraries/stock_footage"}

def repo_rel(rel):
    top = rel.split(os.sep)[0]
    return LIB.get(top, top) + rel[len(top):]

def wanted(f):
    return not f.startswith(".") and os.path.splitext(f)[1].lower() in KEEP_EXT and not f.endswith((".run.json", ".dryrun.json")) and f != "_people_sheet.html"

def copy_tree(drive, repo, base, apply, stats):
    for d, ds, fs in os.walk(base):
        ds[:] = [x for x in ds if not x.startswith(".") and x not in SKIP_DIRS]
        for f in fs:
            if not wanted(f): continue
            copy_one(drive, repo, os.path.join(d, f), apply, stats)

def copy_one(drive, repo, src, apply, stats):
    if not os.path.isfile(src): return
    dst = os.path.join(repo, repo_rel(os.path.relpath(src, drive)))
    if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False): stats["same"] += 1; return
    stats["changed" if os.path.exists(dst) else "new"] += 1; stats["files"].append(os.path.relpath(dst, repo))
    if apply: os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(src, dst)

def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True)
    if check and r.returncode: sys.exit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()

def batch_summary(batch):
    """Clip count, described count and cost from the batch's run logs on the drive."""
    clips = [f for f in os.listdir(batch) if os.path.splitext(f)[1].lower() in (".mp4", ".mov", ".m4v", ".avi", ".mxf", ".mkv") and not f.startswith(".")]
    jsons = [f for f in clips if os.path.exists(os.path.join(batch, os.path.splitext(f)[0] + ".json"))]
    cost = 0.0
    for p in glob.glob(os.path.join(batch, "outputs", "describe_runs", "*.run.json")):
        try: cost += float(json.load(open(p)).get("total_cost_usd") or 0)
        except Exception: pass
    linked = os.path.exists(os.path.join(batch, "people", "people.json")); synced = os.path.exists(os.path.join(batch, "sync_map.json"))
    return len(clips), len(jsons), round(cost, 2), linked, synced

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("drive"); ap.add_argument("--repo", required=True, help="Separate private catalogue clone; do not publish records to this public toolkit")
    ap.add_argument("--client"); ap.add_argument("--batch", action="append", help="batch folder, relative to the drive or absolute; repeatable")
    ap.add_argument("--apply", action="store_true", help="copy files (no git)"); ap.add_argument("--pr", action="store_true", help="copy, commit on a branch, push, open a PR")
    a = ap.parse_args(); drive = os.path.abspath(a.drive); repo = os.path.abspath(a.repo); apply = a.apply or a.pr
    if not os.path.isdir(os.path.join(repo, ".git")): sys.exit(f"{repo} is not a git clone")
    stats = {"new": 0, "changed": 0, "same": 0, "files": []}
    if a.pr:
        if not a.batch: sys.exit("--pr needs at least one --batch")
        if git(repo, "status", "--porcelain"): sys.exit("the clone has uncommitted changes; commit or stash them first so the PR holds only this batch")
        if git(repo, "config", "core.hooksPath", check=False) != "tools/githooks": sys.exit("commit guard not installed: run tools/setup_repo.sh once")
        git(repo, "checkout", "-q", "main"); git(repo, "pull", "-q", "--ff-only")
    batches = []
    if a.batch:
        for b in a.batch:
            p = b if os.path.isabs(b) else os.path.join(drive, b); p = os.path.abspath(p)
            if not os.path.isdir(p) or not p.startswith(drive): sys.exit(f"not a batch folder on the drive: {b}")
            batches.append(p)
        for b in batches:
            copy_tree(drive, repo, b, apply, stats)
            parts = os.path.relpath(b, drive).split(os.sep)
            if parts[0] == "media" and len(parts) > 2:      # the client's shared files travel with the batch
                cdir = os.path.join(drive, parts[0], parts[1])
                for f in ("client_note.md", "people.json"): copy_one(drive, repo, os.path.join(cdir, f), apply, stats)
    else:
        for top in TOPS:
            base = os.path.join(drive, top)
            if a.client: base = os.path.join(base, a.client)
            if os.path.isdir(base): copy_tree(drive, repo, base, apply, stats)
    print(f"{'copied' if apply else 'would copy'}: {stats['new']} new, {stats['changed']} changed; {stats['same']} already up to date")
    if not apply: print("dry run. Add --apply to copy, or --batch ... --pr to publish a batch as a pull request."); return
    if not a.pr: print("copied. Review with git status and commit."); return
    if not stats["files"]: print("nothing new to publish"); return
    who = git(repo, "config", "user.name") or os.environ.get("USER", "unknown")
    names = [os.path.basename(b) for b in batches]
    rel0 = os.path.relpath(batches[0], drive).split(os.sep); client = rel0[1] if len(rel0) > 2 else ""
    slug = (client + "-" + names[0] if client else names[0]) + ("" if len(names) == 1 else f"-and-{len(names) - 1}-more")
    branch = "describe/" + "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in slug)
    lines = []
    for b in batches:
        clips, jsons, cost, linked, synced = batch_summary(b)
        lines.append(f"- `{os.path.relpath(b, drive)}`: {jsons} of {clips} clips described, ${cost:.2f}" + (", synced" if synced else "") + (", people linked" if linked else ""))
    title = f"Describe {client + ': ' if client else ''}{', '.join(names)}"
    body = (f"Published by {who} on {datetime.date.today().isoformat()} with tools/publish.py.\n\n" + "\n".join(lines) +
            f"\n\n{len(stats['files'])} file(s): JSONs, notes, source and sync maps, people registry. No media.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)")
    if git(repo, "rev-parse", "--verify", "--quiet", branch, check=False): sys.exit(f"branch {branch} already exists; merge or delete it first")
    git(repo, "checkout", "-q", "-b", branch)
    git(repo, "add", "--", *stats["files"])
    r = subprocess.run(["git", "-C", repo, "commit", "-q", "-m", title + "\n\n" + "\n".join(lines) + f"\n\nPublished by {who}."], capture_output=True, text=True)
    if r.returncode: git(repo, "checkout", "-q", "main"); sys.exit("commit refused:\n" + r.stdout + r.stderr)
    git(repo, "push", "-q", "-u", "origin", branch)
    r = subprocess.run(["gh", "pr", "create", "--title", title, "--body", body, "--head", branch], cwd=repo, capture_output=True, text=True)
    git(repo, "checkout", "-q", "main")
    if r.returncode: sys.exit("push succeeded but gh pr create failed (is gh logged in?):\n" + r.stderr)
    print(r.stdout.strip().splitlines()[-1])

if __name__ == "__main__":
    main()
