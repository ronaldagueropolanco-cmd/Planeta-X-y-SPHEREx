"""SPHEREx spectral band definitions (102 channels, 0.75 - 5.0 microns).

SPHEREx uses six linear-variable-filter (LVF) detector arrays. Each detector
covers a contiguous near-infrared window and is sampled with 17 reference
channels, giving 6 x 17 = 102 channels.
"""

from __future__ import annotations

import numpy as np

CHANNELS_PER_DETECTOR = 17

DETECTORS = [
    {"id": "D1", "name": "Detector 1", "wmin": 0.7455, "wmax": 1.1167},
    {"id": "D2", "name": "Detector 2", "wmin": 1.1033, "wmax": 1.6499},
    {"id": "D3", "name": "Detector 3", "wmin": 1.6400, "wmax": 2.4200},
    {"id": "D4", "name": "Detector 4", "wmin": 2.4027, "wmax": 3.8473},
    {"id": "D5", "name": "Detector 5", "wmin": 3.8113, "wmax": 4.4300},
    {"id": "D6", "name": "Detector 6", "wmin": 4.4200, "wmax": 5.0000},
]

DETECTOR_BY_ID = {d["id"]: d for d in DETECTORS}


def _build_wavelengths() -> list[float]:
    values: list[float] = []
    for det in DETECTORS:
        values.extend(np.geomspace(det["wmin"], det["wmax"], CHANNELS_PER_DETECTOR))
    return [round(float(w), 5) for w in values]


WAVELENGTHS = _build_wavelengths()


def band_table() -> list[dict]:
    """Metadata for the 102 reference channels."""
    out = []
    for index, wavelength in enumerate(WAVELENGTHS):
        det_index = index // CHANNELS_PER_DETECTOR
        det = DETECTORS[det_index]
        out.append(
            {
                "index": index,
                "channel": index + 1,
                "wavelength_um": wavelength,
                "detector": det["id"],
                "detector_index": index % CHANNELS_PER_DETECTOR,
            }
        )
    return out


BANDS = band_table()


def detector_for_wavelength(wavelength_um: float) -> str | None:
    for det in DETECTORS:
        if det["wmin"] - 1e-3 <= wavelength_um <= det["wmax"] + 1e-3:
            return det["id"]
    return None


def nearest_band(wavelength_um: float) -> int | None:
    """Return the index of the closest reference channel."""
    if not np.isfinite(wavelength_um):
        return None
    arr = np.asarray(WAVELENGTHS)
    idx = int(np.argmin(np.abs(arr - wavelength_um)))
    # Reject wavelengths far outside the instrument range.
    if abs(arr[idx] - wavelength_um) > 0.12:
        return None
    return idx
