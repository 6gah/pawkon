#!/usr/bin/env python3
"""
pawkon.py — Konachan TUI wallpaper manager
Features: startup animation, resolution toggle, random mode, idle wallpaper,
          blacklist, open-in-browser, sort, saved tab, save current wallpaper,
          live pywal color reloading
"""
import curses
import subprocess
import urllib.request
import urllib.parse
import json
import os
import random as _random
import threading
import time
import sys
import tty
import termios

# ─── CONFIG ──────────────────────────────────────────────────────────────────
CONFIG = {
    "tags":               "scenic",
    "rating":             "s",
    "limit":              20,
    "transition":         "wipe",
    "transition_duration":"1",
    "use_pywal":          True,
    "sort":               "score",
    "idle_minutes":       0,
    "save_dir":           os.path.expanduser("~/Pictures/wallpapers/pawkon"),
    "tmp_dir":            "/tmp/pawkon",
}

RES_PRESETS = [
    (1920, 1080, "1080p"),
    (2560, 1440, "1440p"),
    (3840, 2160, "4K"),
]
RES_IDX      = 0
RATING_CYCLE = ["s", "q", "e"]
RATING_LABEL = {"s": "safe", "q": "questionable", "e": "explicit"}
TRANSITIONS  = ["wipe", "fade", "slide", "grow", "outer", "random"]
SORT_CYCLE   = ["score", "date", "random"]

API_BASE       = "https://konachan.com/post.json"
STATE_FILE     = os.path.expanduser("~/.cache/pawkon/state.json")
BLACKLIST_FILE = os.path.expanduser("~/.cache/pawkon/blacklist.json")
SAVED_FILE     = os.path.expanduser("~/.cache/pawkon/saved.json")

# ─── STATE ───────────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        for k in ("tags", "rating", "sort", "idle_minutes"):
            if k in s:
                CONFIG[k] = s[k]
        global RES_IDX
        if "res_idx" in s:
            RES_IDX = s["res_idx"]
    except Exception:
        pass

def save_state():
    global RES_IDX
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(
                {k: CONFIG[k] for k in ("tags", "rating", "sort", "idle_minutes")}
                | {"res_idx": RES_IDX}, f
            )
    except Exception:
        pass

def load_blacklist():
    try:
        with open(BLACKLIST_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_blacklist(bl):
    os.makedirs(os.path.dirname(BLACKLIST_FILE), exist_ok=True)
    try:
        with open(BLACKLIST_FILE, "w") as f:
            json.dump(list(bl), f)
    except Exception:
        pass

# ─── SAVED LIST ──────────────────────────────────────────────────────────────
def load_saved():
    """Returns list of dicts: {path, id, width, height, tags, score, added}"""
    try:
        with open(SAVED_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def write_saved(saved_list):
    os.makedirs(os.path.dirname(SAVED_FILE), exist_ok=True)
    try:
        with open(SAVED_FILE, "w") as f:
            json.dump(saved_list, f, indent=2)
    except Exception:
        pass

def add_to_saved(saved_list, path, post=None):
    """Add a wallpaper path to saved list. Returns True if newly added."""
    if any(e["path"] == path for e in saved_list):
        return False
    entry = {
        "path":   path,
        "id":     str(post.get("id", "?")) if post else os.path.basename(path),
        "width":  post.get("width", "?")   if post else "?",
        "height": post.get("height", "?")  if post else "?",
        "tags":   post.get("tags", "")     if post else "",
        "score":  post.get("score", 0)     if post else 0,
        "added":  time.strftime("%Y-%m-%d %H:%M"),
    }
    saved_list.append(entry)
    write_saved(saved_list)
    return True

load_state()
BLACKLIST = load_blacklist()

# ─── API ─────────────────────────────────────────────────────────────────────
def fetch_posts(tags, rating, limit=20, page=1, sort="score"):
    w, h, _ = RES_PRESETS[RES_IDX]
    order  = f"order:{sort}" if sort in ("score", "date") else "order:random"
    query  = f"{tags} {order} rating:{rating} width:>={w} height:>={h}"
    params = urllib.parse.urlencode({"tags": query, "limit": limit, "page": page})
    url    = f"{API_BASE}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pawkon/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            posts = json.loads(r.read())
        if BLACKLIST:
            posts = [p for p in posts
                     if not any(bt in p.get("tags", "").split() for bt in BLACKLIST)]
        return posts
    except Exception:
        return []

def download_image(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "pawkon/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        f.write(r.read())

def set_wallpaper(path):
    tr  = CONFIG["transition"]
    dur = CONFIG["transition_duration"]
    subprocess.Popen(
        ["awww", "img", path, "--transition-type", tr, "--transition-duration", dur],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.makedirs(os.path.expanduser("~/.cache/wal"), exist_ok=True)
    open(os.path.expanduser("~/.cache/wal/wal"), "w").write(path)
    if CONFIG["use_pywal"]:
        subprocess.run(["cwal", "--img", path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["pkill", "-SIGUSR2", "waybar"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def get_current_wallpaper_path():
    try:
        with open(os.path.expanduser("~/.cache/wal/wal")) as f:
            return f.read().strip()
    except Exception:
        return None

# ─── IDLE THREAD ─────────────────────────────────────────────────────────────
_idle_stop = threading.Event()

def idle_worker(status_cb):
    """Auto-rotate wallpaper every idle_minutes. Wakes every minute to recheck config."""
    elapsed = 0
    while not _idle_stop.wait(60):
        mins = CONFIG.get("idle_minutes", 0)
        if mins <= 0:
            elapsed = 0
            continue
        elapsed += 1
        if elapsed < mins:
            continue
        elapsed = 0
        if _idle_stop.is_set():
            break
        posts = fetch_posts(CONFIG["tags"], CONFIG["rating"], limit=50, sort=CONFIG["sort"])
        if not posts:
            continue
        post = _random.choice(posts)
        url  = post.get("file_url") or post.get("sample_url", "")
        if not url:
            continue
        ext  = url.split(".")[-1].split("?")[0]
        dest = os.path.join(CONFIG["tmp_dir"], f"idle_{post['id']}.{ext}")
        try:
            if not os.path.exists(dest):
                download_image(url, dest)
            set_wallpaper(dest)
            status_cb(f"  ⏱ Auto-rotated → #{post['id']}")
        except Exception:
            pass

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def truncate(s, n):
    if n <= 1:
        return ""
    return s[:n - 1] + "…" if len(s) > n else s

def cp(n):
    return curses.color_pair(n)

def read_wal_colors():
    path = os.path.expanduser("~/.cache/wal/colors.json")
    try:
        with open(path) as f:
            data = json.load(f)
        colors = []
        for key in sorted(data["colors"].keys()):
            hexval = data["colors"][key].lstrip("#")
            r = int(hexval[0:2], 16)
            g = int(hexval[2:4], 16)
            b = int(hexval[4:6], 16)
            colors.append((r * 1000 // 255, g * 1000 // 255, b * 1000 // 255))
        return colors
    except Exception:
        return None

def wait_key():
    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

# ─── STARTUP ANIMATION ───────────────────────────────────────────────────────
LOGO = [
    " ██▓███   ▄▄▄       █     █░██ ▄█▀ ▒█████   ███▄    █ ",
    "▓██░  ██▒▒████▄    ▓█░ █ ░█░██▄█▒ ▒██▒  ██▒ ██ ▀█   █ ",
    "▓██░ ██▓▒▒██  ▀█▄  ▒█░ █ ░█▓███▄░ ▒██░  ██▒▓██  ▀█ ██▒",
    "▒██▄█▓▒ ▒░██▄▄▄▄██ ░█░ █ ░█▓██ █▄ ▒██   ██░▓██▒  ▐▌██▒",
    "▒██▒ ░  ░ ▓█   ▓██▒░░██▒██▓▒██▒ █▄░ ████▓▒░▒██░   ▓██░",
    "▒▓▒░ ░  ░ ▒▒   ▓▒█░░ ▓░▒ ▒ ▒ ▒▒ ▓▒░ ▒░▒░▒░ ░ ▒░   ▒ ▒ ",
    "░▒ ░       ▒   ▒▒ ░  ▒ ░ ░ ░ ░▒ ▒░  ░ ▒ ▒░ ░ ░░   ░ ▒░",
    "░░         ░   ▒     ░   ░ ░ ░░ ░ ░ ░ ░ ▒     ░   ░ ░ ",
    "               ░  ░    ░   ░  ░       ░ ░           ░ ",
    "",
    "          konachan wallpaper manager  🐾                             ",
]

def print_logo_ansi():
    try:
        colors = open(os.path.expanduser("~/.cache/wal/colors")).read().splitlines()
        def hex_to_rgb(hx):
            hx = hx.lstrip("#")
            return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
        c1 = hex_to_rgb(colors[1])
        c2 = hex_to_rgb(colors[4])
    except Exception:
        c1, c2 = (200, 200, 255), (100, 100, 200)

    try:
        result = subprocess.run(
            ["figlet", "-f",
             os.path.expanduser("~/.local/share/figlet-fonts/xero/Delta Corps Priest 1.flf"),
             "PAWKON"],
            capture_output=True, text=True,
        )
        art = result.stdout if result.returncode == 0 else None
    except Exception:
        art = None

    if not art:
        try:
            import pyfiglet
            art = pyfiglet.figlet_format("PAWKON", font="bloody")
        except Exception:
            art = "PAWKON\n"

    import shutil
    term_w = shutil.get_terminal_size().columns
    term_h = shutil.get_terminal_size().lines
    lines  = art.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()

    art_h    = len(lines)
    art_w    = max(len(l) for l in lines) if lines else 0
    pad_top  = max(0, (term_h - art_h - 4) // 2)
    pad_left = max(0, (term_w - art_w) // 2)

    print("\n" * pad_top)
    for line in lines:
        n   = max(1, len(line))
        out = " " * pad_left
        for j, ch in enumerate(line):
            t = j / n
            r = int(c1[0] + t * (c2[0] - c1[0]))
            g = int(c1[1] + t * (c2[1] - c1[1]))
            b = int(c1[2] + t * (c2[2] - c1[2]))
            out += f"\x1b[38;2;{r};{g};{b}m{ch}"
        print(out + "\x1b[0m")

    subtitle = "konachan wallpaper manager  🐾"
    sub_pad  = " " * max(0, (term_w - len(subtitle)) // 2)
    n   = max(1, len(subtitle))
    out = sub_pad
    for j, ch in enumerate(subtitle):
        t = j / n
        r = int(c1[0] + t * (c2[0] - c1[0]))
        g = int(c1[1] + t * (c2[1] - c1[1]))
        b = int(c1[2] + t * (c2[2] - c1[2]))
        out += f"\x1b[38;2;{r};{g};{b}m{ch}"
    print(out + "\x1b[0m")
    print()

    dot_pad = " " * max(0, (term_w - 20) // 2)
    for dots in range(6):
        print(
            f"\r{dot_pad}\x1b[38;2;{c1[0]};{c1[1]};{c1[2]}m"
            f" fetching{'.' * (dots % 4 + 1)}   \x1b[0m",
            end="", flush=True,
        )
        time.sleep(0.12)
    print()

# ─── APP ─────────────────────────────────────────────────────────────────────
TAB_BROWSE = 0
TAB_SAVED  = 1

class App:
    def __init__(self, stdscr):
        self.scr       = stdscr
        self.posts     = []
        self.selected  = 0
        self.page      = 1
        self.status    = "Loading…"
        self.loading   = False
        self.tab       = TAB_BROWSE
        self.saved     = load_saved()
        self.saved_sel = 0
        self.show_help = False
        self._status_lock = threading.Lock()
        self._init_colors()
        curses.curs_set(0)
        self.scr.timeout(200)
        os.makedirs(CONFIG["tmp_dir"], exist_ok=True)
        os.makedirs(CONFIG["save_dir"], exist_ok=True)

    def _set_status(self, s):
        with self._status_lock:
            self.status = s

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        wal = read_wal_colors()
        if wal and curses.can_change_color() and curses.COLORS >= 256:
            try:
                for i, (r, g, b) in enumerate(wal[:8]):
                    curses.init_color(16 + i, r, g, b)
                curses.init_pair(1, 16, -1)               # header
                curses.init_pair(2, 17, 16)               # selected row
                curses.init_pair(3, 18, -1)               # status ok
                curses.init_pair(4, 20, -1)               # dim / loading
                curses.init_pair(5, 21, -1)               # info bar
                curses.init_pair(6, curses.COLOR_RED, -1) # error / missing
                curses.init_pair(7, 16, 17)               # help bar / active tab
                curses.init_pair(8, 19, -1)               # saved accent
                return
            except Exception:
                pass
        # fallback to basic terminal colors
        curses.init_pair(1, curses.COLOR_CYAN,    -1)
        curses.init_pair(2, curses.COLOR_BLACK,   curses.COLOR_CYAN)
        curses.init_pair(3, curses.COLOR_GREEN,   -1)
        curses.init_pair(4, curses.COLOR_YELLOW,  -1)
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)
        curses.init_pair(6, curses.COLOR_RED,     -1)
        curses.init_pair(7, curses.COLOR_WHITE,   curses.COLOR_BLUE)
        curses.init_pair(8, curses.COLOR_GREEN,   -1)

    def reload_wal_colors(self):
        """Re-read pywal colors and reinitialize curses color pairs."""
        self._init_colors()
        self._set_status("  🎨 Reloaded wal colors.")

    def _watch_wal_colors(self):
        """Background thread: reload colors whenever colors.json changes."""
        path       = os.path.expanduser("~/.cache/wal/colors.json")
        last_mtime = None
        while True:
            time.sleep(2)
            try:
                mtime = os.path.getmtime(path)
                if last_mtime is not None and mtime != last_mtime:
                    self.reload_wal_colors()
                last_mtime = mtime
            except Exception:
                pass

    # ── fetch ────────────────────────────────────────────────────────────────
    def fetch(self):
        self.loading = True
        _, _, lbl    = RES_PRESETS[RES_IDX]
        self.status  = f"  Fetching — {CONFIG['tags']} [{lbl}] [{CONFIG['sort']}]…"

        def _work():
            posts = fetch_posts(
                CONFIG["tags"], CONFIG["rating"],
                CONFIG["limit"], self.page, CONFIG["sort"],
            )
            self.posts    = posts
            self.selected = 0
            _, _, lbl2    = RES_PRESETS[RES_IDX]
            if posts:
                self.status = (
                    f"  {len(posts)} results — p{self.page}"
                    f"  [{CONFIG['tags']}  {RATING_LABEL[CONFIG['rating']]}"
                    f"  {lbl2}  order:{CONFIG['sort']}]"
                )
            else:
                self.status = "  No results. Try different tags (t) or rating (R)."
            self.loading = False

        threading.Thread(target=_work, daemon=True).start()

    def do_random(self):
        self._set_status("  🎲 Picking random wallpaper…")

        def _work():
            posts = fetch_posts(CONFIG["tags"], CONFIG["rating"], limit=50, sort="random")
            if not posts:
                self._set_status("  No results for random.")
                return
            post = _random.choice(posts)
            url  = post.get("file_url") or post.get("sample_url", "")
            if not url:
                self._set_status("  No URL.")
                return
            ext  = url.split(".")[-1].split("?")[0]
            dest = os.path.join(CONFIG["tmp_dir"], f"rand_{post['id']}.{ext}")
            try:
                if not os.path.exists(dest):
                    download_image(url, dest)
                set_wallpaper(dest)
                self._set_status(
                    f"  🎲 Set random #{post['id']}  [{post['width']}×{post['height']}]"
                )
            except Exception as e:
                self._set_status(f"  ✗ {e}")

        threading.Thread(target=_work, daemon=True).start()

    # ── drawing ──────────────────────────────────────────────────────────────
    def draw_tabs(self, w):
        browse_label = " 🌐 browse "
        saved_label  = f" 🌸 saved ({len(self.saved)}) "
        try:
            self.scr.addstr(0, 0, " " * (w - 1), cp(4))
        except curses.error:
            pass
        battr = (cp(7) | curses.A_BOLD) if self.tab == TAB_BROWSE else cp(1)
        sattr = (cp(7) | curses.A_BOLD) if self.tab == TAB_SAVED  else cp(8)
        try:
            self.scr.addstr(0, 1, browse_label, battr)
            self.scr.addstr(0, 1 + len(browse_label) + 1, saved_label, sattr)
        except curses.error:
            pass
        hint = " [f] switch "
        try:
            self.scr.addstr(0, w - len(hint) - 1, hint, cp(4))
        except curses.error:
            pass

    def draw_header(self, w):
        _, _, lbl = RES_PRESETS[RES_IDX]
        if self.tab == TAB_BROWSE:
            title = f" 🐾 pawkon — {CONFIG['tags']} [{lbl}] "
        else:
            title = f" 🌸 saved wallpapers — {len(self.saved)} entries "
        self.scr.attron(cp(1) | curses.A_BOLD)
        try:
            self.scr.addstr(1, 0, "─" * (w - 1))
            self.scr.addstr(1, max(0, (w - len(title)) // 2), title)
        except curses.error:
            pass
        self.scr.attroff(cp(1) | curses.A_BOLD)

    def draw_status(self, w):
        spin   = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        prefix = spin[int(time.time() * 8) % len(spin)] + " " if self.loading else ""
        line   = prefix + self.status
        attr   = cp(3) if not self.loading else cp(4)
        self.scr.attron(attr)
        try:
            self.scr.addstr(2, 0, truncate(line, w - 1).ljust(w - 1))
        except curses.error:
            pass
        self.scr.attroff(attr)

    def draw_list(self, h, w):
        if not self.posts:
            return
        list_h = h - 6
        start  = max(0, self.selected - list_h // 2)
        for i, post in enumerate(self.posts[start:start + list_h]):
            idx        = i + start
            y          = 3 + i
            if y >= h - 3:
                break
            num        = f"{idx + 1:>3}"
            res        = f"{post.get('width', '?')}×{post.get('height', '?')}"
            score      = post.get("score", 0)
            rating     = post.get("rating", "?")
            tag_budget = max(10, w - 38)
            tags_short = truncate(post.get("tags", ""), tag_budget)
            row = truncate(
                f" {num}  {res:<13} ★{score:<5} [{rating}]  {tags_short}", w - 1
            )
            try:
                if idx == self.selected:
                    self.scr.attron(cp(2) | curses.A_BOLD)
                    self.scr.addstr(y, 0, row.ljust(w - 1))
                    self.scr.attroff(cp(2) | curses.A_BOLD)
                elif idx % 2 == 0:
                    self.scr.attron(curses.A_DIM)
                    self.scr.addstr(y, 0, row)
                    self.scr.attroff(curses.A_DIM)
                else:
                    self.scr.addstr(y, 0, row)
            except curses.error:
                pass

    def draw_saved_tab(self, h, w):
        if not self.saved:
            try:
                self.scr.addstr(
                    4, 2,
                    "No saved wallpapers yet.  Browse tab: press 's' to save hovered, "
                    "'S' to save active wallpaper.",
                    cp(4),
                )
            except curses.error:
                pass
            return
        list_h = h - 6
        start  = max(0, self.saved_sel - list_h // 2)
        for i, entry in enumerate(self.saved[start:start + list_h]):
            idx        = i + start
            y          = 3 + i
            if y >= h - 3:
                break
            num        = f"{idx + 1:>3}"
            res        = f"{entry.get('width', '?')}×{entry.get('height', '?')}"
            score      = entry.get("score", 0)
            added      = entry.get("added", "")
            exists     = os.path.exists(entry.get("path", ""))
            tag_budget = max(10, w - 52)
            tags_short = truncate(entry.get("tags", ""), tag_budget)
            missing    = " ✗" if not exists else ""
            row = truncate(
                f" {num}  {res:<13} ★{score:<5}  {added}  {tags_short}{missing}", w - 1
            )
            try:
                if idx == self.saved_sel:
                    self.scr.attron(cp(2) | curses.A_BOLD)
                    self.scr.addstr(y, 0, row.ljust(w - 1))
                    self.scr.attroff(cp(2) | curses.A_BOLD)
                elif not exists:
                    self.scr.attron(cp(6) | curses.A_DIM)
                    self.scr.addstr(y, 0, row)
                    self.scr.attroff(cp(6) | curses.A_DIM)
                elif idx % 2 == 0:
                    self.scr.attron(curses.A_DIM)
                    self.scr.addstr(y, 0, row)
                    self.scr.attroff(curses.A_DIM)
                else:
                    self.scr.addstr(y, 0, row)
            except curses.error:
                pass

    def draw_help_bar(self, h, w):
        if self.tab == TAB_BROWSE:
            bar = (" ↑↓/jk  Enter:set  i:preview  s:save  S:save active  "
                   "o:browser  x:random  f:saved  q:quit  ?:help ")
        else:
            bar = " ↑↓/jk  Enter:set  i:preview  d:delete  S:save active  f:browse  q:quit "
        self.scr.attron(cp(7))
        try:
            self.scr.addstr(h - 2, 0, truncate(bar, w - 1).ljust(w - 1))
        except curses.error:
            pass
        self.scr.attroff(cp(7))
        _, _, lbl = RES_PRESETS[RES_IDX]
        idle_s = f"{CONFIG['idle_minutes']}m" if CONFIG["idle_minutes"] > 0 else "off"
        info = (
            f" wal:{'ON' if CONFIG['use_pywal'] else 'OFF'}"
            f"  {CONFIG['transition']}  {CONFIG['rating']}"
            f"  {lbl}  sort:{CONFIG['sort']}  idle:{idle_s} "
        )
        self.scr.attron(cp(5))
        try:
            self.scr.addstr(h - 1, 0, truncate(info, w - 1))
        except curses.error:
            pass
        self.scr.attroff(cp(5))

    def draw_help_overlay(self):
        h, w = self.scr.getmaxyx()
        ALL_KEYS = [
            ("↑ / k",  "Move up"),
            ("↓ / j",  "Move down"),
            ("Enter",  "Set as wallpaper"),
            ("i",      "Preview (kitty icat)"),
            ("s",      "Save hovered post → disk + saved tab"),
            ("S",      "Save currently active wallpaper → saved tab"),
            ("d",      "Delete from saved tab (saved tab only)"),
            ("o",      "Open post in browser"),
            ("x",      "Random wallpaper"),
            ("f",      "Toggle browse / saved tab"),
            ("r",      "Refresh / re-fetch"),
            ("t",      "Change tags"),
            ("b",      "Blacklist a tag"),
            ("R",      "Cycle rating  s→q→e"),
            ("T",      "Cycle awww transition"),
            ("Z",      "Cycle resolution"),
            ("c",      "Cycle sort  score→date→random"),
            ("I",      "Set idle auto-rotate minutes"),
            ("w",      "Toggle pywal"),
            ("W",      "Force reload wal colors"),
            ("p / P",  "Next / prev page"),
            ("q",      "Quit"),
            ("?",      "Toggle this help"),
        ]
        box_w = 54
        box_h = len(ALL_KEYS) + 4
        bx    = max(0, (w - box_w) // 2)
        by    = max(0, (h - box_h) // 2)
        for y in range(by, min(by + box_h, h - 1)):
            try:
                self.scr.addstr(y, bx, " " * min(box_w, w - bx), cp(7))
            except curses.error:
                pass
        self.scr.attron(cp(1) | curses.A_BOLD)
        try:
            self.scr.addstr(by, bx, "─" * min(box_w, w - bx - 1))
            self.scr.addstr(by + box_h - 1, bx, "─" * min(box_w, w - bx - 1))
            title = " 🐾 keybinds "
            self.scr.addstr(by, bx + max(0, (box_w - len(title)) // 2), title)
        except curses.error:
            pass
        self.scr.attroff(cp(1) | curses.A_BOLD)
        for i, (key, desc) in enumerate(ALL_KEYS):
            y = by + 2 + i
            if y >= by + box_h - 1 or y >= h - 1:
                break
            try:
                self.scr.addstr(y, bx + 2,  f"{key:<13}", cp(2) | curses.A_BOLD)
                self.scr.addstr(y, bx + 16, truncate(desc, box_w - 18), cp(7))
            except curses.error:
                pass
        try:
            self.scr.addstr(by + box_h - 1, bx, " press ? to close ", cp(1) | curses.A_BOLD)
        except curses.error:
            pass

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < 8 or w < 40:
            self.scr.addstr(0, 0, "Terminal too small!")
            self.scr.refresh()
            return
        self.draw_tabs(w)
        self.draw_header(w)
        self.draw_status(w)
        if self.tab == TAB_BROWSE:
            if self.posts:
                self.draw_list(h, w)
            elif not self.loading:
                try:
                    self.scr.addstr(4, 2, "No posts. Press 'r' to fetch.", cp(6))
                except curses.error:
                    pass
        else:
            self.draw_saved_tab(h, w)
        self.draw_help_bar(h, w)
        if self.show_help:
            self.draw_help_overlay()
        self.scr.refresh()

    # ── prompt ───────────────────────────────────────────────────────────────
    def prompt(self, label, prefill=""):
        h, w = self.scr.getmaxyx()
        curses.curs_set(1)
        buf = list(prefill)
        while True:
            try:
                self.scr.addstr(h - 3, 0, " " * (w - 1), cp(1))
                self.scr.addstr(h - 3, 0, f" {label}: {''.join(buf)}", cp(1) | curses.A_BOLD)
            except curses.error:
                pass
            self.scr.refresh()
            ch = self.scr.getch()
            if ch in (10, 13):
                break
            elif ch == 27:
                buf = list(prefill)
                break
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif 32 <= ch < 127:
                buf.append(chr(ch))
        curses.curs_set(0)
        return "".join(buf).strip()

    # ── actions ──────────────────────────────────────────────────────────────
    def _cur_post(self):
        if self.posts and self.selected < len(self.posts):
            return self.posts[self.selected]
        return None

    def _cur_saved(self):
        if self.saved and self.saved_sel < len(self.saved):
            return self.saved[self.saved_sel]
        return None

    def do_preview(self):
        if self.tab == TAB_SAVED:
            entry = self._cur_saved()
            if not entry:
                return
            path = entry.get("path", "")
            if not os.path.exists(path):
                self.status = "  ✗ File missing."
                return
            curses.endwin()
            print(f"\n  saved: {os.path.basename(path)}  "
                  f"{entry.get('width')}×{entry.get('height')}\n")
            subprocess.run(["kitty", "+kitten", "icat", "--scale-up", path],
                           stdout=sys.stdout)
            print("\n  Press any key to return…")
            wait_key()
            self.scr.refresh()
            curses.doupdate()
            self.status = "  Back."
            return

        post = self._cur_post()
        if not post:
            return
        url = post.get("sample_url") or post.get("file_url", "")
        if not url:
            self.status = "  No preview URL."
            return
        ext  = url.split(".")[-1].split("?")[0]
        dest = os.path.join(CONFIG["tmp_dir"], f"prev_{post['id']}.{ext}")
        self._set_status("  Downloading preview…")
        self.draw()
        try:
            if not os.path.exists(dest):
                download_image(url, dest)
        except Exception as e:
            self.status = f"  ✗ {e}"
            return
        curses.endwin()
        print(f"\n  #{post['id']}  {post.get('width')}×{post.get('height')}"
              f"  ★{post.get('score', 0)}\n")
        subprocess.run(["kitty", "+kitten", "icat", "--scale-up", dest],
                       stdout=sys.stdout)
        print("\n  Press any key to return…")
        wait_key()
        self.scr.refresh()
        curses.doupdate()
        self.status = "  Back."

    def do_set_wallpaper(self):
        if self.tab == TAB_SAVED:
            entry = self._cur_saved()
            if not entry:
                return
            path = entry.get("path", "")
            if not os.path.exists(path):
                self.status = "  ✗ File missing — cannot set."
                return
            try:
                set_wallpaper(path)
                self.status = f"  ✓ Set: {os.path.basename(path)}"
            except Exception as e:
                self.status = f"  ✗ {e}"
            return

        post = self._cur_post()
        if not post:
            return
        url = post.get("file_url") or post.get("sample_url", "")
        if not url:
            self.status = "  No URL."
            return
        ext  = url.split(".")[-1].split("?")[0]
        dest = os.path.join(CONFIG["tmp_dir"], f"wall_{post['id']}.{ext}")
        self._set_status(f"  Downloading {post['id']}…")
        self.draw()
        try:
            if not os.path.exists(dest):
                download_image(url, dest)
            set_wallpaper(dest)
            self.status = f"  ✓ Set! [{post['width']}×{post['height']}]"
        except Exception as e:
            self.status = f"  ✗ {e}"

    def do_save(self):
        """Save the hovered browse post to disk + saved tab."""
        post = self._cur_post()
        if not post:
            return
        url = post.get("file_url") or post.get("sample_url", "")
        if not url:
            self.status = "  No URL."
            return
        ext  = url.split(".")[-1].split("?")[0]
        dest = os.path.join(CONFIG["save_dir"], f"{post['id']}.{ext}")
        self._set_status("  Saving…")
        self.draw()
        try:
            download_image(url, dest)
            added = add_to_saved(self.saved, dest, post)
            if added:
                self.status = f"  ✓ Saved → {dest}  (added to saved tab 🌸)"
            else:
                self.status = "  ✓ File saved (already in saved tab)"
        except Exception as e:
            self.status = f"  ✗ {e}"

    def do_save_current_wallpaper(self):
        """Save the currently active wallpaper into the saved tab.
        Copies from /tmp to save_dir first if needed."""
        path = get_current_wallpaper_path()
        if not path:
            self.status = "  ✗ Couldn't read active wallpaper path."
            return
        if not os.path.exists(path):
            self.status = f"  ✗ File not found: {path}"
            return
        if not path.startswith(CONFIG["save_dir"]):
            import shutil
            fname = os.path.basename(path)
            dest  = os.path.join(CONFIG["save_dir"], fname)
            if not os.path.exists(dest):
                try:
                    shutil.copy2(path, dest)
                except Exception as e:
                    self.status = f"  ✗ Copy failed: {e}"
                    return
            path = dest
        added = add_to_saved(self.saved, path)
        if added:
            self.status = (f"  🌸 Active wallpaper saved → saved tab"
                           f"  ({os.path.basename(path)})")
        else:
            self.status = "  Already in saved tab."

    def do_delete_saved(self):
        """Remove selected entry from saved tab (does not delete the file)."""
        if not self.saved:
            return
        entry = self._cur_saved()
        if not entry:
            return
        self.saved.remove(entry)
        write_saved(self.saved)
        self.saved_sel = max(0, min(self.saved_sel, len(self.saved) - 1))
        self.status = f"  Removed {os.path.basename(entry.get('path', '?'))} from saved tab."

    def do_open_browser(self):
        post = self._cur_post()
        if not post:
            return
        pid = post.get("id", "")
        url = f"https://konachan.com/post/show/{pid}"
        subprocess.Popen(["xdg-open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.status = f"  Opened #{pid} in browser."

    def do_blacklist(self):
        val = self.prompt("Blacklist tag (empty to list/remove)", "")
        if not val:
            if BLACKLIST:
                self.status = "  Blacklist: " + ", ".join(sorted(BLACKLIST))
            else:
                self.status = "  Blacklist is empty."
            return
        if val in BLACKLIST:
            BLACKLIST.discard(val)
            save_blacklist(BLACKLIST)
            self.status = f"  Removed '{val}' from blacklist."
        else:
            BLACKLIST.add(val)
            save_blacklist(BLACKLIST)
            self.status = f"  Added '{val}' to blacklist. Refetching…"
            self.fetch()

    def do_cycle_sort(self):
        idx = SORT_CYCLE.index(CONFIG["sort"]) if CONFIG["sort"] in SORT_CYCLE else 0
        CONFIG["sort"] = SORT_CYCLE[(idx + 1) % len(SORT_CYCLE)]
        save_state()
        self.page = 1
        self.fetch()

    def do_set_idle(self):
        val = self.prompt("Auto-rotate every N minutes (0=off)", str(CONFIG["idle_minutes"]))
        try:
            CONFIG["idle_minutes"] = max(0, int(val))
            save_state()
            self.status = (
                f"  Idle rotation: {CONFIG['idle_minutes']}m"
                if CONFIG["idle_minutes"] else "  Idle rotation off."
            )
        except Exception:
            self.status = "  Invalid number."

    # ── main loop ────────────────────────────────────────────────────────────
    def run(self):
        self.fetch()
        threading.Thread(target=self._watch_wal_colors, daemon=True).start()
        threading.Thread(target=idle_worker, args=(self._set_status,), daemon=True).start()

        while True:
            self.draw()
            key = self.scr.getch()
            if key == -1:
                continue

            # global
            if key == ord('?'):
                self.show_help = not self.show_help
            elif key in (ord('q'), ord('Q')):
                _idle_stop.set()
                break
            elif key == ord('f'):
                self.tab = TAB_SAVED if self.tab == TAB_BROWSE else TAB_BROWSE
                self.show_help = False

            # navigation
            elif key in (curses.KEY_UP, ord('k')):
                if self.tab == TAB_BROWSE and self.posts:
                    self.selected = max(0, self.selected - 1)
                elif self.tab == TAB_SAVED and self.saved:
                    self.saved_sel = max(0, self.saved_sel - 1)
            elif key in (curses.KEY_DOWN, ord('j')):
                if self.tab == TAB_BROWSE and self.posts:
                    self.selected = min(len(self.posts) - 1, self.selected + 1)
                elif self.tab == TAB_SAVED and self.saved:
                    self.saved_sel = min(len(self.saved) - 1, self.saved_sel + 1)

            # shared actions
            elif key in (10, 13):
                self.do_set_wallpaper()
            elif key == ord('i'):
                self.do_preview()
            elif key == ord('S'):
                self.do_save_current_wallpaper()

            # browse-only actions
            elif key == ord('s') and self.tab == TAB_BROWSE:
                self.do_save()
            elif key == ord('o') and self.tab == TAB_BROWSE:
                self.do_open_browser()
            elif key == ord('x') and self.tab == TAB_BROWSE:
                self.do_random()
            elif key == ord('b') and self.tab == TAB_BROWSE:
                self.do_blacklist()
            elif key == ord('r') and self.tab == TAB_BROWSE:
                self.fetch()
            elif key == ord('t') and self.tab == TAB_BROWSE:
                val = self.prompt("Tags", CONFIG["tags"])
                if val:
                    CONFIG["tags"] = val
                    save_state()
                    self.page = 1
                    self.fetch()
            elif key == ord('R') and self.tab == TAB_BROWSE:
                idx = RATING_CYCLE.index(CONFIG["rating"])
                CONFIG["rating"] = RATING_CYCLE[(idx + 1) % len(RATING_CYCLE)]
                save_state()
                self.page = 1
                self.fetch()
            elif key == ord('c') and self.tab == TAB_BROWSE:
                self.do_cycle_sort()
            elif key == ord('p') and self.tab == TAB_BROWSE:
                self.page += 1
                self.fetch()
            elif key == ord('P') and self.tab == TAB_BROWSE:
                self.page = max(1, self.page - 1)
                self.fetch()

            # saved-only actions
            elif key == ord('d') and self.tab == TAB_SAVED:
                self.do_delete_saved()

            # settings (always available)
            elif key == ord('T'):
                idx = TRANSITIONS.index(CONFIG["transition"]) if CONFIG["transition"] in TRANSITIONS else 0
                CONFIG["transition"] = TRANSITIONS[(idx + 1) % len(TRANSITIONS)]
                self.status = f"  Transition: {CONFIG['transition']}"
            elif key == ord('Z'):
                global RES_IDX
                RES_IDX = (RES_IDX + 1) % len(RES_PRESETS)
                save_state()
                self.page = 1
                self.fetch()
            elif key == ord('W'):
                self.reload_wal_colors()
            elif key == ord('w'):
                CONFIG["use_pywal"] = not CONFIG["use_pywal"]
                self.status = f"  pywal: {'ON' if CONFIG['use_pywal'] else 'OFF'}"
            elif key == ord('I'):
                self.do_set_idle()


def main():
    if os.environ.get("PAWKON_LAUNCHED") != "1":
        script = os.path.abspath(__file__)
        subprocess.Popen(
            ["kitty", "--title", "pawkon", "python3", script],
            env={**os.environ, "PAWKON_LAUNCHED": "1"},
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    os.environ.setdefault("TERM", "xterm-256color")
    print_logo_ansi()
    curses.wrapper(lambda stdscr: App(stdscr).run())


if __name__ == "__main__":
    main()