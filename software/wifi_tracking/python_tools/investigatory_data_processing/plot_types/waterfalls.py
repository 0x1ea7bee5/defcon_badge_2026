"""
plot_types/waterfalls.py — Generic magnitude + phase waterfall.

Supports a configurable n_rows_grid × n_cols_grid subplot grid.
Only positions listed in active_positions are populated; all other
grid cells are left empty.

Legend items support click-to-hide via add_clickable_legend().

draw() API:
    data_dict: {(row, col): (mag_db_2d, phase_2d)}
    where mag_db_2d and phase_2d are (n_frames, n_sc) ndarrays.
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.cm as cm
from matplotlib.colors import Normalize

WINDOW_LEN = 200
_DB_MIN    = -40.0
_DB_MAX    = 0.0


class WaterfallFigure:
    """Magnitude + phase waterfall in an n_rows × n_cols grid.

    Each active grid cell contains two adjacent axes: magnitude (dB)
    on the left and phase on the right.  Inactive cells are empty.

    Args:
        n_rows_grid (int): Number of subplot rows.
        n_cols_grid (int): Number of subplot columns.
        active_positions (list[tuple]): (row, col) pairs to populate.
            None = all positions.
        titles (dict): {(row, col): str} titles per subplot.
        window (int): Rolling window depth (frames).
        fig_title (str): Figure window title.
    """

    def __init__(
        self,
        n_rows_grid:      int,
        n_cols_grid:      int,
        active_positions: list = None,
        titles:           dict = None,
        window:           int  = WINDOW_LEN,
        fig_title:        str  = 'Waterfall',
    ):
        self._nr     = n_rows_grid
        self._nc     = n_cols_grid
        self._win    = window
        self._titles = titles or {}
        self._active = (
            active_positions if active_positions is not None
            else [(r, c)
                  for r in range(n_rows_grid)
                  for c in range(n_cols_grid)])

        fw = max(6.0, n_cols_grid * 4.0)
        fh = max(3.0, n_rows_grid * 2.5 + 0.8)
        self.fig = plt.figure(fig_title, figsize=(fw, fh))
        self.fig.suptitle(fig_title, fontsize=10)

        # GridSpec: n_rows × (n_cols * 2) — each cell = (mag, phase)
        gs = gridspec.GridSpec(
            n_rows_grid, n_cols_grid * 2,
            figure=self.fig,
            hspace=0.55, wspace=0.30)

        self._ax_mag   = {}
        self._ax_phase = {}
        self._im_mag   = {}
        self._im_phase = {}

        for r, c in self._active:
            t    = self._titles.get((r, c), f'[{r},{c}]')
            ax_m = self.fig.add_subplot(gs[r, c * 2])
            ax_p = self.fig.add_subplot(gs[r, c * 2 + 1])
            ax_m.set_title(t + ' |·| dB', fontsize=7)
            ax_m.set_xlabel('SC', fontsize=6)
            ax_m.set_ylabel('Frame', fontsize=6)
            ax_p.set_title(t + ' phase', fontsize=7)
            ax_p.set_xlabel('SC', fontsize=6)
            self._ax_mag[(r, c)]   = ax_m
            self._ax_phase[(r, c)] = ax_p
            self._im_mag[(r, c)]   = None
            self._im_phase[(r, c)] = None

        # Leave right margin for colorbars.
        self.fig.subplots_adjust(top=0.92, right=0.84)

        # Shared magnitude colorbar (dB)
        ax_cb_m = self.fig.add_axes([0.86, 0.12, 0.015, 0.80])
        sm_mag  = cm.ScalarMappable(
            cmap='inferno', norm=Normalize(_DB_MIN, _DB_MAX))
        sm_mag.set_array([])
        cb_m = self.fig.colorbar(sm_mag, cax=ax_cb_m)
        cb_m.set_label('dB', fontsize=8)

        # Shared phase colorbar (rad)
        ax_cb_p = self.fig.add_axes([0.93, 0.12, 0.015, 0.80])
        sm_ph   = cm.ScalarMappable(
            cmap='twilight', norm=Normalize(-np.pi, np.pi))
        sm_ph.set_array([])
        cb_p = self.fig.colorbar(sm_ph, cax=ax_cb_p)
        cb_p.set_label('rad', fontsize=8)

    def draw(self, data_dict: dict):
        """Update all active waterfall subplots.

        Args:
            data_dict (dict): {(row, col): (mag_db, phase)}
                mag_db (np.ndarray): (n_frames, n_sc) magnitude dB.
                phase  (np.ndarray): (n_frames, n_sc) phase radians.
        """
        for (r, c), (mag, phase) in data_dict.items():
            ax_m = self._ax_mag.get((r, c))
            if ax_m is None or mag is None or mag.size == 0:
                continue
            ax_p  = self._ax_phase[(r, c)]
            nf, ns = mag.shape
            ext   = [0, ns, 0, nf]

            if self._im_mag[(r, c)] is None:
                self._im_mag[(r, c)] = ax_m.imshow(
                    mag[::-1], aspect='auto', cmap='inferno',
                    vmin=_DB_MIN, vmax=_DB_MAX,
                    extent=ext, interpolation='nearest')
                self._im_phase[(r, c)] = ax_p.imshow(
                    phase[::-1], aspect='auto', cmap='twilight',
                    vmin=-np.pi, vmax=np.pi,
                    extent=ext, interpolation='nearest')
            else:
                self._im_mag[(r, c)].set_data(mag[::-1])
                self._im_mag[(r, c)].set_extent(ext)
                self._im_phase[(r, c)].set_data(phase[::-1])
                self._im_phase[(r, c)].set_extent(ext)

        self.fig.canvas.draw_idle()

    def add_clickable_legend(self, ax, handles: list, labels: list):
        """Add a click-to-hide legend to an axes.

        Args:
            ax: Matplotlib axes.
            handles (list): Artist handles.
            labels (list[str]): Label strings.
        """
        leg  = ax.legend(handles, labels, fontsize=7, loc='upper right')
        map_ = {lh: h for lh, h in zip(leg.get_lines(), handles)}
        for lh in leg.get_lines():
            lh.set_picker(5)

        def _pick(event):
            orig = map_.get(event.artist)
            if orig is None:
                return
            vis = not orig.get_visible()
            orig.set_visible(vis)
            event.artist.set_alpha(1.0 if vis else 0.2)
            ax.figure.canvas.draw_idle()

        self.fig.canvas.mpl_connect('pick_event', _pick)
