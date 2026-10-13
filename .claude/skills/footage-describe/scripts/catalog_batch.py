#!/usr/bin/env python3
"""footage-describe-batch: same output as catalog.py, but every clip's requests go through the Message Batches API
(50% of list prices, asynchronous). Two batches run in sequence: describe for every clip, then verify.

usage: catalog_batch.py TARGET [TARGET ...] [--out DIR] [--note PATH] [--batch-name NAME] [--no-verify]
                        [--transcribe auto|off] [--language es] [--primary DEVICE] [--include-weak-sync] [--dry-run]
                        [--effort xhigh] [--verify-effort xhigh] [--model claude-opus-5-5] [--poll 30] [--keep-work] [--force]
"""
import argparse, datetime, json, os, shutil, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); SKILL_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline as pl, transcribe as tr, schema
from catalog import collect, read_note, load_json, load_sync, resolve_sync, plan_transcripts

DISCOUNT = 0.5

def run_batch(client, requests, poll, label):
    t0 = time.time(); batch = client.messages.batches.create(requests=requests)
    print(f"[{label}] batch {batch.id}: {len(requests)} requests submitted {datetime.datetime.now().isoformat(timespec='seconds')}", flush=True)
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended": break
        print(f"[{label}] {b.processing_status}: processing={b.request_counts.processing} succeeded={b.request_counts.succeeded} errored={b.request_counts.errored} ({int(time.time()-t0)}s)", flush=True)
        time.sleep(poll)
    wall = time.time() - t0
    print(f"[{label}] ended after {wall:.0f}s: succeeded={b.request_counts.succeeded} errored={b.request_counts.errored} expired={b.request_counts.expired}", flush=True)
    out = {}
    for r in client.messages.batches.results(batch.id):
        if r.result.type == "succeeded":
            msg = r.result.message
            if msg.stop_reason == "refusal": out[r.custom_id] = {"error": f"the model declined this request: {getattr(msg, 'stop_details', None)}"}; continue
            text = "".join(bk.text for bk in msg.content if getattr(bk, "type", "") == "text")
            try: parsed = json.loads(text)
            except Exception as e: out[r.custom_id] = {"error": f"unparseable output: {e}"}; continue
            out[r.custom_id] = {"json": parsed, "usage": pl.usage_dict(msg.usage), "cost_usd": pl.cost_of(msg.usage, msg.model, DISCOUNT), "model": msg.model}
        else:
            err = getattr(r.result, "error", None)
            out[r.custom_id] = {"error": f"{r.result.type}: {getattr(err, 'type', '')} {getattr(err, 'message', '')}".strip()}
    return batch.id, wall, out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("targets", nargs="+")
    ap.add_argument("--out"); ap.add_argument("--note"); ap.add_argument("--batch-name")
    ap.add_argument("--no-verify", action="store_true"); ap.add_argument("--transcribe", choices=["auto", "off"], default="auto"); ap.add_argument("--language")
    ap.add_argument("--primary"); ap.add_argument("--include-weak-sync", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--effort", default="xhigh"); ap.add_argument("--verify-effort", default="xhigh")
    ap.add_argument("--model", default="claude-opus-5-5"); ap.add_argument("--poll", type=int, default=30)
    ap.add_argument("--keep-work", action="store_true"); ap.add_argument("--force", action="store_true"); ap.add_argument("--camera-id")
    a = ap.parse_args()
    clips = collect(a.targets)
    if not clips: sys.exit("no video files found")
    preps = {}; meta = {}
    for clip in clips:
        folder = os.path.dirname(clip); stem = os.path.splitext(os.path.basename(clip))[0]
        batch = a.batch_name or os.path.basename(folder)
        out_dir = os.path.abspath(a.out) if a.out else folder; os.makedirs(out_dir, exist_ok=True)
        final = os.path.join(out_dir, f"{stem}.json"); existing = load_json(final)
        if existing and not a.force: print(f"[{stem}] exists, skipping (use --force to redo)"); continue
        note_text = schema.with_client_note(read_note(a.note or os.path.join(folder, "directors_note.md")), folder)
        if not note_text: print(f"[{stem}] no directors note; continuing with the folder date only", file=sys.stderr)
        roles = tr.default_main(tr.audio_sources(note_text), folder)
        work = os.path.join(out_dir, "outputs", "_describe_work", stem); runs_dir = os.path.join(out_dir, "outputs", "describe_runs"); os.makedirs(runs_dir, exist_ok=True)
        print(f"[{stem}] preparing")
        stats = pl.audio_stats(clip); duration = pl.probe(clip)["duration"]
        sync = resolve_sync(load_sync(folder), stem, folder, roles, a.primary, a.include_weak_sync)
        transcripts, source, provided = plan_transcripts(clip, stats, duration, sync, roles, a, note_text, print)
        sync_arg = {"fields": sync["fields"], "note": sync["note"], "transcript_source": source} if sync else {"fields": None, "note": None, "transcript_source": source}
        key = stem if stem not in preps else f"{stem}__{len(preps)}"
        preps[key] = pl.prepare(clip, batch, note_text, transcripts, work, sync_arg)
        if a.camera_id: preps[key]["out"]["camera_metadata"]["camera_id"] = a.camera_id
        meta[key] = {"clip": clip, "final": final, "runs": runs_dir, "work": work, "stem": stem, "existing": existing, "source": source, "provided": provided}
        m = preps[key]["manifest"]; print(f"[{stem}] {m['images']} images, ~{m['estimated_input_tokens']} input tokens, transcript from {source or 'nothing'}" + (", dark clip" if m["dark"] else ""))
    if not preps: sys.exit("nothing to do")
    if a.dry_run:
        for key, p in preps.items():
            json.dump({"manifest": p["manifest"], "deterministic": p["out"]}, open(os.path.join(meta[key]["runs"], f"{meta[key]['stem']}.dryrun.json"), "w"), indent=2)
            if not a.keep_work: shutil.rmtree(meta[key]["work"], ignore_errors=True)
        print(f"dry run: {len(preps)} clip(s) prepared, nothing submitted"); return
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    client = anthropic.Anthropic()
    reqs = [Request(custom_id=key, params=MessageCreateParamsNonStreaming(model=a.model, max_tokens=32000, system=pl.SYSTEM,
                messages=[{"role": "user", "content": p["content"]}], output_config={"effort": a.effort, "format": {"type": "json_schema", "schema": pl.CONTENT_SCHEMA}}))
            for key, p in preps.items()]
    b1, w1, res1 = run_batch(client, reqs, a.poll, "describe")
    logs = {}; outs = {}
    for key, p in preps.items():
        r = res1.get(key, {"error": "no result returned"})
        logs[key] = {"clip": p["clip"], "batch": p["batch"], "batch_mode": True, "manifest": p["manifest"],
                     "describe": {"batch_id": b1, "queue_wall_s": round(w1, 1), "effort": a.effort, **{k: v for k, v in r.items() if k != "json"}}}
        if "json" not in r: print(f"[{meta[key]['stem']}] describe failed: {r['error']}"); continue
        outs[key] = pl.apply_content(p["out"], r["json"], meta[key]["source"], meta[key]["provided"]); logs[key]["describe"]["notes"] = r["json"].get("notes")
        print(f"[{meta[key]['stem']}] described: {r['usage']['input_tokens']} in / {r['usage']['output_tokens']} out, ${r['cost_usd']:.3f} (batch price)")
    if not a.no_verify and outs:
        vreqs = [Request(custom_id=key, params=MessageCreateParamsNonStreaming(model=a.model, max_tokens=32000, system=pl.VERIFY_SYSTEM,
                    messages=[{"role": "user", "content": pl.verify_content(preps[key], outs[key]["content"]["visual_description"])}],
                    output_config={"effort": a.verify_effort, "format": {"type": "json_schema", "schema": pl.VERIFY_SCHEMA}}))
                 for key in outs]
        b2, w2, res2 = run_batch(client, vreqs, a.poll, "verify")
        for key in outs:
            r = res2.get(key, {"error": "no result returned"})
            logs[key]["draft_visual_description"] = outs[key]["content"]["visual_description"]
            logs[key]["verify"] = {"batch_id": b2, "queue_wall_s": round(w2, 1), "effort": a.verify_effort, **{k: v for k, v in r.items() if k != "json"}}
            if "json" not in r: print(f"[{meta[key]['stem']}] verify failed: {r['error']}; keeping the draft"); continue
            logs[key]["verify"]["corrections"] = r["json"].get("corrections", [])
            outs[key]["content"]["visual_description"] = r["json"]["visual_description"]
            print(f"[{meta[key]['stem']}] verified: {len(r['json'].get('corrections', []))} corrections, ${r['cost_usd']:.3f} (batch price)")
    total = 0.0
    for key, out in outs.items():
        lg = logs[key]; lg["total_cost_usd"] = round((lg["describe"].get("cost_usd") or 0) + (lg.get("verify", {}).get("cost_usd") or 0), 4); total += lg["total_cost_usd"]
        out["generated_at"] = schema.now(); out["generated_by"] = {"skill": "footage-describe-batch", "model": lg["describe"].get("model"), "effort": a.effort, "verify_model": (lg.get("verify") or {}).get("model"), "verify_effort": (a.verify_effort if not a.no_verify else None)}
        out = pl.merge_existing(out, meta[key]["existing"])
        pl.save_reference_stills(meta[key]["clip"], out, os.path.dirname(meta[key]["final"]), print)
        json.dump(out, open(meta[key]["final"], "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        json.dump(lg, open(os.path.join(meta[key]["runs"], f"{meta[key]['stem']}.run.json"), "w"), indent=2)
        if not a.keep_work: shutil.rmtree(meta[key]["work"], ignore_errors=True)
        print(f"[{meta[key]['stem']}] wrote {meta[key]['final']}")
    print(f"done: {len(outs)} of {len(preps)} clip(s), estimated cost ${total:.2f} at batch prices")

if __name__ == "__main__":
    main()
