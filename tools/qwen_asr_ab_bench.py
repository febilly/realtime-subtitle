"""Offline Qwen3-ASR A/B bench: one engine configuration over FLEURS and the long-form clips.

    python tools/qwen_asr_ab_bench.py --repo . --label q6k --out q6k.json
    python tools/qwen_asr_ab_bench.py --repo ../old-checkout --label old --out old.json
    python tools/qwen_asr_ab_bench.py --repo . --label q8 --decoder Qwen3-ASR-1.7B-Q8_0.gguf --out q8.json

Data lives in local_models/eval (not tracked):
  fleurs/<en_us|ja_jp|cmn_hans_cn>.tsv + fleurs/<lang>/test/*.wav  google/fleurs test split,
      human transcriptions; the first ~100 utterances of each test.tar.gz are enough.
  clips/steamos.wav, ja_900.wav, zh1c_600.wav with yt_en.txt, yt_ja.txt, sv_zh.txt  casual speech;
      the references are YouTube auto captions / SenseVoice, so use them for relative comparison only.

FLEURS: each utterance is one segment. Clips: 8-16 s chunks cut at the quietest 100 ms, the
same cuts for every configuration. Every segment is decoded like the live path does it: a partial
of its first 70 % (becomes the speculative draft), then the final; only finals are scored.
VRAM is the nvidia-smi delta, so nothing else should use the GPU during a run.
"""
import argparse, json, os, struct, subprocess, sys, threading, time, unicodedata, wave
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parents[1] / "local_models" / "eval"
DATA = EVAL / "clips"
FLEURS = EVAL / "fleurs"
FLEURS_LANGS = {"en": "en_us", "ja": "ja_jp", "zh": "cmn_hans_cn"}
CLIPS = {
    "en": ("steamos.wav", 600, 240, "yt_en.txt"),
    "ja": ("ja_900.wav", 0, 180, "yt_ja.txt"),
    "zh": ("zh1c_600.wav", 0, 200, "sv_zh.txt"),
}


def read_wav(path):
    data = path.read_bytes()
    if data[:4] != b"RIFF":
        return None
    pos, fmt, sr = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt, ch, sr = struct.unpack("<HHI", body[:8])
            bits = struct.unpack("<H", body[14:16])[0]
        elif cid == b"data":
            if len(body) < size:
                return None  # truncated by the partial archive download
            if fmt == 3 and bits == 32:
                a = np.frombuffer(body, np.float32)
            elif fmt == 1 and bits == 16:
                a = np.frombuffer(body, np.int16).astype(np.float32) / 32768
            else:
                return None
            if ch > 1:
                a = a.reshape(-1, ch).mean(axis=1)
            return a.astype(np.float32) if sr == 16000 else None
        pos += 8 + size + (size & 1)
    return None


def fleurs_utterances(lang, limit=100):
    d = FLEURS / FLEURS_LANGS[lang]
    refs = {}
    for line in (FLEURS / f"{FLEURS_LANGS[lang]}.tsv").read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) > 2:
            refs[cols[1]] = cols[2]
    out = []
    for wav in sorted((d / "test").glob("*.wav")):
        if wav.name in refs:
            audio = read_wav(wav)
            if audio is not None and len(audio) > 8000:
                out.append((wav.name, audio, refs[wav.name]))
        if len(out) >= limit:
            break
    return out


def load(name, start, dur):
    with wave.open(str(DATA / name)) as w:
        w.setpos(int(start * 16000))
        return np.frombuffer(w.readframes(int(dur * 16000)), np.int16).astype(np.float32) / 32768


def cuts(audio, lo=8, hi=16):
    frame = 1600
    energy = np.array([np.sqrt(np.mean(audio[i:i + frame] ** 2)) for i in range(0, len(audio) - frame, frame)])
    out = [0]
    while out[-1] + hi * 16000 < len(audio):
        a, b = (out[-1] + lo * 16000) // frame, (out[-1] + hi * 16000) // frame
        out.append(int(a + np.argmin(energy[a:b])) * frame + frame // 2)
    out.append(len(audio))
    return out


def lexical(t):
    v = unicodedata.normalize("NFKC", t or "").casefold()
    return "".join(c for c in v if not c.isspace() and not unicodedata.category(c).startswith("P"))


def words(t):
    v = unicodedata.normalize("NFKC", t or "").casefold()
    v = "".join(c if not unicodedata.category(c).startswith("P") or c == "'" else " " for c in v)
    return [w.strip("'") for w in v.split() if w.strip("'")]


def edit(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def gpu_used():
    r = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int(r.stdout.split()[0])


class Peak(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.peak = 0; self.stop = False
    def run(self):
        while not self.stop:
            self.peak = max(self.peak, gpu_used()); time.sleep(0.3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--model-dir")
    ap.add_argument("--decoder")
    ap.add_argument("--mmproj")
    ap.add_argument("--langs", default="en,ja,zh")
    ap.add_argument("--sets", default="clips,fleurs")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    a.out = str(Path(a.out).resolve())  # before chdir into --repo
    if a.decoder:
        os.environ["LOCAL_QWEN_ASR_DECODER_FILE"] = a.decoder
    if a.mmproj:
        os.environ["LOCAL_QWEN_ASR_MMPROJ_FILE"] = a.mmproj
    sys.path.insert(0, a.repo)
    os.chdir(a.repo)
    import logging
    logging.basicConfig(level=logging.WARNING)
    import config
    config.LOCAL_QWEN_LOG_PIPELINE_TIMING = False
    from local_inference.asr_qwen3 import Qwen3ASREngine

    base = gpu_used()
    t0 = time.perf_counter()
    kwargs = {"model_dir": a.model_dir} if a.model_dir else {}
    eng = Qwen3ASREngine(**kwargs)
    load_s = time.perf_counter() - t0
    after_load = gpu_used()
    eng.set_language("auto")
    # warm-up: shader compilation and first-shape allocations, not part of the timings
    warm = load(*CLIPS["en"][:2], 12)
    for n in (2, 5, 8, 12):
        eng.transcribe(warm[: n * 16000], update_context=False); eng.reset_draft()

    import io, contextlib
    peak = Peak(); peak.start()
    result = {"label": a.label, "load_s": load_s, "vram_base_mb": base, "vram_loaded_mb": after_load - base, "langs": {}}

    def decode(chunk):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tp = time.perf_counter()
            eng.transcribe(chunk[: int(len(chunk) * 0.7)], update_context=False)
            tf = time.perf_counter()
            r = eng.transcribe(chunk, update_context=True)
            te = time.perf_counter()
        eng.reset_draft()
        return (r or {}).get("text", ""), tf - tp, te - tf

    def units(lang, t):
        return words(t) if lang == "en" else list(lexical(t))

    result["fleurs"] = {}
    for lang in (a.langs.split(",") if "fleurs" in a.sets else []):
        utts = fleurs_utterances(lang)
        errs = refs = 0; t_final = audio_s = 0.0; rows = []
        for name, audio, ref in utts:
            hyp, tp, tf = decode(audio)
            R, H = units(lang, ref), units(lang, hyp)
            e = edit(H, R); errs += e; refs += len(R); t_final += tf; audio_s += len(audio) / 16000
            rows.append({"wav": name, "ref": ref, "hyp": hyp, "err": e, "n": len(R)})
        unit = "WER" if lang == "en" else "CER"
        result["fleurs"][lang] = {unit: errs / refs, "n_utts": len(utts), "rtf_final": t_final / audio_s,
                                  "ms_per_final": 1000 * t_final / len(utts), "rows": rows}
        print(f"{a.label} fleurs-{lang}: {unit}={errs / refs:.2%} n={len(utts)} RTF(final)={t_final / audio_s:.4f} "
              f"ms/final={1000 * t_final / len(utts):.0f}", flush=True)

    for lang in (a.langs.split(",") if "clips" in a.sets else []):
        wav, start, dur, ref = CLIPS[lang]
        audio = load(wav, start, dur)
        cs = cuts(audio)
        texts, t_final, t_partial, enc = [], 0.0, 0.0, 0.0
        for s, e in zip(cs, cs[1:]):
            chunk = audio[s:e]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tp = time.perf_counter()
                eng.transcribe(chunk[: int(len(chunk) * 0.7)], update_context=False)
                t_partial += time.perf_counter() - tp
                tf = time.perf_counter()
                r = eng.transcribe(chunk, update_context=True)
                t_final += time.perf_counter() - tf
            eng.reset_draft()
            texts.append((r or {}).get("text", ""))
        ref_text = (DATA / ref).read_text(encoding="utf-8")
        hyp = " ".join(texts)
        if lang == "en":
            R, H = words(ref_text), words(hyp); unit = "WER"
        else:
            R, H = list(lexical(ref_text)), list(lexical(hyp)); unit = "CER"
        err = edit(H, R) / len(R)
        result["langs"][lang] = {
            unit: err, "n_chunks": len(cs) - 1, "audio_s": dur,
            "rtf_final": t_final / dur, "rtf_partial": t_partial / (dur * 0.7),
            "ms_per_final": 1000 * t_final / (len(cs) - 1), "text": hyp,
        }
        print(f"{a.label} {lang}: {unit}={err:.2%} RTF(final)={t_final / dur:.4f} "
              f"ms/final={1000 * t_final / (len(cs) - 1):.0f} ms/partial={1000 * t_partial / (len(cs) - 1):.0f}", flush=True)
    peak.stop = True; peak.join()
    result["vram_peak_mb"] = peak.peak - base
    print(f"{a.label}: load {load_s:.1f}s, VRAM +{after_load - base} MB loaded, +{peak.peak - base} MB peak", flush=True)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    eng.unload()
    os._exit(0)


if __name__ == "__main__":
    main()
