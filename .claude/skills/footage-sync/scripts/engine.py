"""Recorder-first matching for footage-sync, the way an editor syncs a shoot: first line up every recording against the
others (two lavs, a room recorder, the files a recorder split into when it was paused), then place every camera clip
on that one master timeline using all the recordings at once, then use each camera's shooting order to settle the rest.

Features: onset envelopes (rises in log energy) in four speech bands, 100 per second, digital silence masked. Tested on
85 clip/recording pairs from the editor's hand-synced timelines: the band-wise score found the editor's offset 76 times
against 58 for a single broadband envelope; wind, water and engines sit outside the speech bands.
"""
import math
import numpy as np

HZ = 100; SR = 8000; HOP = SR // HZ; NFFT = 256
BANDS = [(300, 700), (700, 1500), (1500, 3000), (3000, 4000)]
SILENCE_POWER = 1e-10; CLIP_SKIP_S = 0.3; MAX_SILENT_FRACTION = 0.3
CHUNK_S = 120          # recordings are matched to each other in 2-minute pieces


def next_pow2(n): return 1 << max(1, int(n) - 1).bit_length()


def band_features(x, skip_s=0.0):
    """(frames, bands) float32 onset envelopes and a digital-silence mask, one frame per 10 ms."""
    if x is None: return None, None
    n = len(x) // HOP
    if n < 8: return None, None
    freqs = np.fft.rfftfreq(NFFT, 1 / SR); masks = [(freqs >= lo) & (freqs < hi) for lo, hi in BANDS]
    win = np.hanning(NFFT).astype(np.float32); be = np.zeros((n, len(BANDS)), np.float32); silent = np.zeros(n, bool)
    step = 50000
    for c0 in range(0, n, step):
        c1 = min(n, c0 + step)
        silent[c0:c1] = np.mean(x[c0 * HOP:c1 * HOP].astype(np.float64).reshape(-1, HOP) ** 2, axis=1) < SILENCE_POWER
        s0 = c0 * HOP - NFFT // 2; need = (c1 - c0 - 1) * HOP + NFFT
        seg = x[max(0, s0):max(0, s0) + need - max(0, -s0)].astype(np.float32)
        if s0 < 0: seg = np.concatenate([np.zeros(-s0, np.float32), seg])
        if len(seg) < need: seg = np.concatenate([seg, np.zeros(need - len(seg), np.float32)])
        fr = seg[(np.arange(c1 - c0) * HOP)[:, None] + np.arange(NFFT)[None, :]] * win
        p = np.abs(np.fft.rfft(fr, axis=1)) ** 2
        for b, m in enumerate(masks): be[c0:c1, b] = p[:, m].sum(1)
    le = np.log10(np.maximum(be, 1e-10))
    d = np.maximum(np.diff(le, axis=0, prepend=le[:1]), 0.0)
    d[silent | np.concatenate([[True], silent[:-1]])] = 0.0          # an onset out of silence says nothing about content
    for b in range(d.shape[1]):
        pos = d[:, b][d[:, b] > 0]
        if len(pos) > 20: d[:, b] = np.minimum(d[:, b], np.percentile(pos, 99))
    if skip_s: d[:int(skip_s * HZ)] = 0.0
    return (d - d.mean(axis=0)).astype(np.float32), silent


class Track:
    """One recording's features with cached spectra, so many clips can be scored against it quickly."""
    def __init__(self, F, silent, n_max):
        self.F = F; self.sil = silent; self.lb = len(F); self.N = next_pow2(self.lb + n_max)
        self.spec = np.fft.rfft(F.astype(np.float64), n=self.N, axis=0).astype(np.complex64)
        z = np.zeros((1, F.shape[1]))
        self.c1 = np.vstack([z, np.cumsum(F, axis=0, dtype=np.float64)]); self.c2 = np.vstack([z, np.cumsum(F.astype(np.float64) ** 2, axis=0)])
        self.cs = np.concatenate([[0], np.cumsum(silent)])

    def score(self, A, min_overlap=None):
        """Mean over bands of the Pearson correlation of A against every window of this recording.
        lags[i] = frame in the recording where A's first frame sits. Invalid windows score -1."""
        la, lb = len(A), self.lb; N = self.N
        spec = self.spec if la + lb - 1 <= N else np.fft.rfft(self.F.astype(np.float64), n=next_pow2(la + lb), axis=0)
        N = 2 * (spec.shape[0] - 1)
        lags = np.arange(-(la - 1), lb)
        lo = np.clip(lags, 0, lb); hi = np.clip(lags + la, 0, lb); nreal = hi - lo
        need = min(la / 2 if min_overlap is None else min_overlap, 0.8 * lb)   # a clip longer than the recording can still match it
        valid = (nreal >= need) & ((self.cs[hi] - self.cs[lo]) <= MAX_SILENT_FRACTION * np.maximum(nreal, 1))
        Am = A.astype(np.float64) - A.mean(axis=0); norms = np.linalg.norm(Am, axis=0)
        SA = np.fft.rfft(Am / np.where(norms > 1e-9, norms, 1.0), n=N, axis=0)
        total = np.zeros(len(lags)); used = 0
        for k in range(A.shape[1]):
            if norms[k] <= 1e-9: continue
            r = np.fft.irfft(spec[:, k] * np.conj(SA[:, k]), n=N)
            corr = np.concatenate([r[N - (la - 1):], r[:lb]]) if la > 1 else r[:lb]
            s1 = self.c1[hi, k] - self.c1[lo, k]; s2 = self.c2[hi, k] - self.c2[lo, k]
            var = np.maximum(s2 - s1 ** 2 / la, 0.0)
            nc = corr / (np.sqrt(var) + 1e-9); nc[var <= 1e-6] = 0.0
            total += nc; used += 1
        if not used: return lags, np.full(len(lags), -1.0)
        total /= used; total[~valid] = -1.0
        return lags, total


def peak_stats(lags, sc, exclude=HZ):
    """Best lag, its score, ratio to the best score more than a second away, and z against the whole curve."""
    i = int(np.argmax(sc)); peak = float(sc[i])
    far = np.abs(lags - lags[i]) > exclude
    second = float(np.max(sc[far])) if far.any() else 0.0
    v = sc[sc > -1.0]
    z = float((peak - v.mean()) / (v.std() + 1e-12)) if len(v) > 10 else 0.0
    return int(lags[i]), peak, (peak / second if second > 1e-6 else 99.0), z


# ---------------------------------------------------------------- recordings to recordings

def link_recordings(recs, log=print):
    """Match every pair of recordings in 2-minute pieces. Returns edges {a, b, offset_s (position in b of a's start),
    drift_ppm, votes, z} for pairs where the pieces agree. Every pair is tried: device folders often hold several
    physical recorders (two lavs both filed under recorder1), and files that never overlapped simply find no match."""
    CH = CHUNK_S * HZ; edges = []
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            a, b = recs[i], recs[j]
            short, long_ = (a, b) if a["track"].lb <= b["track"].lb else (b, a)
            hop = max(CH, short["track"].lb // 40); votes = []
            for c0 in range(0, max(1, short["track"].lb - CH // 4), hop):
                A = short["track"].F[c0:c0 + CH]
                if len(A) < 30 * HZ or short["track"].sil[c0:c0 + len(A)].mean() > 0.5: continue
                lags, sc = long_["track"].score(A)
                lag, peak, ratio, z = peak_stats(lags, sc)
                if z >= 8 and ratio >= 1.5: votes.append((c0, lag - c0, z))
            if not votes: continue
            votes.sort(key=lambda v: v[1]); best = []
            for v in votes:
                cl = [w for w in votes if abs(w[1] - v[1]) <= 20]
                if sum(w[2] for w in cl) > sum(w[2] for w in best): best = cl
            if len(best) < 2 and not confirm_short(short["track"], long_["track"], best[0][1]): continue
            offs = np.array([w[1] for w in best], float); pos = np.array([w[0] for w in best], float)
            drift = 0.0
            if len(best) >= 3 and pos.max() - pos.min() > 600 * HZ: drift = float(np.polyfit(pos, offs, 1)[0]) * 1e6
            e = {"a": short["file"], "b": long_["file"], "offset_s": round(float(np.median(offs)) / HZ, 3), "drift_ppm": round(drift, 1),
                 "votes": len(best), "pieces": len(votes), "z": round(max(w[2] for w in best), 1)}
            edges.append(e)
            log(f"  {short['file']} starts at {e['offset_s']} s in {long_['file']} ({e['votes']} of {e['pieces']} matching pieces agree, drift {e['drift_ppm']} ppm)")
    return edges


def confirm_short(short, long_, off, piece=30 * HZ):
    """A link resting on one 2-minute piece (a short overlap) needs two 30-second pieces inside the implied overlap to
    land on the same offset."""
    lo = max(0, -off); hi = min(short.lb, long_.lb - off); agree = 0
    for c0 in range(lo, hi - piece + 1, piece):
        if short.sil[c0:c0 + piece].mean() > 0.5: continue
        lags, sc = long_.score(short.F[c0:c0 + piece]); lag, peak, ratio, z = peak_stats(lags, sc)
        if z >= 6 and abs((lag - c0) - off) <= 10: agree += 1
    return agree >= 2


def build_groups(recs, edges, log=print):
    """Recordings joined by edges form one continuous session; place each on that session's master clock (the longest
    recording starts at 0). Returns groups [{id, members: [{file, device, start_s}], residual_s}]."""
    files = [r["file"] for r in recs]; dur = {r["file"]: r["track"].lb / HZ for r in recs}; dev = {r["file"]: r["device"] for r in recs}
    parent = {f: f for f in files}
    def find(f):
        while parent[f] != f: parent[f] = parent[parent[f]]; f = parent[f]
        return f
    for e in edges: parent[find(e["a"])] = find(e["b"])
    comps = {}
    for f in files: comps.setdefault(find(f), []).append(f)
    groups = []
    for k, members in enumerate(sorted(comps.values(), key=lambda m: -max(dur[f] for f in m))):
        root = max(members, key=lambda f: dur[f]); pos = {root: 0.0}
        es = sorted([e for e in edges if e["a"] in members], key=lambda e: -e["votes"] * e["z"])
        changed = True
        while changed:
            changed = False
            for e in es:
                if e["b"] in pos and e["a"] not in pos: pos[e["a"]] = pos[e["b"]] + e["offset_s"]; changed = True
                elif e["a"] in pos and e["b"] not in pos: pos[e["b"]] = pos[e["a"]] - e["offset_s"]; changed = True
        resid = max([abs(pos[e["a"]] - pos[e["b"]] - e["offset_s"]) for e in es] or [0.0])
        g = {"id": f"session{k + 1}", "members": [{"file": f, "device": dev[f], "start_s": round(pos[f], 3), "duration_s": round(dur[f], 3)} for f in sorted(members, key=lambda f: pos[f])],
             "residual_s": round(resid, 3)}
        groups.append(g)
        if len(members) > 1: log(f"  {g['id']}: {len(members)} recordings on one clock" + (f" (loop residual {resid:.3f} s, check)" if resid > 0.1 else ""))
    return groups


# ---------------------------------------------------------------- clips onto the master timeline

def group_curve(A, group, tracks, window=None):
    """Combined score of clip A over master time for one session: each recording's curve shifted to master time,
    summed, divided by sqrt(number of recordings covering that time). window = (lo, hi) master frames to keep."""
    la = len(A); lo = min(int(round(m["start_s"] * HZ)) - (la - 1) for m in group["members"])
    hi = max(int(round(m["start_s"] * HZ)) + tracks[m["file"]].lb for m in group["members"])
    S = np.zeros(hi - lo); C = np.zeros(hi - lo); per = {}
    for m in group["members"]:
        t = tracks[m["file"]]; lags, sc = t.score(A); pos = int(round(m["start_s"] * HZ))
        ok = sc > -1.0; idx = lags[ok] + pos - lo
        S[idx] += sc[ok]; C[idx] += 1; per[m["file"]] = (pos, lags[0], sc)
    out = np.full(hi - lo, -1.0); has = C > 0; out[has] = S[has] / np.sqrt(C[has])
    mlags = np.arange(lo, hi)
    if window is not None:
        keep = (mlags >= window[0]) & (mlags <= window[1]); return mlags[keep], out[keep], per, (mlags, out)
    return mlags, out, per, None


def member_matches(master_lag, la, group, per):
    """Per-recording offsets and scores implied by a master position."""
    out = []
    for m in group["members"]:
        pos, lag0, sc = per[m["file"]]; rl = master_lag - pos; lb = len(sc) - (la - 1)
        ov = min(rl + la, lb) - max(rl, 0)                             # frames of the clip inside this recording
        if ov < HZ: continue
        i = rl - lag0
        out.append({"recording": m["file"], "device": m["device"], "offset_seconds": round(rl / HZ, 3),
                    "score": round(float(sc[i]), 3) if 0 <= i < len(sc) else None, "overlap_s": round(ov / HZ, 1)})
    return out


def parts_agree(A, group, tracks, master_lag, tol=3):
    """Both halves of the clip (for long clips, a minute at 25% and at 75%) must land on the same master position."""
    la = len(A)
    if la < 8 * HZ: return None
    if la <= 120 * HZ: parts = [(0, la // 2), (la // 2, la)]
    else: parts = [(la // 4 - 30 * HZ, la // 4 + 30 * HZ), (3 * la // 4 - 30 * HZ, 3 * la // 4 + 30 * HZ)]
    for p0, p1 in parts:
        exp = master_lag + p0
        ml, sc, _, _ = group_curve(A[p0:p1], group, tracks, window=(exp - 200, exp + 200))
        if not len(sc) or sc.max() <= 0 or abs(int(ml[int(np.argmax(sc))]) - exp) > tol: return False
    return True


def camera_of(stem):
    import re
    m = re.match(r"[A-Za-z]+", stem); return m.group(0).upper() if m else "?"


def classify(c, z_conf=8.0, r_conf=1.5):
    """confident / weak / none from z, ratio, clip length and the halves check."""
    z, r, dur, halves = c["z"], c["ratio"], c["dur"], c.get("halves")
    if dur < 8:
        if z >= z_conf + 1 and r >= 2.0: return "confident"
    elif z >= z_conf and r >= r_conf and (halves is True or (z >= 15 and r >= 3)): return "confident"
    return "weak" if z >= 6 else "none"


def place_clips(clips, groups, tracks, log=print):
    """clips: [{stem, A, dur}]. Returns {stem: placement}. Three passes: global search per session, shooting-order
    check per camera, then a bounded search between confidently placed neighbours."""
    res = {}
    for c in clips:
        if c["A"] is None: res[c["stem"]] = {"status": "none", "reason": "camera track silent or missing", "dur": c["dur"]}; continue
        best = None; cands = []
        for g in groups:
            ml, sc, per, _ = group_curve(c["A"], g, tracks)
            if not len(sc) or sc.max() <= -1: continue
            lag, peak, ratio, z = peak_stats(ml, sc)
            mem = member_matches(lag, len(c["A"]), g, per)
            cands.append({"group": g["id"], "master_lag": lag, "z": round(z, 1), "ratio": round(ratio, 2), "overlap_s": max([m["overlap_s"] for m in mem] or [0])})
            if best is None or z > best["z"]:
                best = {"group": g["id"], "master_lag": lag, "score": round(peak, 4), "ratio": round(ratio, 2), "z": round(z, 1), "dur": c["dur"],
                        "members": mem, "method": "global"}
        if best is None: res[c["stem"]] = {"status": "none", "reason": "no recording long enough to compare", "dur": c["dur"]}; continue
        g = next(g for g in groups if g["id"] == best["group"])
        if best["z"] >= 6 and c["dur"] >= 8: best["halves"] = parts_agree(c["A"], g, tracks, best["master_lag"])
        best["status"] = classify(best); best["candidates"] = cands; res[c["stem"]] = best
    A = {c["stem"]: c["A"] for c in clips}
    # shooting order: one camera's confident clips must rise in master time; the rest of the run is suspect
    by = {}
    for st, p in res.items():
        if p["status"] == "confident" and camera_of(st) != "?": by.setdefault((camera_of(st), p["group"]), []).append(st)
    for key, stems in by.items():
        stems.sort(); lags = [res[s]["master_lag"] for s in stems]
        keep = longest_rising(lags)
        for i, st in enumerate(stems):
            strong = res[st]["z"] >= 15 and res[st]["ratio"] >= 3     # file numbers can restart (second body, new card)
            if i not in keep and not strong: res[st]["status"] = "weak"; res[st]["reason"] = "out of shooting order with its neighbours"
    # bounded pass between confident neighbours of the same camera and session
    for _ in range(4):
        new = 0; anchors = {}
        for st, p in res.items():
            if p["status"] == "confident" and camera_of(st) != "?": anchors.setdefault((camera_of(st), p["group"]), []).append(st)
        for (cam, gid), stems in anchors.items():
            stems.sort(); g = next(g for g in groups if g["id"] == gid)
            for prv, nxt in zip(stems, stems[1:]):
                lo = res[prv]["master_lag"] + len(A[prv]) - 2 * HZ
                for st in sorted(s for s in res if prv < s < nxt and camera_of(s) == cam and res[s]["status"] != "confident" and A.get(s) is not None):
                    la = len(A[st]); hi = res[nxt]["master_lag"] - la + 2 * HZ
                    if hi - lo < HZ or hi - lo > 45 * 60 * HZ: continue
                    ml, sc, per, full = group_curve(A[st], g, tracks, window=(lo, hi))
                    if not len(sc) or sc.max() <= 0: continue
                    lag, peak, ratio, _ = peak_stats(ml, sc)
                    fv = full[1][full[1] > -1.0]; zf = float((peak - fv.mean()) / (fv.std() + 1e-12))
                    short = la < 8 * HZ
                    if zf < (6 if short else 5) or ratio < (1.6 if short else 1.3): continue
                    halves = None if short else parts_agree(A[st], g, tracks, lag)
                    if halves is False: continue
                    res[st] = {"group": gid, "master_lag": lag, "score": round(peak, 4), "ratio": round(ratio, 2), "z": round(zf, 1), "dur": la / HZ,
                               "members": member_matches(lag, la, g, per), "method": f"between {prv} and {nxt}", "halves": halves, "status": "confident"}
                    new += 1
        if not new: break
    # two clips of one camera, or three of any, can't start at the same moment
    seen = {}
    for st, p in res.items():
        if p["status"] == "confident": seen.setdefault((p["group"], round(p["master_lag"] / (HZ / 2))), []).append(st)
    for stems in seen.values():
        cams = [camera_of(s) for s in stems]
        if len(stems) >= 3 or len(set(cams)) < len(cams):
            for st in stems: res[st]["status"] = "weak"; res[st]["reason"] = "same start as " + ", ".join(t for t in stems if t != st)
    for p in res.values():
        if "master_lag" in p: p["master_offset_s"] = round(p.pop("master_lag") / HZ, 3)
    return res


def longest_rising(vals):
    """Indices of a longest strictly rising subsequence."""
    import bisect
    tails, tails_i, prev = [], [], [-1] * len(vals)
    for i, v in enumerate(vals):
        k = bisect.bisect_left(tails, v)
        if k == len(tails): tails.append(v); tails_i.append(i)
        else: tails[k] = v; tails_i[k] = i
        prev[i] = tails_i[k - 1] if k else -1
    out = set(); i = tails_i[-1] if tails_i else -1
    while i >= 0: out.add(i); i = prev[i]
    return out


def bridges(res, clips, groups, tracks, log=print):
    """Join sessions with the clips that run across a gap between them: a recorder paused and resumed into files that
    never overlap, with the camera rolling through the pause. For every confidently placed clip, the stretches of it
    that its session's recordings don't cover (20 s or more) are matched against the other sessions; a strong match
    fixes where the two sessions sit relative to each other. Returns synthetic edges between the sessions' roots."""
    gid = {g["id"]: g for g in groups}
    root = {g["id"]: min(g["members"], key=lambda m: abs(m["start_s"]))["file"] for g in groups}
    rstart = {g["id"]: next(m["start_s"] for m in g["members"] if m["file"] == root[g["id"]]) for g in groups}
    A = {c["stem"]: c["A"] for c in clips}; votes = {}
    for st, p in res.items():
        if p.get("status") != "confident" or A.get(st) is None or p["z"] < 10: continue
        la = len(A[st]); covered = np.zeros(la, bool)
        for m in p["members"]:
            rl = int(round(m["offset_seconds"] * HZ)); lb = tracks[m["recording"]].lb
            covered[max(0, -rl):max(0, min(la, lb - rl))] = True
        gaps = []; i = 0
        while i < la:
            if covered[i]: i += 1; continue
            j = i
            while j < la and not covered[j]: j += 1
            if j - i >= 20 * HZ: gaps.append((i, j) if i == 0 else (i, min(j, i + 300 * HZ)))
            i = j
        for g0, g1 in gaps:
            if g0 == 0: g0 = max(0, g1 - 300 * HZ)                     # the stretch next to the covered part
            for g in groups:
                if g["id"] == p["group"]: continue
                ml, sc, per, _ = group_curve(A[st][g0:g1], g, tracks)
                if not len(sc) or sc.max() <= -1: continue
                lag, peak, ratio, z = peak_stats(ml, sc)
                if z < 10 or ratio < 2: continue
                clip_in_g = lag - g0                                  # clip start in g's master time (frames)
                # the same moment in both sessions: g's root starts at rstart_g + (clip in p - clip in g) on p's clock
                off = p["master_offset_s"] - clip_in_g / HZ + rstart[g["id"]] - rstart[p["group"]]
                votes.setdefault((g["id"], p["group"]), []).append((off, st, round(z, 1)))
    edges = []
    for (ga, gb), vs in votes.items():
        vs.sort(); best = []
        for v in vs:
            cl = [w for w in vs if abs(w[0] - v[0]) <= 0.1]
            if len(cl) > len(best): best = cl
        e = {"a": root[ga], "b": root[gb], "offset_s": round(float(np.median([w[0] for w in best])), 3), "drift_ppm": 0.0, "votes": len(best), "pieces": len(vs),
             "z": max(w[2] for w in best), "via": "clips " + ", ".join(w[1] for w in best[:5])}
        edges.append(e); log(f"  {e['a']} starts at {e['offset_s']} s in {e['b']}, bridged by {e['via']}")
    return edges


def sync_batch(recs, clips, log=print):
    """The whole recorder-first pass: recordings into sessions, clips onto sessions, sessions joined by clips that span
    them, then clips placed again on the joined sessions."""
    tracks = {r["file"]: r["track"] for r in recs}
    edges = link_recordings(recs, log) if len(recs) > 1 else []
    groups = build_groups(recs, edges, log)
    res = place_clips(clips, groups, tracks, log)
    extra = bridges(res, clips, groups, tracks, log) if len(groups) > 1 else []
    if extra:
        edges = edges + extra; groups = build_groups(recs, edges, log); res = place_clips(clips, groups, tracks, log)
    for p in res.values(): p.pop("candidates", None)
    return edges, groups, res
