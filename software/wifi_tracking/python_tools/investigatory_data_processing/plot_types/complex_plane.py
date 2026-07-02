"""
plot_types/complex_plane.py — Generic complex-plane scatter/trail.

Supports a configurable n_rows_grid × n_cols_grid subplot grid.
Only positions listed in active_positions are populated.

Features per subplot:
  - Fading opacity: newer points more opaque than older.
  - Per-series distinct markers; click legend entry to hide.
  - Half-opacity dashed line connecting consecutive points.
  - "All SCs" checkbox: all subcarriers (in selected range) shown
    simultaneously, each coloured by a continuous colormap.
  - Subcarrier slider: single-SC selector, shown when unchecked.
  - SC Lo / SC Hi sliders: lower and upper range bounds, shown when
    "All SCs" is checked.
  - Time Window slider: trims visible frames from 1 to max window.

draw() API:
    data_dict: {(row, col): [series_array, ...]}
    Each series_array has shape (n_frames, n_sc) complex.
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.colors as mcolors
from matplotlib.lines import Line2D
from matplotlib.widgets import CheckButtons, Slider
from matplotlib.cm import get_cmap

WINDOW_LEN  = 200
_MARKERS    = ['o', 's', '^', 'D', 'v', 'P', '*', 'X']
_ALPHA_MAX  = 0.9
_ALPHA_MIN  = 0.05
_LINE_ALPHA = 0.35


def _fade(n: int) -> np.ndarray:
    """Return alpha ramp from _ALPHA_MIN (oldest) to _ALPHA_MAX (newest).

    Args:
        n (int): Number of points.
    Returns:
        np.ndarray: shape (n,) float alpha values.
    """
    if n <= 1:
        return np.array([_ALPHA_MAX])
    return np.linspace(_ALPHA_MIN, _ALPHA_MAX, n)


class ComplexPlaneFigure:
    """Complex-plane figure in an n_rows × n_cols active-position grid.

    Args:
        n_rows_grid (int): Grid rows.
        n_cols_grid (int): Grid columns.
        active_positions (list[tuple]): (row, col) pairs to populate.
            None = all positions.
        series_labels (list[str]): Label per data series.
        titles (dict): {(row, col): str} subplot title overrides.
        window (int): Rolling window depth (frames).
        fig_title (str): Figure window title.
        cmap_name (str): Colormap for all-SCs mode.
    """

    def __init__(
        self,
        n_rows_grid:      int,
        n_cols_grid:      int,
        active_positions: list = None,
        series_labels:    list = None,
        titles:           dict = None,
        window:           int  = WINDOW_LEN,
        fig_title:        str  = 'Complex Plane',
        cmap_name:        str  = 'plasma',
    ):
        self._nr        = n_rows_grid
        self._nc        = n_cols_grid
        self._win       = window
        self._labels    = series_labels or ['Value']
        self._titles    = titles or {}
        self._fig_title = fig_title
        self._cmap      = get_cmap(cmap_name)
        self._active = (
            active_positions if active_positions is not None
            else [(r, c)
                  for r in range(n_rows_grid)
                  for c in range(n_cols_grid)])

        self._all_sc   = False
        self._sc_idx   = 0
        self._n_sc     = 1
        self._sc_range = (0, 0)    # (lo, hi) subcarrier range for all-SC mode
        self._time_win = window    # visible frame count (1 … window)
        self._hidden   = set()     # hidden series indices

        fw = max(4.0, n_cols_grid * 2.5)
        fh = max(3.0, n_rows_grid * 2.5 + 0.8)
        self.fig = plt.figure(fig_title, figsize=(fw, fh))
        self.fig.suptitle(fig_title, fontsize=10)

        gs = gridspec.GridSpec(
            n_rows_grid, n_cols_grid,
            figure=self.fig, hspace=0.55, wspace=0.35)

        self._ax = {}
        for r, c in self._active:
            ax = self.fig.add_subplot(gs[r, c])
            t  = self._titles.get((r, c), f'[{r},{c}]')
            ax.set_title(t, fontsize=7)
            ax.axhline(0, color='grey', lw=0.4, alpha=0.4)
            ax.axvline(0, color='grey', lw=0.4, alpha=0.4)
            ax.grid(True, lw=0.3, alpha=0.3)
            ax.set_xlabel('Real', fontsize=6)
            ax.set_ylabel('Imag', fontsize=6)
            self._ax[(r, c)] = ax

        self.fig.subplots_adjust(bottom=0.17, top=0.92)

        # "All SCs" checkbox
        ax_chk = self.fig.add_axes([0.05, 0.01, 0.15, 0.065])
        ax_chk.set_axis_off()
        self._chk = CheckButtons(ax_chk, ['All SCs'], [False])
        self._chk.on_clicked(self._on_check)

        # Single-subcarrier slider (shown when All SCs is unchecked)
        ax_sl = self.fig.add_axes([0.25, 0.02, 0.65, 0.025])
        self._sc_slider = Slider(
            ax_sl, 'Subcarrier', 0, 1,
            valinit=0, valstep=1, color='steelblue')
        self._sc_slider.on_changed(self._on_sc_change)

        # SC Lo / SC Hi sliders (shown when All SCs is checked)
        ax_lo = self.fig.add_axes([0.25, 0.02, 0.65, 0.025])
        ax_lo.set_visible(False)
        self._sc_lo_slider = Slider(
            ax_lo, 'SC Lo', 0, 1,
            valinit=0, valstep=1, color='steelblue')
        self._sc_lo_slider.on_changed(self._on_sc_lo_change)

        ax_hi = self.fig.add_axes([0.25, 0.055, 0.65, 0.025])
        ax_hi.set_visible(False)
        self._sc_hi_slider = Slider(
            ax_hi, 'SC Hi', 0, 1,
            valinit=1, valstep=1, color='cornflowerblue')
        self._sc_hi_slider.on_changed(self._on_sc_hi_change)

        # Time-window slider — controls how many recent frames to show
        ax_tw = self.fig.add_axes([0.25, 0.09, 0.65, 0.025])
        self._time_slider = Slider(
            ax_tw, 'Window', 1, max(window, 2),
            valinit=window, valstep=1, color='mediumpurple')
        self._time_slider.on_changed(self._on_time_change)

    def _on_check(self, _label):
        self._all_sc = not self._all_sc
        self._sc_slider.ax.set_visible(not self._all_sc)
        self._sc_lo_slider.ax.set_visible(self._all_sc)
        self._sc_hi_slider.ax.set_visible(self._all_sc)
        self.fig.canvas.draw_idle()

    def _on_sc_change(self, val):
        self._sc_idx = int(round(val))

    def _on_sc_lo_change(self, val):
        self._sc_range = (int(round(val)), self._sc_range[1])

    def _on_sc_hi_change(self, val):
        self._sc_range = (self._sc_range[0], int(round(val)))

    def _on_time_change(self, val):
        self._time_win = max(1, int(round(val)))

    def set_n_sc(self, n_sc: int):
        """Update subcarrier slider range.

        Updates both the single-SC slider and the range slider.

        Args:
            n_sc (int): Number of subcarriers.
        """
        if n_sc < 1 or n_sc == self._n_sc:
            return
        self._n_sc = n_sc
        # Single-SC slider
        self._sc_slider.valmax = n_sc - 1
        self._sc_slider.ax.set_xlim(0, n_sc - 1)
        self._sc_slider.set_val(min(self._sc_idx, n_sc - 1))
        # SC Lo / SC Hi sliders — expand hi to full range on first update
        lo, hi = self._sc_range
        new_hi = min(hi, n_sc - 1) if hi > 0 else n_sc - 1
        new_lo = min(lo, new_hi)
        for sl in (self._sc_lo_slider, self._sc_hi_slider):
            sl.valmax = n_sc - 1
            sl.ax.set_xlim(0, n_sc - 1)
        self._sc_lo_slider.set_val(new_lo)
        self._sc_hi_slider.set_val(new_hi)
        self._sc_range = (new_lo, new_hi)

    def draw(self, data_dict: dict):
        """Redraw all active complex-plane subplots.

        Args:
            data_dict (dict): {(row, col): [series_array, ...]}
                series_array: (n_frames, n_sc) complex ndarray.
        """
        for (r, c), series_list in data_dict.items():
            ax = self._ax.get((r, c))
            if ax is None or not series_list:
                continue

            ax.cla()
            t = self._titles.get((r, c), f'[{r},{c}]')
            ax.set_title(t, fontsize=7)
            ax.axhline(0, color='grey', lw=0.4, alpha=0.4)
            ax.axvline(0, color='grey', lw=0.4, alpha=0.4)
            ax.grid(True, lw=0.3, alpha=0.3)
            ax.set_xlabel('Real', fontsize=6)
            ax.set_ylabel('Imag', fontsize=6)

            proxies, proxy_labels = [], []
            for s_idx, series in enumerate(series_list):
                if series is None or series.size == 0:
                    continue
                # Apply time-window trim along the frame axis.
                n_show = min(self._time_win, series.shape[0])
                series = series[-n_show:]
                label  = (self._labels[s_idx]
                          if s_idx < len(self._labels)
                          else f'S{s_idx}')
                marker = _MARKERS[s_idx % len(_MARKERS)]
                hidden = s_idx in self._hidden

                if self._all_sc:
                    self._draw_all_sc(ax, series, s_idx, marker, hidden)
                else:
                    h = self._draw_single_sc(
                        ax, series, s_idx, marker, label, hidden)
                    if h:
                        proxies.append(h)
                        proxy_labels.append(label)

            if proxies:
                self._attach_legend(ax, proxies, proxy_labels)

            ax.relim()
            ax.autoscale_view()

        self.fig.canvas.draw_idle()

    def _draw_single_sc(
        self, ax, series, s_idx, marker, label, hidden
    ):
        """Plot history for the selected subcarrier.

        Args:
            ax: Matplotlib axes.
            series (np.ndarray): (n_frames, n_sc) complex.
            s_idx (int): Series index (colour selector).
            marker (str): Marker style.
            label (str): Legend label.
            hidden (bool): Whether this series is hidden.
        Returns:
            Line2D proxy handle or None.
        """
        sc   = min(self._sc_idx, series.shape[1] - 1)
        pts  = series[:, sc]
        n    = len(pts)
        if n == 0:
            return None
        color  = f'C{s_idx}'
        scale  = 0.2 if hidden else 1.0
        alphas = _fade(n) * scale
        base_a = _LINE_ALPHA * scale

        re, im = pts.real, pts.imag
        ax.plot(re, im, '--', color=color, lw=0.8, alpha=base_a)

        # Build per-point RGBA so a single scatter handles all data limits.
        base_rgb = mcolors.to_rgb(color)
        rgba = np.column_stack([
            np.tile(base_rgb, (n, 1)), alphas])
        ax.scatter(re, im, color=rgba, marker=marker,
                   s=18, linewidths=0, zorder=3)

        return Line2D([], [], color=color, marker=marker,
                      linestyle='--', label=label, markersize=5)

    def _draw_all_sc(self, ax, series, s_idx, marker, hidden):
        """Plot subcarriers in the selected range with per-SC colour.

        When the range slider is at full range, all subcarriers are
        shown.  The colour mapping always spans the full n_sc range so
        colours are consistent as the range is adjusted.

        Args:
            ax: Matplotlib axes.
            series (np.ndarray): (n_frames, n_sc) complex.
            s_idx (int): Series index.
            marker (str): Marker style.
            hidden (bool): Whether this series is hidden.
        """
        n_frames, n_sc = series.shape
        scale = 0.2 if hidden else 1.0
        lo = max(0, min(self._sc_range))
        hi = min(n_sc - 1, max(self._sc_range))
        for sc in range(lo, hi + 1):
            color  = self._cmap(sc / max(n_sc - 1, 1))
            pts    = series[:, sc]
            n      = len(pts)
            alphas = _fade(n) * scale
            re, im = pts.real, pts.imag
            ax.plot(re, im, '--', color=color, lw=0.5,
                    alpha=_LINE_ALPHA * scale)
            base_rgb = mcolors.to_rgb(color)
            rgba = np.column_stack([
                np.tile(base_rgb, (n, 1)), alphas])
            ax.scatter(re, im, color=rgba, marker=marker,
                       s=10, linewidths=0, zorder=3)

    def _attach_legend(self, ax, proxies: list, labels: list):
        """Attach click-to-hide legend.

        Args:
            ax: Matplotlib axes.
            proxies (list[Line2D]): Proxy handles.
            labels (list[str]): Labels.
        """
        leg  = ax.legend(proxies, labels, fontsize=6, loc='upper right')
        map_ = {lh: idx for idx, lh
                in enumerate(leg.get_lines())}
        for lh in leg.get_lines():
            lh.set_picker(5)

        def _pick(event):
            idx = map_.get(event.artist)
            if idx is None:
                return
            if idx in self._hidden:
                self._hidden.discard(idx)
            else:
                self._hidden.add(idx)
            event.artist.set_alpha(
                0.2 if idx in self._hidden else 1.0)
            ax.figure.canvas.draw_idle()

        self.fig.canvas.mpl_connect('pick_event', _pick)
