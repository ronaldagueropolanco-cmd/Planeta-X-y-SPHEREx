"""Astronomical stretch, colour mapping and PNG encoding."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

COLORMAPS = {
    "mono": [
        (0.00, (0.00, 0.00, 0.00)),
        (0.55, (0.72, 0.75, 0.80)),
        (1.00, (1.00, 1.00, 1.00)),
    ],
    "ir": [
        (0.00, (0.01, 0.01, 0.03)),
        (0.22, (0.09, 0.07, 0.26)),
        (0.45, (0.36, 0.10, 0.42)),
        (0.68, (0.76, 0.24, 0.28)),
        (0.86, (0.98, 0.62, 0.24)),
        (1.00, (1.00, 0.98, 0.86)),
    ],
    "inferno": [
        (0.00, (0.00, 0.00, 0.04)),
        (0.25, (0.34, 0.06, 0.43)),
        (0.50, (0.73, 0.22, 0.33)),
        (0.75, (0.97, 0.56, 0.11)),
        (1.00, (0.99, 0.99, 0.75)),
    ],
    "ice": [
        (0.00, (0.01, 0.02, 0.05)),
        (0.35, (0.06, 0.24, 0.45)),
        (0.65, (0.24, 0.62, 0.80)),
        (0.85, (0.72, 0.90, 0.96)),
        (1.00, (1.00, 1.00, 1.00)),
    ],
}

COLORMAP_LABELS = {
    "mono": "Monocromo",
    "ir": "Infrarrojo",
    "inferno": "Inferno",
    "ice": "Cielo profundo",
}


def _cmap_lut(name: str, size: int = 256) -> np.ndarray:
    stops = COLORMAPS.get(name, COLORMAPS["mono"])
    xs = np.array([s[0] for s in stops])
    rgb = np.array([s[1] for s in stops])
    grid = np.linspace(0.0, 1.0, size)
    lut = np.stack([np.interp(grid, xs, rgb[:, i]) for i in range(3)], axis=1)
    return (np.clip(lut, 0, 1) * 255).astype(np.uint8)


def stretch(image: np.ndarray, method: str = "asinh", low: float = 0.5,
            high: float = 99.6, gamma: float = 1.0) -> np.ndarray:
    """Normalise a science array to [0, 1] with an astronomical stretch."""
    arr = np.asarray(image, dtype=np.float32)
    finite = np.isfinite(arr)
    if not finite.any():
        return np.zeros(arr.shape, dtype=np.float32)

    values = arr[finite]
    p_low, p_high = np.percentile(values, [low, high])
    if not np.isfinite(p_low) or not np.isfinite(p_high) or p_high <= p_low:
        p_low = float(values.min())
        p_high = float(values.max()) if values.max() > p_low else p_low + 1e-6

    norm = np.clip((arr - p_low) / (p_high - p_low), 0.0, 1.0)
    norm[~finite] = 0.0

    if method == "log":
        out = np.log1p(norm * 60.0) / np.log1p(60.0)
    elif method == "sqrt":
        out = np.sqrt(norm)
    elif method == "linear":
        out = norm
    elif method == "zscale":
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median))) or 1.0
        z1 = max(p_low, median - 2.5 * mad)
        z2 = min(p_high, median + 7.0 * mad)
        out = np.clip((arr - z1) / max(z2 - z1, 1e-6), 0.0, 1.0)
        out[~finite] = 0.0
    else:  # asinh, the standard for near-infrared survey data
        beta = 8.0
        out = np.arcsinh(norm * beta) / np.arcsinh(beta)

    if gamma and gamma != 1.0:
        out = np.power(np.clip(out, 0, 1), 1.0 / gamma)
    return out.astype(np.float32)


def colorize(normalised: np.ndarray, cmap: str = "mono") -> np.ndarray:
    lut = _cmap_lut(cmap)
    idx = (np.clip(normalised, 0.0, 1.0) * 255).astype(np.uint8)
    return lut[idx]


def png_bytes(rgb: np.ndarray, size: int | None = None) -> bytes:
    image = Image.fromarray(np.asarray(rgb, dtype=np.uint8), mode="RGB")
    if size:
        image = image.resize((size, size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer.getvalue()


def render_image(image: np.ndarray, *, cmap: str = "mono", method: str = "asinh",
                 low: float = 0.5, high: float = 99.6, gamma: float = 1.0,
                 size: int = 768) -> bytes:
    normalised = stretch(image, method=method, low=low, high=high, gamma=gamma)
    # FITS row 0 is the south edge; flip so north is up in the PNG.
    return png_bytes(colorize(np.flipud(normalised), cmap=cmap), size=size)


def render_difference(diff: np.ndarray, *, variance: np.ndarray | None = None,
                      size: int = 768, scale_sigma: float = 6.0,
                      color_floor: float = 3.0) -> bytes:
    """Bipolar residual map: cyan = faded/removed, orange = appeared/brightened.

    When a variance map is supplied the residual is converted to significance,
    which suppresses the correlated background residuals that otherwise paint
    almost the whole frame.
    """
    arr = np.asarray(diff, dtype=np.float32)
    finite = np.isfinite(arr)
    if not finite.any():
        return png_bytes(np.zeros((arr.shape[0], arr.shape[1], 3), dtype=np.uint8), size=size)

    values = arr[finite]
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    p16, p84 = np.percentile(values, [16, 84])
    # The 16-84 percentile half-width is more robust than the MAD when the
    # residuals have broad wings from bright stars.
    sigma = max(1.4826 * mad, 0.5 * float(p84 - p16), 1e-9)

    if variance is not None:
        var = np.asarray(variance, dtype=np.float64)
        sigma_pixel = np.sqrt(np.maximum(var, sigma * sigma))
        significance = np.zeros_like(arr, dtype=np.float64)
        significance[finite] = (arr[finite] - median) / sigma_pixel[finite]
    else:
        significance = np.zeros_like(arr, dtype=np.float64)
        significance[finite] = (arr[finite] - median) / sigma

    span = max(float(scale_sigma) - float(color_floor), 0.1)
    scaled = np.sign(significance) * np.clip(
        (np.abs(significance) - float(color_floor)) / span, 0.0, 1.0
    )
    scaled = np.arcsinh(scaled * 3.0) / np.arcsinh(3.0)
    scaled = np.clip(scaled, -1.0, 1.0)

    rgb = np.zeros(arr.shape + (3,), dtype=np.float32)
    rgb[..., 0] = 0.035
    rgb[..., 1] = 0.042
    rgb[..., 2] = 0.055

    positive = scaled > 0.02
    vp = scaled[positive]
    rgb[..., 0][positive] = np.clip(0.20 + 1.55 * vp, 0, 1)
    rgb[..., 1][positive] = np.clip(0.12 + 0.95 * vp ** 1.5, 0, 1)
    rgb[..., 2][positive] = np.clip(0.10 + 0.35 * vp ** 3, 0, 1)

    negative = scaled < -0.02
    vn = -scaled[negative]
    rgb[..., 0][negative] = np.clip(0.06 + 0.25 * vn ** 2, 0, 1)
    rgb[..., 1][negative] = np.clip(0.16 + 1.05 * vn, 0, 1)
    rgb[..., 2][negative] = np.clip(0.28 + 1.20 * vn, 0, 1)

    return png_bytes(np.flipud(np.clip(rgb, 0, 1) * 255).astype(np.uint8), size=size)
