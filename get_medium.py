"""Download faster-whisper-medium.en in the background, resumably.

Range requests mean a dropped connection resumes instead of restarting,
and a partial file is never mistaken for a complete one: the downloader
writes to .part and only renames on a full-length match.
"""
import os
import sys
import time
import urllib.request

REPO = "Systran/faster-whisper-medium.en"
FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]
BASE = "https://huggingface.co/{}/resolve/main/{}"
OUT = os.path.expanduser(
    r"~\.cache\huggingface\hub\models--Systran--faster-whisper-medium.en")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medium.log")


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def size_of(url):
    """Content-Length, or 0 when the server will not say. Never raises:
    an unknown size must not be able to kill the downloader."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception as e:
        log(f"  (size unknown for {url.rsplit('/', 1)[-1]}: {e})")
        return 0


def download(name):
    try:
        return _download(name)
    except Exception as e:
        log(f"{name}: failed, {type(e).__name__}: {e}")
        return False


def _download(name):
    url = BASE.format(REPO, name)
    final = os.path.join(OUT, name)
    if os.path.exists(final):
        return True
    total = size_of(url)
    part = final + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if total and have >= total:
        os.replace(part, final)
        return True
    log(f"{name}: {have/1e6:.0f}MB of {total/1e6:.0f}MB")
    for attempt in range(60):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0",
                              "Range": f"bytes={have}-"})
            with urllib.request.urlopen(req, timeout=120) as r, \
                    open(part, "ab") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    have += len(chunk)
                    if have % (32 << 20) < (1 << 20):
                        pct = 100.0 * have / total if total else 0
                        log(f"{name}: {have/1e6:.0f}MB ({pct:.0f}%)")
            if not total or os.path.getsize(part) >= total:
                os.replace(part, final)
                log(f"{name}: done")
                return True
        except Exception as e:
            log(f"{name}: {type(e).__name__}, resuming")
        have = os.path.getsize(part) if os.path.exists(part) else 0
        time.sleep(3)
    return False


def main():
    os.makedirs(OUT, exist_ok=True)
    log("starting medium.en download")
    for name in FILES:
        if not download(name):
            log(f"{name}: giving up for now")
    ok = all(os.path.exists(os.path.join(OUT, n))
             for n in FILES if n != "model.bin")
    model_ok = os.path.exists(os.path.join(OUT, "model.bin"))
    log(f"finished. small files ok={ok} model.bin={model_ok}")


if __name__ == "__main__":
    sys.exit(main())
