#!/usr/bin/env python3
"""One daily batch of the snapshot: hot list, then a small deep-check batch that resumes across days.

    build_snapshot.py --skill-repo DIR [--queue 30] [--max-words 12] [--max-deep-requests 252]

Runs from the repo root. Page cache and books go to cache/ (gitignored). Every Amazon request goes through the
skill's own fetch() (fixed UA, no proxy); deep checks run with the skill's --snapshot pacing (8–12 s between pages,
product pages cached 7 days) plus WORD_PAUSE_S between keywords. scripts/bin/curl on PATH only counts requests.

Queue = the top --queue topics by heat; a topic is due when it has no card or its card is older than CARD_MAX_AGE_DAYS.
A CAPTCHA, bot check or blocked list page stops the batch at once (the skill runs with --budget-seconds, so it defers
instead of sleeping and retrying); the topic it stopped on is still due, so the next day's batch starts there.
Cards accumulate in snapshot/cards/; manifest.json records each card's fetch date.

Prints one JSON status line on stdout. Exit 0 = snapshot/ rewritten (new_cards may be 0), 3 = stopped before the deep
check (blocked list page, CAPTCHA on the hot list, daily IP limit), reason in the status. Stdlib only, Python 3.9+.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "cache")
SNAPSHOT_DIR = os.path.join(ROOT, "snapshot")
CARDS_DIR = os.path.join(SNAPSHOT_DIR, "cards")
MANIFEST = os.path.join(SNAPSHOT_DIR, "manifest.json")
REQUEST_LOG = os.path.join(CACHE_DIR, "requests.log")
SKILL_SCRIPTS = "skills/clawcreek-howto-author/steps/01-niche-research/scripts"
SCHEMA = "kindle-niche-snapshot/0.1"
MIN_LIST_PAGE_BYTES = 100 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_REQUESTS_PER_KEYWORD = 21        # 1 search page + up to 20 product pages
DAILY_IP_LIMIT = 1200
WORD_PAUSE_S = 60
CARD_MAX_AGE_DAYS = 7                # a card is re-checked once a week
RUN_BUDGET_SECONDS = 4 * 3600        # only there to make the skill stop on a CAPTCHA instead of retrying
EXIT_DEFERRED = 75
CARD_FILES = ("card.md", "competitors.json")


class Stop(Exception):
    def __init__(self, reason, **detail):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def request_times():
    if not os.path.exists(REQUEST_LOG):
        return []
    with open(REQUEST_LOG) as f:
        return [datetime.strptime(line.split()[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for line in f if line.strip()]


def requests_last_24h():
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return sum(1 for t in request_times() if t >= since)


def slugify(keyword):
    return re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()


def load_manifest():
    if not os.path.exists(MANIFEST):
        return {}
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def run_skill(script, *args):
    """Run niche_research.py; raise Stop on CAPTCHA / bot check / blocked list page / budget deferral."""
    r = subprocess.run([sys.executable, script, *args, "--workdir", CACHE_DIR, "--budget-seconds", str(RUN_BUDGET_SECONDS)],
                       capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    if r.returncode == EXIT_DEFERRED:
        m = re.search(r"reason=(\S+)", last)
        raise Stop(m.group(1) if m else "deferred", command=args[0], stderr_tail=r.stderr[-800:])
    return r


def check_list_pages(scripts_dir):
    """Every Best Sellers / Hot New Releases page must be cached and at least 100 KB; smaller = blocked."""
    sys.path.insert(0, scripts_dir)
    import discover
    import topics
    discover.set_workdir(CACHE_DIR)
    pages = []
    for nid in topics.default_categories():
        for _, url in topics.list_urls(nid):
            path = discover.cache_key(url)
            pages.append((os.path.getsize(path) if os.path.exists(path) else 0, url, path))
    size, url, path = min(pages)
    if size < MIN_LIST_PAGE_BYTES:
        title = ""
        if size:
            with open(path, encoding="utf-8", errors="ignore") as f:
                m = re.search(r"<title[^>]*>(.*?)</title>", f.read(), re.S | re.I)
                title = m.group(1).strip() if m else ""
        raise Stop("list-page-too-small", url=url, bytes=size, title=title)
    return {"pages": len(pages), "smallest_bytes": size, "smallest_url": url}


def due_keywords(ranked, card_index, queue, today):
    """Top `queue` topics by heat whose card is missing or older than CARD_MAX_AGE_DAYS, in heat order."""
    out = []
    for t in ranked[:queue]:
        card = card_index.get(slugify(t["topic"]))
        if card and (today - date.fromisoformat(card["fetched_at"])).days < CARD_MAX_AGE_DAYS:
            continue
        out.append(t["topic"])
    return out


def deep_batch(script, keywords, card_index, a, t0, run_start_requests):
    """export due keywords one by one (with --snapshot pacing); copy non-empty results into snapshot/cards/."""
    batch = {"due": len(keywords), "attempted": [], "new_cards": [], "empty": [], "failed": [], "stopped": None}
    deep_start = len(request_times())
    for i, kw in enumerate(keywords):
        used = len(request_times()) - deep_start
        if len(batch["attempted"]) >= a.max_words:
            batch["stopped"] = {"reason": "daily-word-cap", "next": kw}
            break
        if used + MAX_REQUESTS_PER_KEYWORD > a.max_deep_requests or requests_last_24h() + MAX_REQUESTS_PER_KEYWORD >= DAILY_IP_LIMIT:
            batch["stopped"] = {"reason": "request-budget", "next": kw}
            break
        if i:
            time.sleep(WORD_PAUSE_S)
        slug = slugify(kw)
        book = os.path.join(CACHE_DIR, "books", slug)
        shutil.rmtree(os.path.join(book, "niche"), ignore_errors=True)   # never copy last week's card by mistake
        batch["attempted"].append(kw)
        try:
            r = run_skill(script, "export", kw, "--book", book, "--snapshot")
        except Stop as s:
            batch["stopped"] = {"reason": s.reason, "next": kw}
            break
        src = os.path.join(book, "niche")
        if r.returncode != 0 or not all(os.path.exists(os.path.join(src, n)) for n in CARD_FILES):
            batch["failed"].append({"keyword": kw, "rc": r.returncode, "stderr_tail": r.stderr[-300:]})
            continue
        with open(os.path.join(src, "competitors.json"), encoding="utf-8") as f:
            if not json.load(f):
                batch["empty"].append(kw)   # no search results: not a card
                continue
        dst = os.path.join(CARDS_DIR, slug)
        os.makedirs(dst, exist_ok=True)
        for n in CARD_FILES:
            shutil.copyfile(os.path.join(src, n), os.path.join(dst, n))
        card_index[slug] = {"keyword": kw, "fetched_at": date.today().isoformat()}
        batch["new_cards"].append(slug)
        log(f"card {len(batch['new_cards'])}: {kw} · requests this run {len(request_times()) - run_start_requests} · "
            f"{time.monotonic() - t0:.0f}s")
    batch["requests"] = len(request_times()) - deep_start
    return batch


def prune_orphan_cards(card_index):
    """Drop card directories the manifest does not know (e.g. cards from before the 1.0.3 topic algorithm)."""
    if not os.path.isdir(CARDS_DIR):
        return []
    orphans = [d for d in os.listdir(CARDS_DIR) if d not in card_index]
    for d in orphans:
        shutil.rmtree(os.path.join(CARDS_DIR, d))
    return orphans


def write_manifest(skill, card_index, run):
    prune_orphan_cards(card_index)
    shutil.copyfile(os.path.join(CACHE_DIR, "topics.json"), os.path.join(SNAPSHOT_DIR, "topics.json"))
    files = {}
    for dirpath, _, names in os.walk(SNAPSHOT_DIR):
        for n in sorted(names):
            path = os.path.join(dirpath, n)
            rel = os.path.relpath(path, SNAPSHOT_DIR)
            if rel == "manifest.json":
                continue
            if n.lower().endswith((".html", ".htm")) or os.path.getsize(path) > MAX_FILE_BYTES:
                raise Stop("forbidden-or-oversized-file", file=rel, bytes=os.path.getsize(path))
            files[rel] = sha256(path)
    with open(os.path.join(SNAPSHOT_DIR, "topics.json"), encoding="utf-8") as f:
        n_topics = len(json.load(f)["topics"])
    present = {s: c for s, c in card_index.items() if all(os.path.exists(os.path.join(CARDS_DIR, s, n)) for n in CARD_FILES)}
    manifest = {"schema": SCHEMA,
                "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "topics": n_topics, "cards": len(present), "skill": skill,
                "requests": run["requests"], "elapsed_seconds": run["elapsed_seconds"], "steps": run["steps"],
                "new_cards": run["new_cards"], "stopped": run["stopped"],
                "card_index": dict(sorted(present.items())), "files": dict(sorted(files.items()))}
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
        f.write("\n")
    return manifest


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--skill-repo", required=True)
    p.add_argument("--queue", type=int, default=30)
    p.add_argument("--max-words", type=int, default=12)
    p.add_argument("--max-deep-requests", type=int, default=12 * MAX_REQUESTS_PER_KEYWORD)
    a = p.parse_args()
    skill_repo = os.path.abspath(os.path.expanduser(a.skill_repo))
    scripts_dir = os.path.join(skill_repo, SKILL_SCRIPTS)
    script = os.path.join(scripts_dir, "niche_research.py")
    skill = {"repo": "netmind-rs-clawcreek-skills", "path": SKILL_SCRIPTS + "/niche_research.py",
             "branch": git(skill_repo, "rev-parse", "--abbrev-ref", "HEAD"), "commit": git(skill_repo, "rev-parse", "HEAD")}
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.environ["SNAPSHOT_REQUEST_LOG"] = REQUEST_LOG
    os.environ["PATH"] = os.path.join(ROOT, "scripts", "bin") + os.pathsep + os.environ["PATH"]
    card_index = dict(load_manifest().get("card_index", {}))
    start = len(request_times())
    t0 = time.monotonic()
    steps = {}
    try:
        if requests_last_24h() + 48 >= DAILY_IP_LIMIT:
            raise Stop("daily-ip-limit", last_24h=requests_last_24h())
        run_skill(script, "topics").check_returncode()   # list pages refetch only when older than the skill's 7-day TTL
        steps["topics"] = {"requests": len(request_times()) - start, "seconds": round(time.monotonic() - t0)}
        list_pages = check_list_pages(scripts_dir)
        with open(os.path.join(CACHE_DIR, "topics.json"), encoding="utf-8") as f:
            ranked = sorted(json.load(f)["topics"], key=lambda t: -t["heat"])
        keywords = due_keywords(ranked, card_index, a.queue, date.today())
        t1 = time.monotonic()
        batch = deep_batch(script, keywords, card_index, a, t0, start)
        steps["deep"] = {"words": len(batch["attempted"]), "requests": batch["requests"], "seconds": round(time.monotonic() - t1)}
        run = {"requests": len(request_times()) - start, "elapsed_seconds": round(time.monotonic() - t0), "steps": steps,
               "new_cards": batch["new_cards"], "stopped": batch["stopped"]}
        manifest = write_manifest(skill, card_index, run)
        status = {"ok": True, "topics": manifest["topics"], "cards": manifest["cards"], "new_cards": len(batch["new_cards"]),
                  "list_pages": list_pages, **{k: batch[k] for k in ("due", "empty", "failed", "stopped")},
                  "requests": run["requests"], "elapsed_seconds": run["elapsed_seconds"], "steps": steps}
    except (Stop, subprocess.CalledProcessError) as e:
        reason = e.reason if isinstance(e, Stop) else f"skill-exit-{e.returncode}"
        status = {"ok": False, "reason": reason, "detail": getattr(e, "detail", {}), "steps": steps,
                  "requests": len(request_times()) - start, "elapsed_seconds": round(time.monotonic() - t0)}
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
