"""
plot_types/time_series.py — Generic time-series plot.

Each subplot draws provided data over time using a continuous
colormap so newer samples are visually distinct from older ones.
Legend items are click-to-hide.
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.collections import LineCollection
from matplotlib.cm import get_cmap

WINDOW_LEN = 200


class TimeSeriesFigure:
    """Time-series figure with colormap-tinted traces.

    Args:
        n_plots (int): Number of subplots.
        n_rows_grid (int): Grid rows for subplot layout.
        n_cols_grid (int): Grid columns for subplot layout.
        titles (list[str]): Per-subplot titles.
        ylabels (list[str]): Per-subplot y-axis labels.
        window (int): Rolling window depth.
        fig_title (str): Figure window title.
        cmap_name (str): Colormap name (time axis).
    """

    def __init__(
        self,
        n_plots:      int,
        n_rows_grid:  int = 1,
        n_cols_grid:  int = None,
        titles:       list = None,
        ylabels:      list = None,
        window:       int = WINDOW_LEN,
        fig_title:    str = 'Time Series',
        cmap_name:    str = 'viridis',
    ):
        self._n       = n_plots
        self._win     = window
        self._titles  = titles  or [f'Plot {i}' for i in range(n_plots)]
        self._ylabels = ylabels or [''] * n_plots
        self._cmap    = get_cmap(cmap_name)
        self._hidden  = set()

        if n_cols_grid is None:
            n_cols_grid = max(1, (n_plots + n_rows_grid - 1) // n_rows_grid)

        self.fig = plt.figure(fig_title, figsize=(5 * n_cols_grid,
                                                   4 * n_rows_grid))
        self.fig.suptitle(fig_title, fontsize=10)

        self.axes = []
        for k in range(n_plots):
            ax = self.fig.add_subplot(n_rows_grid, n_cols_grid, k + 1)
            ax.set_title(self._titles[k], fontsize=8)
            ax.set_xlabel('Frame', fontsize=7)
            ax.set_ylabel(self._ylabels[k], fontsize=7)
            ax.grid(True, lw=0.3, alpha=0.3)
            self.axes.append(ax)

        self.fig.tight_layout()

    def draw(self, data_list: list, series_labels: list = None):
        """Redraw all subplots.

        Args:
            data_list (list): One entry per subplot.
                Each entry is a list of 1-D real arrays (one per series).
            series_labels (list[str]): Labels for legend items.
        """
        labels = series_labels or []
        for k, ax in enumerate(self.axes):
            ax.cla()
            ax.set_title(self._titles[k], fontsize=8)
            ax.set_xlabel('Frame', fontsize=7)
            ax.set_ylabel(self._ylabels[k], fontsize=7)
            ax.grid(True, lw=0.3, alpha=0.3)

            if k >= len(data_list) or not data_list[k]:
                continue

            handles, leg_labels = [], []
            for s_idx, arr in enumerate(data_list[k]):
                if arr is None or len(arr) == 0:
                    continue
                n     = len(arr)
                x     = np.arange(n)
                color = f'C{s_idx}'

                # Build LineCollection with time-dependent color
                pts   = np.array([x, arr]).T.reshape(-1, 1, 2)
                segs  = np.concatenate([pts[:-1], pts[1:]], axis=1)
                t_val = np.linspace(0, 1, max(len(segs), 1))
                lc    = LineCollection(segs, cmap=self._cmap,
                                       array=t_val, lw=1.2)
                ax.add_collection(lc)

                ax.set_xlim(0, max(n - 1, 1))
                ax.autoscale_view(scalex=False)

                label = (labels[s_idx] if s_idx < len(labels)
                         else f'S{s_idx}')
                from matplotlib.lines import Line2D
                h = Line2D([], [], color=color, lw=1.5,
                           label=label, alpha=0.8)
                handles.append(h)
                leg_labels.append(label)

            if handles:
                leg = ax.legend(handles, leg_labels,
                                fontsize=7, loc='upper right')
                self._attach_pick(ax, leg, handles)

        self.fig.canvas.draw_idle()

    def _attach_pick(self, ax, leg, handles):
        """Wire legend click-to-hide for all handles in leg.

        Args:
            ax: Matplotlib axes.
            leg: Legend object.
            handles (list): Original line handles.
        """
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
