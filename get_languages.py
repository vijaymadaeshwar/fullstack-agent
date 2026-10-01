"""Multilingual STT + every Kokoro voice, so Jarvis can hear and speak any
language it has. Resumable: a killed run picks up where it stopped."""
import os
import sys
import time

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
from huggingface_hub import hf_hub_download

HOME = os.path.expanduser("~")
LOGB = sys.argv[1] if len(sys.argv) > 1 else "download.log"


def log(m):
    line = "%s %s" % (time.strftime("%H:%M:%S"), m)
    print(line, flush=True)
    with open(LOGB, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


WHISPER_FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]
# Kokoro ships 9 languages. Pull every voice so any pick works with no network.
KOKORO_LANGS = ["a", "b", "e", "f", "h", "i", "j", "p", "z"]


def whisper(repo):
    """Resumable direct download into the HF cache layout backtalk looks for."""
    d = os.path.join(HOME, ".cache", "huggingface", "hub",
                     "models--" + repo.replace("/", "--"))
    os.makedirs(d, exist_ok=True)
    for name in WHISPER_FILES:
        dest = os.path.join(d, name)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            log("  have %s" % name)
            continue
        url = "https://huggingface.co/%s/resolve/main/%s" % (repo, name)
        tmp = dest + ".part"
        for attempt in range(1, 9):
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"Range": "bytes=%d-" % have})
                with urllib.request.urlopen(req, timeout=60) as r:
                    if have and r.status != 206:
                        have = 0
                    total = int(r.headers.get("Content-Length", 0)) + have
                    mode = "ab" if have else "wb"
                    last = time.time()
                    with open(tmp, mode) as fh:
                        while True:
                            b = r.read(1 << 20)
                            if not b:
                                break
                            fh.write(b)
                            have += len(b)
                            if time.time() - last > 10:
                                last = time.time()
                                log("  %s %d/%dMB" % (name, have // 1048576,
                                                      total // 1048576))
                if os.path.getsize(tmp) == 0:
                    raise IOError("empty")
                os.replace(tmp, dest)
                log("  done %s (%dMB)" % (name, os.path.getsize(dest) // 1048576))
                break
            except Exception as exc:
                log("  %s retry %d: %s" % (name, attempt, exc))
                time.sleep(5)
        else:
            log("  GAVE UP on %s" % name)


def kokoro():
    import json
    import urllib.request
    api = "https://huggingface.co/api/models/hexgrad/Kokoro-82M"
    info = json.load(urllib.request.urlopen(api, timeout=45))
    voices = [f["rfilename"] for f in info.get("siblings", [])
              if f["rfilename"].startswith("voices/") and f["rfilename"].endswith(".pt")]
    log("kokoro: %d voice files across %d languages" % (len(voices), len(KOKORO_LANGS)))
    ok = 0
    for v in voices:
        try:
            hf_hub_download("hexgrad/Kokoro-82M", filename=v)
            ok += 1
        except Exception as exc:
            log("  voice failed %s: %s" % (v, exc))
        if ok % 10 == 0 and ok:
            log("  voices %d/%d" % (ok, len(voices)))
    log("kokoro: %d/%d voices ready" % (ok, len(voices)))


if __name__ == "__main__":
    log("=== kokoro voices first (small, makes speech work immediately)")
    kokoro()
    for repo in ["Systran/faster-whisper-small", "Systran/faster-whisper-medium"]:
        log("=== %s" % repo)
        whisper(repo)
    log("=== ALL DONE")