#!/usr/bin/env python3
"""
Interactive SVD visualizer for a complex 2×2 matrix H = U Σ V†.

Each element H[i,j] = |H[i,j]| · exp(i·φ[i,j]).  Use the sliders
to control magnitude and phase of each element independently.

The real unit circle and standard basis vectors are transformed by H
and each of its SVD factors:
  • Solid scatter / solid arrows    →  real part of the output
  • Translucent scatter / dashed    →  imaginary part of the output

Layout (2×5 panels):
  Top row   : Original | V†   | Σ V†  | H = U Σ V† | H†H
  Bottom row: U        | Σ    | U Σ   | SVD matrix values (spans 2 cols)
  Sliders   : |H[0,0]|  |H[0,1]|  |H[1,0]|  |H[1,1]|   (magnitude)
               φ[0,0]    φ[0,1]    φ[1,0]    φ[1,1]     (phase)

Usage:
    python3 python_tools/visualize_SVD.py
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.widgets import Slider

# -----------------------------------------------------------------------
# Static geometry — real unit circle + real basis vectors
# -----------------------------------------------------------------------

_N      = 300
_THETA  = np.linspace(0, 2 * np.pi, _N, endpoint=False)
_CIRCLE = np.array([np.cos(_THETA), np.sin(_THETA)])   # (2, N) float64
_COLORS = plt.cm.hsv(np.linspace(0, 1, _N, endpoint=False))
_BASIS  = np.eye(2)                        # columns = e₁, e₂
_VC     = ['#e74c3c', '#2980b9']           # e₁=red, e₂=blue
_VL     = ['e₁', 'e₂']

_INDICES = [(0, 0), (0, 1), (1, 0), (1, 1)]


# -----------------------------------------------------------------------
# Drawing helpers
# -----------------------------------------------------------------------

def _draw(ax, M, title):
    """Apply complex matrix M to unit circle and basis; redraw ax.

    Solid elements = Re(output).  Translucent / dashed = Im(output).
    """
    M   = np.asarray(M, dtype=complex)
    pts = M @ _CIRCLE   # (2, N) complex

    re = pts.real
    im = pts.imag
    has_im = np.max(np.abs(im)) > 1e-8

    ax.cla()

    ax.scatter(re[0], re[1], c=_COLORS, s=5,
               linewidths=0, zorder=3, alpha=0.9)
    if has_im:
        ax.scatter(im[0], im[1], c=_COLORS, s=5,
                   linewidths=0, zorder=2, alpha=0.3)

    vecs_out = M @ _BASIS           # (2, 2) complex
    for i, (col, lbl) in enumerate(zip(_VC, _VL)):
        vr = vecs_out[:, i].real
        vi = vecs_out[:, i].imag

        if np.linalg.norm(vr) > 1e-8:
            ax.annotate('', xy=(vr[0], vr[1]), xytext=(0.0, 0.0),
                        arrowprops=dict(
                            arrowstyle='->', color=col, lw=2,
                            mutation_scale=14),
                        zorder=4)
            ax.text(vr[0] * 1.12, vr[1] * 1.12, lbl,
                    color=col, fontsize=7,
                    ha='center', va='center', zorder=5)

        if has_im and np.linalg.norm(vi) > 1e-8:
            ax.plot([0, vi[0]], [0, vi[1]], '--',
                    color=col, lw=1.5, alpha=0.6, zorder=3)
            ax.plot(vi[0], vi[1], 'o',
                    mfc='none', mec=col, ms=5, alpha=0.6, zorder=3)

    ax.axhline(0, color='grey', lw=0.4, ls='--', zorder=1)
    ax.axvline(0, color='grey', lw=0.4, ls='--', zorder=1)
    ax.set_aspect('equal')
    ax.set_title(title, fontsize=9, pad=3)
    ax.tick_params(labelsize=6)

    combined = np.hstack([re, im, vecs_out.real, vecs_out.imag])
    lim = max(float(np.max(np.abs(combined))), 0.5) * 1.3
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)


def _fmt_polar(z):
    """Format a complex scalar as 'magnitude∠phase°'."""
    return f'{abs(z):.3f}∠{np.degrees(np.angle(z)):+.1f}°'


def _fmt2x2(M, label):
    """Two-line polar-format display of a 2×2 complex matrix."""
    lines = [f'  [{_fmt_polar(M[r, 0])}  {_fmt_polar(M[r, 1])}]'
             for r in range(2)]
    return (f'{label}:\n' if label else '') + '\n'.join(lines)


def _draw_info(ax, H, U, s, Vt):
    """Render current SVD values as polar-form text."""
    ax.cla()
    ax.axis('off')

    S_mat = np.diag(s.astype(complex))
    body  = '\n\n'.join([
        _fmt2x2(H,     'H'),
        _fmt2x2(U,     'U'),
        _fmt2x2(S_mat, f'Σ  (σ₁={s[0]:.3f}, σ₂={s[1]:.3f})'),
        _fmt2x2(Vt,    'V†'),
    ])
    ax.text(0.03, 0.97, body,
            transform=ax.transAxes,
            fontsize=7.2, va='top', ha='left',
            fontfamily='monospace',
            bbox=dict(boxstyle='round', fc='#f9f9f9',
                      ec='#bbbbbb', lw=0.8))
    ax.set_title('SVD matrices  (mag∠phase°)', fontsize=9, pad=3)


# -----------------------------------------------------------------------
# App
# -----------------------------------------------------------------------

def main():
    mag0   = np.array([[1.5, 0.5], [0.3, 1.2]])
    phase0 = np.zeros((2, 2))

    fig = plt.figure(figsize=(19, 10))
    fig.suptitle('SVD Decomposition   H = U Σ V†   (complex)', fontsize=13)
    fig.text(0.5, 0.945,
             'Solid = Re(Mx)  ·  Translucent = Im(Mx)  ·'
             '  Solid arrows = Re  ·  Dashed = Im',
             ha='center', fontsize=8, color='#555555')

    gs = gridspec.GridSpec(
        3, 5, figure=fig,
        height_ratios=[10, 10, 4.5],
        hspace=0.6, wspace=0.38)

    ax_orig = fig.add_subplot(gs[0, 0])
    ax_vt   = fig.add_subplot(gs[0, 1])
    ax_svt  = fig.add_subplot(gs[0, 2])
    ax_h    = fig.add_subplot(gs[0, 3])
    ax_hhh  = fig.add_subplot(gs[0, 4])
    ax_u    = fig.add_subplot(gs[1, 0])
    ax_s    = fig.add_subplot(gs[1, 1])
    ax_us   = fig.add_subplot(gs[1, 2])
    ax_info = fig.add_subplot(gs[1, 3:5])

    # Sliders: row 0 = magnitudes, row 1 = phases, 4 cols (one per element)
    gs_sl = gridspec.GridSpecFromSubplotSpec(
        2, 4, subplot_spec=gs[2, :], hspace=0.8, wspace=0.3)

    mag_sliders   = []
    phase_sliders = []
    for col_i, (r, c) in enumerate(_INDICES):
        ax_m = fig.add_subplot(gs_sl[0, col_i])
        sl_m = Slider(ax_m, f'|H[{r},{c}]|', 0.0, 3.0,
                      valinit=float(mag0[r, c]),
                      valstep=0.05, color='steelblue')
        mag_sliders.append(sl_m)

        ax_p = fig.add_subplot(gs_sl[1, col_i])
        sl_p = Slider(ax_p, f'φ[{r},{c}]  (rad)', -np.pi, np.pi,
                      valinit=float(phase0[r, c]),
                      valstep=np.pi / 36,   # 5° steps
                      color='darkorange')
        phase_sliders.append(sl_p)

    def build_H():
        H = np.zeros((2, 2), dtype=complex)
        for k, (r, c) in enumerate(_INDICES):
            H[r, c] = mag_sliders[k].val * np.exp(
                1j * phase_sliders[k].val)
        return H

    def update(_=None):
        H = build_H()
        try:
            U, s, Vt = np.linalg.svd(H)
        except np.linalg.LinAlgError:
            return
        S  = np.diag(s.astype(complex))
        I  = np.eye(2)
        HH = H.conj().T @ H

        _draw(ax_orig, I,        'Original')
        _draw(ax_vt,   Vt,       'V†  (right-singular basis)')
        _draw(ax_svt,  S @ Vt,   'Σ V†')
        _draw(ax_h,    H,        'H = U Σ V†  (full)')
        _draw(ax_hhh,  HH,       'H†H  (eigenvalues = σ²)')
        _draw(ax_u,    U,        'U  (left-singular basis)')
        _draw(ax_s,    S,        'Σ  (scale by singular values)')
        _draw(ax_us,   U @ S,    'U Σ')
        _draw_info(ax_info, H, U, s, Vt)

        fig.canvas.draw_idle()

    for sl in mag_sliders + phase_sliders:
        sl.on_changed(update)

    update()
    plt.show()


if __name__ == '__main__':
    main()
