"""
csi_viewer.py — Interactive CSI / multipath channel visualizer.

Left pane:  2-D room with draggable TX, RX, person; sliders for room
            size, reflectivity, wavelength, # paths.
Right pane: per-path phasors on complex plane + time-domain waveforms,
            plus the summed (composite) signal.
"""

import io
import os
import threading
import time

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.widgets import Slider, Button, TextBox
from matplotlib.lines import Line2D
import matplotlib.patheffects as pe
from PIL import Image

matplotlib.use("TkAgg")          # change to "Qt5Agg" if TkAgg unavailable

# ── colour palette ────────────────────────────────────────────────────────────
PATH_COLORS = ["#e74c3c", "#ff79c6", "#2ecc71", "#f39c12", "#9b59b6",
               "#1abc9c", "#e67e22", "#34495e"]
PERSON_PATH_IDX = 1     # slot always reserved for TX→person→RX
PERSON_SCATTER  = 0.4   # person scattering coefficient (vs wall reflectivity)
ROOM_COLOR  = "#1a1a2e"
WALL_COLOR  = "#e94560"
TX_COLOR    = "#f5a623"
RX_COLOR    = "#50fa7b"
PERSON_COLOR = "#8be9fd"
SUM_COLOR   = "#ff79c6"

# ── default parameters ────────────────────────────────────────────────────────
DEFAULTS = dict(
    room_size   = 10.0,   # metres
    reflectivity= 0.7,    # 0–1
    wavelength  = 0.125,  # metres  (~2.4 GHz WiFi)
    n_paths     = 4,      # 1–7 (direct + reflections)
)

T_POINTS = 500          # time-domain samples
T_CYCLES = 6            # how many cycles to display


# ═════════════════════════════════════════════════════════════════════════════
# Multipath physics
# ═════════════════════════════════════════════════════════════════════════════

def reflect_point(p, wall):
    """Mirror point p across wall ('left','right','top','bottom')."""
    x, y = p
    if wall == "left":   return (-x, y)
    if wall == "right":  return (2 * wall_x - x, y)   # handled via closure
    if wall == "bottom": return (x, -y)
    if wall == "top":    return (x, 2 * wall_y - y)
    return p


def build_paths(tx, rx, person_pos, room_size, n_paths, wavelength,
                reflectivity):
    """
    Return list of path dicts, each with:
      waypoints, length, amplitude, phase_rad, complex_amp,
      hits_person (bool), label.

    Path 0  = direct (TX→RX).
    Path 1  = TX→person→RX (always present, updates with person position).
    Paths 2+ = single/double-wall reflections to fill remaining slots.
    """
    L = room_size
    tx  = tuple(tx)
    rx  = tuple(rx)
    pp  = tuple(person_pos)
    walls = [
        ("left",        np.array([-tx[0], tx[1]])),
        ("right",       np.array([2*L - tx[0], tx[1]])),
        ("bottom",      np.array([tx[0], -tx[1]])),
        ("top",         np.array([tx[0], 2*L - tx[1]])),
        ("left+top",    np.array([-tx[0], 2*L - tx[1]])),
        ("right+bottom",np.array([2*L - tx[0], -tx[1]])),
        ("right+top",   np.array([2*L - tx[0], 2*L - tx[1]])),
    ]

    k = 2 * np.pi / wavelength
    paths = []

    # ── path 0: direct ───────────────────────────────────────────────────────
    d_direct = np.linalg.norm(np.array(rx) - np.array(tx))
    paths.append({
        "waypoints": [tx, rx],
        "length": d_direct,
        "amplitude": 1.0 / max(d_direct, 0.01),
        "phase_rad": k * d_direct,
        "hits_person": _segment_hits_person(tx, rx, pp, wavelength),
        "label": "Direct",
        "bounces": 0,
    })

    # ── path 1: TX → person → RX (guaranteed scatter path) ──────────────────
    if n_paths >= 2:
        d_p = (np.linalg.norm(np.array(pp) - np.array(tx))
               + np.linalg.norm(np.array(rx) - np.array(pp)))
        paths.append({
            "waypoints": [tx, pp, rx],
            "length": d_p,
            "amplitude": PERSON_SCATTER / max(d_p, 0.01),
            "phase_rad": k * d_p,
            "hits_person": True,
            "label": "Person scatter",
            "bounces": 1,
        })

    # ── paths 2+: wall reflections ───────────────────────────────────────────
    for name, img in walls:
        if len(paths) >= n_paths:
            break
        bounces = name.count("+") + 1
        refl_pts = _reflection_waypoints(img, np.array(rx), tx, name, L,
                                         bounces)
        if refl_pts is None:
            continue
        wpts = [tx] + refl_pts + [rx]
        d_total = sum(
            np.linalg.norm(np.array(wpts[j+1]) - np.array(wpts[j]))
            for j in range(len(wpts)-1))
        atten = (reflectivity ** bounces) / max(d_total, 0.01)
        hits = any(_segment_hits_person(wpts[j], wpts[j+1], pp, wavelength)
                   for j in range(len(wpts)-1))
        paths.append({
            "waypoints": wpts,
            "length": d_total,
            "amplitude": atten,
            "phase_rad": k * d_total,
            "hits_person": hits,
            "label": f"Refl {name}",
            "bounces": bounces,
        })

    # compute complex amplitudes
    for p in paths:
        p["complex_amp"] = p["amplitude"] * np.exp(1j * p["phase_rad"])

    return paths


def _reflection_waypoints(image_src, rx, tx_orig, wall_name, L, bounces):
    """
    Compute intermediate reflection points on wall(s).
    Returns list of 2-D points or None if invalid (outside room).
    """
    # Single-bounce: one reflection point = image→rx intersect wall
    if bounces == 1:
        wall = wall_name
        pt = _line_wall_intersect(image_src, rx, wall, L)
        if pt is None:
            return None
        if not (0 <= pt[0] <= L and 0 <= pt[1] <= L):
            return None
        return [pt]
    # Two-bounce: approximate — use midpoint heuristic
    if bounces == 2:
        w1, w2 = wall_name.split("+")
        img1 = _mirror(np.array(tx_orig), w1, L)
        img2 = _mirror(rx, w2, L)
        pt1 = _line_wall_intersect(img1, img2, w1, L)
        pt2 = _line_wall_intersect(img1, img2, w2, L)
        if pt1 is None or pt2 is None:
            return None
        if not (_in_room(pt1, L) and _in_room(pt2, L)):
            return None
        return [pt1, pt2]
    return None


def _mirror(p, wall, L):
    p = np.array(p, dtype=float)
    if wall == "left":   return np.array([-p[0], p[1]])
    if wall == "right":  return np.array([2*L - p[0], p[1]])
    if wall == "bottom": return np.array([p[0], -p[1]])
    if wall == "top":    return np.array([p[0], 2*L - p[1]])
    return p


def _line_wall_intersect(a, b, wall, L):
    """Parametric intersection of line segment a→b with a room wall."""
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    d = b - a
    eps = 1e-12
    if wall == "left":
        if abs(d[0]) < eps: return None
        t = (0 - a[0]) / d[0]
    elif wall == "right":
        if abs(d[0]) < eps: return None
        t = (L - a[0]) / d[0]
    elif wall == "bottom":
        if abs(d[1]) < eps: return None
        t = (0 - a[1]) / d[1]
    elif wall == "top":
        if abs(d[1]) < eps: return None
        t = (L - a[1]) / d[1]
    else:
        return None
    if t < 0 or t > 1:
        return None
    return a + t * d


def _in_room(pt, L):
    return 0 <= pt[0] <= L and 0 <= pt[1] <= L


def _segment_hits_person(a, b, person_pos, wavelength):
    """True if line segment a→b passes within ~half-wavelength of person."""
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    p = np.array(person_pos, dtype=float)
    ab = b - a
    len_ab = np.linalg.norm(ab)
    if len_ab < 1e-9:
        return False
    t = np.clip(np.dot(p - a, ab) / (len_ab ** 2), 0, 1)
    closest = a + t * ab
    return np.linalg.norm(p - closest) < max(wavelength * 0.5, 0.3)


# ═════════════════════════════════════════════════════════════════════════════
# Drawing helpers
# ═════════════════════════════════════════════════════════════════════════════

def draw_stick_person(ax, cx, cy, scale=0.5, color=PERSON_COLOR):
    """Draw a simple stick figure centred at (cx, cy)."""
    # head
    head = plt.Circle((cx, cy + scale*0.85), scale*0.18,
                       color=color, fill=True, zorder=5)
    ax.add_patch(head)
    # body
    ax.plot([cx, cx], [cy+scale*0.65, cy], color=color, lw=2, zorder=5)
    # arms
    ax.plot([cx-scale*0.35, cx+scale*0.35],
            [cy+scale*0.45, cy+scale*0.45], color=color, lw=2, zorder=5)
    # legs
    ax.plot([cx, cx-scale*0.3], [cy, cy-scale*0.5], color=color, lw=2,
            zorder=5)
    ax.plot([cx, cx+scale*0.3], [cy, cy-scale*0.5], color=color, lw=2,
            zorder=5)


def draw_room(ax, room_size, paths, tx, rx, person_pos, wavelength):
    ax.clear()
    L = room_size

    # background
    ax.set_facecolor(ROOM_COLOR)
    room_rect = mpatches.Rectangle((0, 0), L, L,
                                    linewidth=3, edgecolor=WALL_COLOR,
                                    facecolor=ROOM_COLOR, zorder=1)
    ax.add_patch(room_rect)

    # draw wave-front ripples from TX (concentric circles)
    for r in np.arange(wavelength, L*1.5, wavelength):
        c = plt.Circle(tx, r, color=TX_COLOR, fill=False,
                        alpha=max(0, 0.12 - r/(L*8)), lw=0.6, zorder=2)
        ax.add_patch(c)

    # draw paths
    for i, path in enumerate(paths):
        is_person_path = (i == PERSON_PATH_IDX
                          and path["label"] == "Person scatter")
        color = PATH_COLORS[i % len(PATH_COLORS)]
        wpts = path["waypoints"]
        xs = [w[0] for w in wpts]
        ys = [w[1] for w in wpts]
        if path["bounces"] == 0:
            lw, style = 2.0, "-"
        elif is_person_path:
            lw, style = 2.2, "-."
        else:
            lw, style = 1.4, "--"
        alpha = 1.0 if is_person_path else (
            0.9 if not path["hits_person"] else 1.0)
        ax.plot(xs, ys, color=color, lw=lw, ls=style, alpha=alpha,
                zorder=3, label=path["label"])
        # midpoint amplitude label
        mid = len(wpts) // 2
        mx = (wpts[mid-1][0] + wpts[mid][0]) / 2
        my = (wpts[mid-1][1] + wpts[mid][1]) / 2
        ax.text(mx, my, f"{path['amplitude']:.2f}",
                fontsize=6, color=color, alpha=0.8, zorder=6,
                ha="center", va="center",
                path_effects=[pe.withStroke(linewidth=1.5,
                                            foreground=ROOM_COLOR)])
        # scatter dots at intermediate waypoints
        for pt in wpts[1:-1]:
            marker = "*" if is_person_path else "o"
            ms     = 10  if is_person_path else 5
            ax.plot(pt[0], pt[1], marker, color=color, ms=ms, zorder=4)

        # ↯ marker on non-person paths that incidentally hit the person
        if path["hits_person"] and not is_person_path:
            mid_idx = len(wpts) // 2
            ax.annotate("↯", (wpts[mid_idx][0], wpts[mid_idx][1]),
                        fontsize=10, color=color, zorder=7, ha="center")

    # TX marker
    ax.plot(*tx, "*", ms=14, color=TX_COLOR, zorder=8,
            label="TX (source)")
    ax.text(tx[0]+0.15, tx[1]+0.15, "TX", color=TX_COLOR, fontsize=8,
            zorder=9)

    # RX marker
    ax.plot(*rx, "D", ms=10, color=RX_COLOR, zorder=8,
            label="RX (receiver)")
    ax.text(rx[0]+0.15, rx[1]+0.15, "RX", color=RX_COLOR, fontsize=8,
            zorder=9)

    # stick person — always has a scatter path, draw with glow ring
    px, py = person_pos
    scale = L * 0.07
    glow = plt.Circle((px, py + scale*0.85), scale*0.32,
                       color=PATH_COLORS[PERSON_PATH_IDX],
                       fill=False, lw=2.5, alpha=0.55, zorder=4,
                       linestyle="-.")
    ax.add_patch(glow)
    draw_stick_person(ax, px, py, scale=scale)

    ax.set_xlim(-0.3, L+0.3)
    ax.set_ylim(-0.3, L+0.3)
    ax.set_aspect("equal")
    ax.set_title("2-D Room — Multipath Channel", color="white", fontsize=10)
    ax.tick_params(colors="white")
    for sp in ax.spines.values():
        sp.set_edgecolor(WALL_COLOR)

    # legend
    handles = []
    for i, path in enumerate(paths):
        is_ps = (i == PERSON_PATH_IDX and path["label"] == "Person scatter")
        ls = "-." if is_ps else (
             "-" if path["bounces"] == 0 else "--")
        handles.append(Line2D([0],[0], color=PATH_COLORS[i % len(PATH_COLORS)],
                              lw=2, ls=ls, label=path["label"]))
    handles += [
        Line2D([0],[0], marker="*", color=TX_COLOR, lw=0, ms=9,
               label="TX"),
        Line2D([0],[0], marker="D", color=RX_COLOR, lw=0, ms=7,
               label="RX"),
        Line2D([0],[0], marker="o", color=PERSON_COLOR, lw=0, ms=7,
               label="Person (always scatters)"),
    ]
    ax.legend(handles=handles, loc="upper right", fontsize=6,
              framealpha=0.4, labelcolor="white",
              facecolor=ROOM_COLOR)

    # grid
    ax.grid(True, color="#ffffff11", lw=0.5)
    ax.set_xlabel("x (m)", color="white", fontsize=8)
    ax.set_ylabel("y (m)", color="white", fontsize=8)


def draw_signal_plots(axs, paths, wavelength):
    """
    axs: flat list of axes for [phasors, time-domain, sum-phasor, sum-time].
    axs[0] = per-path phasor (complex plane)
    axs[1] = per-path time-domain
    axs[2] = summed phasor
    axs[3] = summed time-domain
    """
    t = np.linspace(0, T_CYCLES * wavelength, T_POINTS)
    k = 2 * np.pi / wavelength

    # ── per-path phasor ──────────────────────────────────────────────────────
    ax = axs[0]
    ax.clear()
    ax.set_facecolor(ROOM_COLOR)
    ax.axhline(0, color="#ffffff33", lw=0.7)
    ax.axvline(0, color="#ffffff33", lw=0.7)
    max_amp = max(p["amplitude"] for p in paths) if paths else 1.0
    for i, path in enumerate(paths):
        c = path["complex_amp"]
        color = PATH_COLORS[i % len(PATH_COLORS)]
        ax.annotate("", xy=(c.real, c.imag), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="->", color=color, lw=1.8))
        ax.plot(c.real, c.imag, "o", color=color, ms=5)
        lbl = f"P{i}: φ={np.degrees(path['phase_rad'])%360:.0f}°"
        ax.text(c.real*1.05, c.imag*1.05, lbl,
                color=color, fontsize=6, ha="center")
    lim = max_amp * 1.3 + 0.01
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.set_title("Per-path Phasors", color="white", fontsize=8)
    ax.tick_params(colors="white", labelsize=6)
    ax.set_xlabel("Re", color="white", fontsize=7)
    ax.set_ylabel("Im", color="white", fontsize=7)
    for sp in ax.spines.values():
        sp.set_edgecolor("#444")

    # ── per-path time-domain ─────────────────────────────────────────────────
    ax = axs[1]
    ax.clear()
    ax.set_facecolor(ROOM_COLOR)
    for i, path in enumerate(paths):
        color = PATH_COLORS[i % len(PATH_COLORS)]
        sig = path["amplitude"] * np.cos(k * t - path["phase_rad"])
        ax.plot(t, sig, color=color, lw=1.2, alpha=0.85,
                label=path["label"])
    ax.axhline(0, color="#ffffff33", lw=0.5)
    ax.set_title("Per-path Time Domain", color="white", fontsize=8)
    ax.tick_params(colors="white", labelsize=6)
    ax.set_xlabel("distance (m)", color="white", fontsize=7)
    ax.set_ylabel("amplitude", color="white", fontsize=7)
    ax.legend(fontsize=5, framealpha=0.3, labelcolor="white",
              facecolor=ROOM_COLOR, loc="upper right")
    for sp in ax.spines.values():
        sp.set_edgecolor("#444")

    # ── summed signal ─────────────────────────────────────────────────────────
    composite = sum(p["complex_amp"] for p in paths)
    comp_amp  = abs(composite)
    comp_phase = np.angle(composite)

    ax = axs[2]
    ax.clear()
    ax.set_facecolor(ROOM_COLOR)
    ax.axhline(0, color="#ffffff33", lw=0.7)
    ax.axvline(0, color="#ffffff33", lw=0.7)
    # draw individual contributions faded
    for i, path in enumerate(paths):
        c = path["complex_amp"]
        color = PATH_COLORS[i % len(PATH_COLORS)]
        ax.annotate("", xy=(c.real, c.imag), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="->", color=color,
                                   lw=1.0, alpha=0.3))
    # draw composite
    ax.annotate("", xy=(composite.real, composite.imag), xytext=(0, 0),
                arrowprops=dict(arrowstyle="->", color=SUM_COLOR, lw=2.5))
    ax.plot(composite.real, composite.imag, "o", color=SUM_COLOR, ms=7)
    lim2 = max(comp_amp * 1.35, lim) + 0.01
    ax.set_xlim(-lim2, lim2)
    ax.set_ylim(-lim2, lim2)
    ax.set_aspect("equal")
    phase_deg = np.degrees(comp_phase) % 360
    ax.set_title(
        f"Composite Phasor  |A|={comp_amp:.3f}  φ={phase_deg:.1f}°",
        color="white", fontsize=8)
    ax.tick_params(colors="white", labelsize=6)
    ax.set_xlabel("Re", color="white", fontsize=7)
    ax.set_ylabel("Im", color="white", fontsize=7)
    for sp in ax.spines.values():
        sp.set_edgecolor("#444")

    ax = axs[3]
    ax.clear()
    ax.set_facecolor(ROOM_COLOR)
    sig_sum = sum(
        p["amplitude"] * np.cos(k * t - p["phase_rad"]) for p in paths)
    ax.plot(t, sig_sum, color=SUM_COLOR, lw=1.8, label="Σ signal")
    ax.fill_between(t, sig_sum, alpha=0.15, color=SUM_COLOR)
    ax.axhline(0, color="#ffffff33", lw=0.5)

    # envelope
    env = comp_amp * np.ones_like(t)
    ax.plot(t, env,  "--", color="#ffffff55", lw=0.9, label=f"env={comp_amp:.3f}")
    ax.plot(t, -env, "--", color="#ffffff55", lw=0.9)

    ax.set_title("Composite Time Domain", color="white", fontsize=8)
    ax.tick_params(colors="white", labelsize=6)
    ax.set_xlabel("distance (m)", color="white", fontsize=7)
    ax.set_ylabel("amplitude", color="white", fontsize=7)
    ax.legend(fontsize=6, framealpha=0.3, labelcolor="white",
              facecolor=ROOM_COLOR)
    for sp in ax.spines.values():
        sp.set_edgecolor("#444")

    # coherence / fading note
    max_possible = sum(p["amplitude"] for p in paths)
    coherence = comp_amp / max(max_possible, 1e-9)
    note_color = "#2ecc71" if coherence > 0.5 else "#e74c3c"
    axs[3].text(0.02, 0.95,
                f"Coherence: {coherence:.2f}  "
                f"({'constructive' if coherence>0.5 else 'destructive'})",
                transform=axs[3].transAxes, fontsize=7,
                color=note_color, va="top")


# ═════════════════════════════════════════════════════════════════════════════
# Drag state
# ═════════════════════════════════════════════════════════════════════════════

class DragState:
    def __init__(self):
        self.active = None   # "tx" | "rx" | "person"

    def nearest(self, x, y, tx, rx, person, threshold=0.8):
        pts = {"tx": tx, "rx": rx, "person": person}
        best, best_d = None, threshold
        for name, (px, py) in pts.items():
            d = np.hypot(x - px, y - py)
            if d < best_d:
                best, best_d = name, d
        return best


# ═════════════════════════════════════════════════════════════════════════════
# Screen recorder
# ═════════════════════════════════════════════════════════════════════════════

RECORD_FPS   = 12          # frames per second captured
RECORD_COLOR = "#e74c3c"   # button colour while recording
IDLE_COLOR   = "#16213e"


class Recorder:
    """
    Captures canvas frames from the main thread via a draw_event hook,
    rate-limited to RECORD_FPS.  Saving is done on a background thread.
    """

    def __init__(self, fig):
        self.fig      = fig
        self.frames   = []
        self.active   = False
        self._cid     = None
        self._last_t  = 0.0
        self._lock    = threading.Lock()

    def start(self):
        if self.active:
            return
        self.frames  = []
        self.active  = True
        self._last_t = 0.0
        self._cid = self.fig.canvas.mpl_connect(
            "draw_event", self._on_draw)

    def stop(self):
        if self._cid is not None:
            self.fig.canvas.mpl_disconnect(self._cid)
            self._cid = None
        self.active = False
        return list(self.frames)

    def _on_draw(self, _event):
        now = time.monotonic()
        if now - self._last_t < 1.0 / RECORD_FPS:
            return
        self._last_t = now
        frame = self._grab_frame()
        if frame is not None:
            with self._lock:
                self.frames.append(frame)

    def _grab_frame(self):
        try:
            buf = io.BytesIO()
            self.fig.savefig(buf, format="png", dpi=100,
                             facecolor=self.fig.get_facecolor())
            buf.seek(0)
            return Image.open(buf).copy()
        except Exception:
            return None

    def save_gif(self, path, status_cb=None):
        """Save captured frames as an animated GIF on a background thread."""
        frames = list(self.frames)
        if not frames:
            if status_cb:
                status_cb("No frames captured.")
            return

        def _write():
            try:
                if status_cb:
                    status_cb(f"Saving {len(frames)} frames…")
                duration_ms = int(1000 / RECORD_FPS)
                frames[0].save(
                    path,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    duration=duration_ms,
                    loop=0,
                    optimize=False,
                )
                size_mb = os.path.getsize(path) / 1e6
                if status_cb:
                    status_cb(
                        f"Saved {len(frames)} frames → {path} "
                        f"({size_mb:.1f} MB)")
            except Exception as exc:
                if status_cb:
                    status_cb(f"Save error: {exc}")

        threading.Thread(target=_write, daemon=True).start()


# ═════════════════════════════════════════════════════════════════════════════
# Main app
# ═════════════════════════════════════════════════════════════════════════════

def main():
    p = DEFAULTS.copy()
    L = p["room_size"]

    # mutable positions (list so closures can rebind)
    state = {
        "tx":     [L * 0.2, L * 0.5],
        "rx":     [L * 0.8, L * 0.5],
        "person": [L * 0.5, L * 0.5],
    }
    drag = DragState()

    # ── figure layout ─────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(18, 9), facecolor="#0d0d1a")
    fig.suptitle("CSI / Multipath Channel Visualizer",
                 color="white", fontsize=13, fontweight="bold")

    # Left: room (col 0), Right: 2×2 signal grid (cols 1-2)
    gs = fig.add_gridspec(
        4, 3,
        left=0.05, right=0.97,
        top=0.92, bottom=0.28,
        wspace=0.35, hspace=0.55,
        width_ratios=[1.2, 1, 1],
    )
    ax_room   = fig.add_subplot(gs[:, 0])
    ax_phasor = fig.add_subplot(gs[0:2, 1])
    ax_time   = fig.add_subplot(gs[0:2, 2])
    ax_sphasor= fig.add_subplot(gs[2:4, 1])
    ax_stime  = fig.add_subplot(gs[2:4, 2])

    signal_axs = [ax_phasor, ax_time, ax_sphasor, ax_stime]

    # ── sliders ───────────────────────────────────────────────────────────────
    slider_color = "#16213e"
    sl_specs = [
        # (label, left, bottom, min, max, init, step)
        ("Room size (m)",   0.07, 0.20, 4.0,  20.0, p["room_size"],    0.5),
        ("Reflectivity",    0.07, 0.16, 0.0,   1.0,  p["reflectivity"], 0.01),
        ("Wavelength (m)",  0.07, 0.12, 0.05,  0.5,  p["wavelength"],   0.01),
        ("# Paths",         0.07, 0.08, 1,     7,    p["n_paths"],      1),
        ("TX  x",           0.55, 0.20, 0.05,  0.95, 0.2,              0.01),
        ("TX  y",           0.55, 0.16, 0.05,  0.95, 0.5,              0.01),
        ("RX  x",           0.55, 0.12, 0.05,  0.95, 0.8,              0.01),
        ("RX  y",           0.55, 0.08, 0.05,  0.95, 0.5,              0.01),
        ("Person x",        0.55, 0.04, 0.05,  0.95, 0.5,              0.01),
        ("Person y",        0.07, 0.04, 0.05,  0.95, 0.5,              0.01),
    ]
    sliders = {}
    for label, l, b, vmin, vmax, vinit, vstep in sl_specs:
        ax_sl = fig.add_axes([l, b, 0.35, 0.025], facecolor=slider_color)
        sl = Slider(ax_sl, label, vmin, vmax, valinit=vinit, valstep=vstep,
                    color="#3498db")
        sl.label.set_color("white")
        sl.label.set_fontsize(7)
        sl.valtext.set_color("#f5a623")
        sliders[label] = sl

    # ── redraw callback ───────────────────────────────────────────────────────
    def redraw(_=None):
        L_new = sliders["Room size (m)"].val
        refl  = sliders["Reflectivity"].val
        wl    = sliders["Wavelength (m)"].val
        n     = int(sliders["# Paths"].val)

        # positions are fractions of room size from sliders
        tx_frac     = (sliders["TX  x"].val,     sliders["TX  y"].val)
        rx_frac     = (sliders["RX  x"].val,     sliders["RX  y"].val)
        person_frac = (sliders["Person x"].val,  sliders["Person y"].val)

        tx     = (tx_frac[0]     * L_new, tx_frac[1]     * L_new)
        rx     = (rx_frac[0]     * L_new, rx_frac[1]     * L_new)
        person = (person_frac[0] * L_new, person_frac[1] * L_new)

        state["tx"]     = list(tx)
        state["rx"]     = list(rx)
        state["person"] = list(person)

        paths = build_paths(tx, rx, person, L_new, n, wl, refl)
        draw_room(ax_room, L_new, paths, tx, rx, person, wl)
        draw_signal_plots(signal_axs, paths, wl)
        fig.canvas.draw_idle()

    for sl in sliders.values():
        sl.on_changed(redraw)

    # ── drag interaction on room axes ─────────────────────────────────────────
    def on_press(event):
        if event.inaxes != ax_room:
            return
        L_now = sliders["Room size (m)"].val
        drag.active = drag.nearest(
            event.xdata, event.ydata,
            state["tx"], state["rx"], state["person"],
            threshold=L_now * 0.08)

    def on_release(event):
        drag.active = None

    def on_motion(event):
        if drag.active is None or event.inaxes != ax_room:
            return
        L_now = sliders["Room size (m)"].val
        x = np.clip(event.xdata, 0.05 * L_now, 0.95 * L_now)
        y = np.clip(event.ydata, 0.05 * L_now, 0.95 * L_now)
        frac_x = x / L_now
        frac_y = y / L_now
        name = drag.active
        if name == "tx":
            sliders["TX  x"].set_val(frac_x)
            sliders["TX  y"].set_val(frac_y)
        elif name == "rx":
            sliders["RX  x"].set_val(frac_x)
            sliders["RX  y"].set_val(frac_y)
        elif name == "person":
            sliders["Person x"].set_val(frac_x)
            sliders["Person y"].set_val(frac_y)

    fig.canvas.mpl_connect("button_press_event",   on_press)
    fig.canvas.mpl_connect("button_release_event", on_release)
    fig.canvas.mpl_connect("motion_notify_event",  on_motion)

    # ── reset button ──────────────────────────────────────────────────────────
    ax_btn = fig.add_axes([0.05, 0.01, 0.10, 0.04])
    btn_reset = Button(ax_btn, "Reset", color="#16213e", hovercolor="#e94560")
    btn_reset.label.set_color("white")

    def reset(_):
        defaults_frac = DEFAULTS.copy()
        sliders["Room size (m)"].set_val(defaults_frac["room_size"])
        sliders["Reflectivity"].set_val(defaults_frac["reflectivity"])
        sliders["Wavelength (m)"].set_val(defaults_frac["wavelength"])
        sliders["# Paths"].set_val(defaults_frac["n_paths"])
        sliders["TX  x"].set_val(0.2)
        sliders["TX  y"].set_val(0.5)
        sliders["RX  x"].set_val(0.8)
        sliders["RX  y"].set_val(0.5)
        sliders["Person x"].set_val(0.5)
        sliders["Person y"].set_val(0.5)

    btn_reset.on_clicked(reset)

    # ── recording controls ────────────────────────────────────────────────────
    recorder = Recorder(fig)

    # save-path text box
    ax_path = fig.add_axes([0.18, 0.015, 0.38, 0.025],
                           facecolor="#16213e")
    default_save = os.path.join(os.path.expanduser("~"),
                                "csi_recording.gif")
    tb_path = TextBox(ax_path, "Save path  ", initial=default_save,
                      color="#16213e", hovercolor="#1e2d4a")
    tb_path.label.set_color("white")
    tb_path.label.set_fontsize(7)
    tb_path.text_disp.set_color("#f5a623")
    tb_path.text_disp.set_fontsize(7)

    # record / stop button
    ax_rec = fig.add_axes([0.58, 0.01, 0.12, 0.04])
    btn_rec = Button(ax_rec, "⏺  Record",
                     color=IDLE_COLOR, hovercolor="#c0392b")
    btn_rec.label.set_color("white")
    btn_rec.label.set_fontsize(8)

    # status label
    status_text = fig.text(
        0.72, 0.022, "",
        color="#aaaaaa", fontsize=7, va="center",
        clip_on=True)

    def set_status(msg):
        status_text.set_text(msg)
        fig.canvas.draw_idle()

    def toggle_record(_):
        if not recorder.active:
            recorder.start()
            btn_rec.label.set_text("⏹  Stop")
            btn_rec.color       = RECORD_COLOR
            btn_rec.hovercolor  = "#c0392b"
            btn_rec.ax.set_facecolor(RECORD_COLOR)
            set_status(f"Recording at {RECORD_FPS} fps…")
        else:
            frames = recorder.stop()
            btn_rec.label.set_text("⏺  Record")
            btn_rec.color      = IDLE_COLOR
            btn_rec.hovercolor = "#c0392b"
            btn_rec.ax.set_facecolor(IDLE_COLOR)
            path = tb_path.text.strip() or default_save
            if not path.lower().endswith(".gif"):
                path += ".gif"
            recorder.save_gif(path, status_cb=set_status)
        fig.canvas.draw_idle()

    btn_rec.on_clicked(toggle_record)

    # ── help text ─────────────────────────────────────────────────────────────
    fig.text(0.05, 0.005,
             "Drag TX ★ / RX ◆ / Person directly in room, or use sliders.  "
             "↯ = path interacts with person.  "
             "Phasors: arrows = individual paths, pink = composite.",
             color="#aaaaaa", fontsize=7)

    # initial draw
    redraw()
    plt.show()


if __name__ == "__main__":
    main()
