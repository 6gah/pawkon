# 🐾 pawkon

konachan wallpaper manager for your terminal. built for hyprland + swww + pywal setups because i got tired of opening a browser every time i wanted a new wallpaper.

> **note:** this pulls from konachan. default rating is safe but you can cycle it. you know what you're doing.

## what it does

- browse konachan from your terminal with a cozy little TUI
- filter by tags, resolution, rating
- hit enter and your wallpaper changes + pywal recolors everything
- the TUI itself recolors live when wal updates — no restart needed
- save wallpapers you like, blacklist tags you don't
- idle auto-rotation if you want your desktop to change itself
- kitty icat preview
- random wallpaper with `x` for when you can't decide

## deps

**need these:**
- [`swww`](https://github.com/LGFae/swww)
- [`kitty`](https://sw.kovidgoyal.net/kitty/)
- python 3.10+

**makes it better:**
- [`pywal`](https://github.com/dylanaraps/pywal) / [`cwal`](https://github.com/nicowillis/cwal) — the whole point tbh
- [`figlet`](http://www.figlet.org/) + a cool font, or `pip install pyfiglet` — startup logo
- `waybar` — gets a SIGUSR2 so it reloads colors too

no pip deps otherwise, pure stdlib.

## install

```bash
git clone https://github.com/yourusername/pawkon
cd pawkon
chmod +x pawkon.py
./pawkon.py
```

opens in a new kitty window automatically.

## keys

| key | thing |
|-----|-------|
| `↑↓` / `jk` | move |
| `enter` | set wallpaper |
| `i` | preview (icat) |
| `s` | save post to disk + saved tab |
| `S` | save whatever's currently on your desktop |
| `d` | remove from saved tab |
| `o` | open post in browser |
| `x` | random wallpaper |
| `f` | flip between browse / saved tab |
| `r` | refetch |
| `t` | change tags |
| `b` | blacklist a tag |
| `R` | cycle rating (s → q → e) |
| `T` | cycle swww transition |
| `Z` | cycle resolution (1080p → 1440p → 4K) |
| `c` | cycle sort (score → date → random) |
| `I` | idle auto-rotate interval |
| `w` | toggle pywal |
| `W` | force reload wal colors |
| `p / P` | next / prev page |
| `q` | quit |
| `?` | keybind help |

## config

top of `pawkon.py`:

```python
CONFIG = {
    "tags":                "scenic",
    "rating":              "s",      # s, q, e
    "limit":               20,
    "transition":          "wipe",
    "transition_duration": "1",
    "use_pywal":           True,
    "sort":                "score",
    "idle_minutes":        0,
    "save_dir":            "~/Pictures/wallpapers/pawkon",
    "tmp_dir":             "/tmp/pawkon",
}
```

tags, rating, sort, resolution and idle interval persist automatically to `~/.cache/pawkon/state.json`.
