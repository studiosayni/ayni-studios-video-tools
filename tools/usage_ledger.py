#!/usr/bin/env python3
"""Subscription usage ledger for describing the library in Claude Code mode.

Every batch described on a Claude subscription gets one file, usage/describe/<client>__<batch>.json, with the tokens each
subagent used per clip and stage (describe, verify, people-link), clip durations and sheet counts, wall time, and the
plan's weekly usage percentage before and after when it was read. One file per batch keeps the two people describing
from ever editing the same file. `summary` rolls every file up into per-clip averages and a projection for the clips
still undescribed on the drive.

The orchestrator (Claude Code) writes <batch>/outputs/describe_runs/agent_tokens.tsv while the agents finish, one line per
agent from its completion notice:   <clip or _link>  <describe|verify|link_group|link_confirm>  <subagent_tokens>  <duration_ms>

usage:
  usage_ledger.py record <batch-folder> [--who NAME] [--plan PLAN] [--effort LEVEL] [--transcribe on|off] [--wall-seconds N]
                         [--weekly-before PCT --weekly-after PCT] [--five-hour-before PCT --five-hour-after PCT] [--notes TEXT]
  usage_ledger.py summary [--drive /Volumes/media-root]
"""
import argparse, datetime, glob, json, os, statistics, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "usage", "describe")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import medialib as ml

def git_user():
    try: return subprocess.run(["git", "-C", REPO, "config", "user.name"], capture_output=True, text=True).stdout.strip() or None
    except Exception: return None

def batch_ids(batch):
    parts = os.path.abspath(batch).split(os.sep)
    i = parts.index("media") if "media" in parts else len(parts) - 2
    return parts[i + 1], parts[-1]

def record(a):
    batch = os.path.abspath(a.batch); client, name = batch_ids(batch)
    tsv = a.tokens or os.path.join(batch, "outputs", "describe_runs", "agent_tokens.tsv")
    if not os.path.exists(tsv): sys.exit(f"no agent token log at {tsv}")
    agents = []
    for line in open(tsv, encoding="utf-8"):
        f = line.split()
        if len(f) >= 3 and f[2].isdigit(): agents.append({"clip": f[0], "stage": f[1], "tokens": int(f[2]), "ms": int(f[3]) if len(f) > 3 and f[3].isdigit() else None})
    clips = {}
    for p in sorted(glob.glob(os.path.join(batch, "*.json"))):
        try: j = json.load(open(p, encoding="utf-8"))
        except Exception: continue
        if not isinstance(j, dict) or "camera_metadata" not in j: continue
        stem = os.path.splitext(j["file_name"])[0]
        run = os.path.join(batch, "outputs", "describe_runs", f"{stem}.run.json")
        sheets = None
        if os.path.exists(run):
            try: sheets = json.load(open(run))["manifest"].get("images")
            except Exception: pass
        clips[stem] = {"clip": stem, "duration_seconds": j["camera_metadata"].get("duration_seconds"), "sheets": sheets,
                       "model": (j.get("generated_by") or {}).get("model"), "describe_tokens": 0, "verify_tokens": 0}
    link = {"group_tokens": 0, "confirm_tokens": 0, "confirm_sheets": 0}
    for ag in agents:
        if ag["stage"] in ("describe", "verify") and ag["clip"] in clips: clips[ag["clip"]][f"{ag['stage']}_tokens"] += ag["tokens"]
        elif ag["stage"] == "link_group": link["group_tokens"] += ag["tokens"]
        elif ag["stage"] == "link_confirm": link["confirm_tokens"] += ag["tokens"]; link["confirm_sheets"] += 1
    rows = list(clips.values())
    d = sum(r["describe_tokens"] for r in rows); v = sum(r["verify_tokens"] for r in rows); lk = link["group_tokens"] + link["confirm_tokens"]
    out = {"client": client, "batch": name, "recorded_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "who": a.who or git_user(), "plan": a.plan, "effort": a.effort, "mode": "claude-code", "transcribe": a.transcribe,
           "clips": len(rows), "footage_seconds": round(sum(r["duration_seconds"] or 0 for r in rows), 1),
           "wall_seconds": a.wall_seconds, "tokens": {"describe": d, "verify": v, "people_link": lk, "total": d + v + lk},
           "tokens_per_clip": round((d + v + lk) / len(rows)) if rows else None,
           "plan_usage": {"weekly_before": a.weekly_before, "weekly_after": a.weekly_after, "five_hour_before": a.five_hour_before, "five_hour_after": a.five_hour_after},
           "people_link": link, "notes": a.notes, "per_clip": rows}
    os.makedirs(LEDGER, exist_ok=True)
    path = os.path.join(LEDGER, f"{client}__{name}.json")
    json.dump(out, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"recorded {path}: {len(rows)} clips, {d + v + lk:,} tokens ({out['tokens_per_clip']:,} per clip)")

def undescribed(drive):
    media = os.path.join(drive, "media"); todo = 0; done = 0
    for c in ml.clients(media):
        for b in ml.batches(os.path.join(media, c)):
            for f in ml.visible(os.listdir(b)):
                if os.path.splitext(f)[1].lower() in ml.VIDEO_EXT:
                    if os.path.exists(os.path.join(b, os.path.splitext(f)[0] + ".json")): done += 1
                    else: todo += 1
    return done, todo

def summary(a):
    files = sorted(glob.glob(os.path.join(LEDGER, "*.json")))
    if not files: sys.exit("no ledger files yet")
    L = [json.load(open(p, encoding="utf-8")) for p in files]
    print(f"{'batch':52s} {'who':8s} {'tr':3s} {'clips':>5s} {'footage':>8s} {'tokens':>11s} {'per clip':>9s} {'weekly %':>9s}")
    for x in L:
        pu = x.get("plan_usage") or {}; wk = (pu.get("weekly_after") - pu.get("weekly_before")) if pu.get("weekly_after") is not None and pu.get("weekly_before") is not None else None
        print(f"{x['client'][:18] + '/' + x['batch']:52.52s} {str(x.get('who') or '-')[:8]:8s} {str(x.get('transcribe') or '-')[:3]:3s} {x['clips']:5d} {x['footage_seconds'] / 60:7.1f}m {x['tokens']['total']:11,d} {x['tokens_per_clip']:9,d} {('%.1f' % wk) if wk is not None else '-':>9s}")
    clips = sum(x["clips"] for x in L); tok = sum(x["tokens"]["total"] for x in L); foot = sum(x["footage_seconds"] for x in L)
    dv = [r["describe_tokens"] + r["verify_tokens"] for x in L for r in x["per_clip"]]
    print(f"\nall batches: {clips} clips, {foot / 60:.1f} min of footage, {tok:,} tokens")
    print(f"per clip (describe + verify): mean {statistics.mean(dv):,.0f}, median {statistics.median(dv):,.0f}, range {min(dv):,} to {max(dv):,}")
    print(f"people-link: {sum(x['tokens']['people_link'] for x in L):,} tokens across {sum(1 for x in L if x['tokens']['people_link'])} batch(es)")
    measured = [(x["plan_usage"]["weekly_after"] - x["plan_usage"]["weekly_before"], x["clips"]) for x in L
                if (x.get("plan_usage") or {}).get("weekly_before") is not None and (x.get("plan_usage") or {}).get("weekly_after") is not None]
    pct_per_clip = (sum(p for p, _ in measured) / sum(c for _, c in measured)) if measured and sum(c for _, c in measured) else None
    if pct_per_clip: print(f"weekly limit per clip, measured on {sum(c for _, c in measured)} clips: {pct_per_clip:.3f}%  (about {100 / pct_per_clip:,.0f} clips per week per account)")
    if a.drive and os.path.isdir(os.path.join(a.drive, "media")):
        done, todo = undescribed(a.drive)
        per = tok / clips
        print(f"\ndrive: {done:,} clips described, {todo:,} to go")
        print(f"projection at {per:,.0f} tokens per clip: {todo * per / 1e9:.2f} billion tokens")
        if pct_per_clip: print(f"projection in weekly limits: {todo * pct_per_clip / 100:.1f} account-weeks ({todo * pct_per_clip / 200:.1f} weeks with two accounts)")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("record"); r.add_argument("batch"); r.add_argument("--tokens"); r.add_argument("--who"); r.add_argument("--plan"); r.add_argument("--effort")
    r.add_argument("--transcribe", choices=["on", "off"]); r.add_argument("--wall-seconds", type=int); r.add_argument("--notes")
    for k in ("--weekly-before", "--weekly-after", "--five-hour-before", "--five-hour-after"): r.add_argument(k, type=float)
    s = sp.add_parser("summary"); s.add_argument("--drive")
    a = ap.parse_args(); record(a) if a.cmd == "record" else summary(a)

if __name__ == "__main__":
    main()
