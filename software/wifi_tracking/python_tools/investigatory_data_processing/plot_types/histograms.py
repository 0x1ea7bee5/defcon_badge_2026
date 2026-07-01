"""
plot_types/histograms.py — Generic live-updating histogram plot.

Each subplot shows a histogram that re-bins as new data arrives.
Legend items are click-to-hide when multiple series are plotted.
"""

import numpy as np
import matplotlib.pyplot as plt

WINDOW_LEN = 200
_N_BINS    = 40


class HistogramFigure:
    """Live-updating histogram figure.

    Args:
        n_plots (int): Number of subplots.
        n_rows_grid (int): Grid rows for subplot layout.
        n_cols_grid (int): Grid columns for subplot layout.
        titles (list[str]): Per-subplot titles.
        xlabels (list[str]): Per-subplot x-axis labels.
        n_bins (int): Number of histogram bins.
        window (int): Rolling window depth (samples per series).
        fig_title (str): Figure window title.
    """

    def __init__(
        self,
        n_plots:      int,
        n_rows_grid:  int = 1,
        n_cols_grid:  int = None,
        titles:       list = None,
        xlabels:      list = None,
        n_bins:       int = _N_BINS,
        window:       int = WINDOW_LEN,
        fig_title:    str = 'Histogram',
    ):
        self._n       = n_plots
        self._n_bins  = n_bins
        self._win     = window
        self._titles  = titles  or [f'Plot {i}' for i in range(n_plots)]
        self._xlabels = xlabels or [''] * n_plots
        self._hidden  = set()

        if n_cols_grid is None:
            n_cols_grid = max(
                1, (n_plots + n_rows_grid - 1) // n_rows_grid)

        self.fig = plt.figure(fig_title,
                              figsize=(5 * n_cols_grid, 4 * n_rows_grid))
        self.fig.suptitle(fig_title, fontsize=10)

        self.axes = []
        for k in range(n_plots):
            ax = self.fig.add_subplot(n_rows_grid, n_cols_grid, k + 1)
            ax.set_title(self._titles[k], fontsize=8)
            ax.set_xlabel(self._xlabels[k], fontsize=7)
            ax.set_ylabel('Count', fontsize=7)
            ax.grid(True, lw=0.3, alpha=0.3, axis='y')
            self.axes.append(ax)

        self.fig.tight_layout()

    def draw(self, data_list: list, series_labels: list = None):
        """Redraw all histograms.

        Args:
            data_list (list): One entry per subplot.
                Each entry is a list of 1-D real arrays (one per series).
            series_labels (list[str]): Labels for legend.
        """
        labels = series_labels or []
        for k, ax in enumerate(self.axes):
            ax.cla()
            ax.set_title(self._titles[k], fontsize=8)
            ax.set_xlabel(self._xlabels[k], fontsize=7)
            ax.set_ylabel('Count', fontsize=7)
            ax.grid(True, lw=0.3, alpha=0.3, axis='y')

            if k >= len(data_list) or not data_list[k]:
                continue

            handles, leg_labels = [], []
            for s_idx, arr in enumerate(data_list[k]):
                if arr is None or len(arr) == 0:
                    continue
                label  = (labels[s_idx] if s_idx < len(labels)
                          else f'S{s_idx}')
                color  = f'C{s_idx}'
                vis    = s_idx not in self._hidden

                n, bins, patches = ax.hist(
                    arr, bins=self._n_bins,
                    color=color, alpha=0.55 if vis else 0.1,
                    label=label, density=False)

                from matplotlib.patches import Patch
                handles.append(Patch(facecolor=color, alpha=0.7,
                                     label=label))
                leg_labels.append(label)

            if handles:
                leg = ax.legend(handles, leg_labels,
                                fontsize=7, loc='upper right')
                self._attach_pick(ax, leg, handles, data_list[k])

        self.fig.canvas.draw_idle()

    def _attach_pick(self, ax, leg, handles, series_data):
        """Wire click-to-hide on legend patches.

        Args:
            ax: Matplotlib axes.
            leg: Legend object.
            handles (list): Patch handles.
            series_data (list): Parallel series arrays.
        """
        patches = leg.get_patches()
        map_    = {p: i for i, p in enumerate(patches)}
        for p in patches:
            p.set_picker(5)

        def _pick(event):
            s_idx = map_.get(event.artist)
            if s_idx is None:
                return
            if s_idx in self._hidden:
                self._hidden.discard(s_idx)
            else:
                self._hidden.add(s_idx)
            event.artist.set_alpha(
                0.7 if s_idx not in self._hidden else 0.2)
            ax.figure.canvas.draw_idle()

        self.fig.canvas.mpl_connect('pick_event', _pick)
