#!/usr/bin/env python3

import re
import numpy as np
import matplotlib.pyplot as plt

data = """
eigenvalues:    1.0000    1.0000    0.0000   -0.0000
evec[0] (lam=  1.0000):  -0.029+0.054j   0.873-0.002j   0.013-0.099j  -0.013-0.473j
evec[1] (lam=  1.0000):   0.006-0.278j   0.003-0.117j   0.953-0.000j   0.000-0.000j
evec[2] (lam=  0.0000):   0.032-0.004j   0.013-0.469j  -0.059-0.010j   0.881+0.000j
evec[3] (lam= -0.0000):   0.958+0.000j  -0.010+0.064j   0.002-0.278j  -0.003-0.018j
"""


data="""
eigenvalues:    1.0000    1.0000    0.0000   -0.0000
evec[0] (lam=  1.0000):   0.880+0.000j   0.000+0.156j  -0.406+0.000j   0.191-0.000j
evec[1] (lam=  1.0000):   0.059+0.000j  -0.000+0.502j   0.609-0.000j   0.612+0.000j
evec[2] (lam=  0.0000):  -0.253-0.000j   0.000-0.462j  -0.365-0.000j   0.767+0.000j
evec[3] (lam= -0.0000):  -0.000+0.398j   0.714-0.000j   0.000+0.576j   0.000-0.025j
"""



# Extract eigenvectors
evec_pattern = re.compile(
    r"evec\[(\d+)\].*?:\s+(.*)"
)

eigenvectors = []

for line in data.splitlines():
    m = evec_pattern.match(line.strip())
    if not m:
        continue

    idx = int(m.group(1))
    values = [complex(x) for x in m.group(2).split()]
    eigenvectors.append((idx, values))

# Plot
fig, ax = plt.subplots(figsize=(8, 8))

colors = plt.cm.tab10(np.linspace(0, 1, len(eigenvectors)))

for color, (evec_idx, vec) in zip(colors, eigenvectors):
    real = [z.real for z in vec]
    imag = [z.imag for z in vec]

    ax.scatter(
        real,
        imag,
        s=100,
        color=color,
        label=f"Eigenvector {evec_idx}"
    )

    for i, z in enumerate(vec):
        ax.annotate(
            f"{evec_idx}:{i}",
            (z.real, z.imag),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=8
        )

ax.axhline(0, color='k', linewidth=0.5)
ax.axvline(0, color='k', linewidth=0.5)

ax.set_xlabel("Real")
ax.set_ylabel("Imaginary")
ax.set_title("Eigenvector Entries on Complex Plane")
ax.grid(True)
ax.set_aspect('equal', adjustable='box')
ax.legend()

plt.tight_layout()
plt.show()