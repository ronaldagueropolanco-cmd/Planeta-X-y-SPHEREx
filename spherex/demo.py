"""Offline demonstration fields.

These are physically motivated simulations, clearly labelled as demo data in the
interface. They keep the tool useful when the IRSA archive is unreachable and
they expose all 102 spectral channels for every epoch.
"""

from __future__ import annotations

import numpy as np

from .bands import WAVELENGTHS

SIZE = 192

SCENARIOS = [
    {
        "id": "neo_asteroid",
        "title": "Asteroide NEO 2025 SPX-42",
        "tag": "Defensa planetaria",
        "description": "Objeto rocoso que se desplaza entre barridos semestrales.",
        "ra": 56.75,
        "dec": 24.12,
        "object_type": "Asteroide cercano a la Tierra",
        "detector": "D3",
        "wavelength_um": 2.1,
    },
    {
        "id": "supernova_transient",
        "title": "Supernova 2025spx",
        "tag": "Cosmología",
        "description": "Estallido en una galaxia espiral, ausente en la primera época.",
        "ra": 189.21,
        "dec": 13.09,
        "object_type": "Supernova tipo Ia",
        "detector": "D4",
        "wavelength_um": 3.15,
    },
    {
        "id": "brown_dwarf",
        "title": "Enana marrón WISEA J1854",
        "tag": "Exoplanetas y estrellas",
        "description": "Objeto subestelar frío con alto movimiento propio, brillante a 4.5 µm.",
        "ra": 283.45,
        "dec": -21.32,
        "object_type": "Enana marrón tipo Y",
        "detector": "D6",
        "wavelength_um": 4.55,
    },
    {
        "id": "active_comet",
        "title": "Cometa P/2025 SPHEREx-1",
        "tag": "Sistema solar",
        "description": "Cometa con coma de hielo y absorción de agua a 3.05 µm.",
        "ra": 83.82,
        "dec": -5.39,
        "object_type": "Cometa activo",
        "detector": "D4",
        "wavelength_um": 3.05,
    },
]

SCENARIO_BY_ID = {s["id"]: s for s in SCENARIOS}


def _gaussian(shape, cx, cy, fwhm, flux):
    y, x = np.mgrid[0:shape[0], 0:shape[1]]
    sigma = fwhm / 2.3548
    return flux * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma ** 2))


def _star_field(seed, count, size=SIZE, fmin=1.2, fmax=3.2):
    rng = np.random.default_rng(seed)
    return [
        (rng.uniform(8, size - 8), rng.uniform(8, size - 8), 10 ** rng.uniform(fmin, fmax))
        for _ in range(count)
    ]


def _base_images(scenario_id: str):
    shape = (SIZE, SIZE)
    if scenario_id == "neo_asteroid":
        dates = ["2025-04-15", "2025-10-18", "2026-04-20"]
        mjds = [60780.0, 60966.5, 61150.0]
        positions = [(64.0, 70.0), (101.0, 103.0), (138.0, 137.0)]
        stars = _star_field(101, 85)
        images = []
        for k, (pos, seed) in enumerate(zip(positions, (200, 217, 234))):
            img = np.zeros(shape, dtype=np.float32)
            for sx, sy, flux in stars:
                img += _gaussian(shape, sx, sy, 2.4, flux)
            img += _gaussian(shape, pos[0], pos[1], 2.5, 380.0)
            img += np.random.default_rng(seed).normal(12.0, 2.8, shape)
            images.append((img, pos))
        return dates, mjds, images

    if scenario_id == "supernova_transient":
        dates = ["2025-03-10", "2025-09-12", "2026-03-15"]
        mjds = [60744.0, 60930.0, 61114.0]
        stars = _star_field(202, 70, fmin=1.3, fmax=3.0)
        y, x = np.mgrid[0:SIZE, 0:SIZE]
        galaxy = 280.0 * np.exp(-(((x - SIZE / 2) * 1.2) ** 2 + ((y - SIZE / 2) * 0.7) ** 2) / (2 * 14.0 ** 2))
        fluxes = [0.0, 750.0, 180.0]
        images = []
        for k, (sn_flux, seed) in enumerate(zip(fluxes, (300, 319, 338))):
            img = galaxy.copy()
            for sx, sy, flux in stars:
                img += _gaussian(shape, sx, sy, 2.3, flux)
            if sn_flux > 0:
                img += _gaussian(shape, 106.0, 88.0, 2.6, sn_flux)
            img += np.random.default_rng(seed).normal(15.0, 3.2, shape)
            images.append((img, (106.0, 88.0)))
        return dates, mjds, images

    if scenario_id == "brown_dwarf":
        dates = ["2025-05-02", "2025-11-04", "2026-05-08"]
        mjds = [60797.0, 60983.0, 61168.0]
        positions = [(51.0, 120.0), (79.0, 106.0), (107.0, 93.0)]
        stars = _star_field(303, 60, fmin=1.0, fmax=2.8)
        images = []
        for pos, seed in zip(positions, (400, 413, 426)):
            img = np.zeros(shape, dtype=np.float32)
            for sx, sy, flux in stars:
                img += _gaussian(shape, sx, sy, 2.2, flux)
            img += _gaussian(shape, pos[0], pos[1], 2.4, 420.0)
            img += np.random.default_rng(seed).normal(10.0, 2.5, shape)
            images.append((img, pos))
        return dates, mjds, images

    dates = ["2025-01-20", "2025-07-22", "2026-01-25"]
    mjds = [60695.0, 60878.0, 61065.0]
    positions = [(52.0, 60.0), (94.0, 98.0), (135.0, 131.0)]
    stars = _star_field(404, 75, fmin=1.2, fmax=3.1)
    images = []
    for pos, seed in zip(positions, (500, 523, 546)):
        img = np.zeros(shape, dtype=np.float32)
        for sx, sy, flux in stars:
            img += _gaussian(shape, sx, sy, 2.3, flux)
        nucleus = _gaussian(shape, pos[0], pos[1], 2.6, 320.0)
        coma = _gaussian(shape, pos[0], pos[1], 14.0, 210.0)
        tail = _gaussian(shape, pos[0] - 12.0, pos[1] - 12.0, 22.0, 110.0)
        img += nucleus + coma + tail
        img += np.random.default_rng(seed).normal(14.0, 3.0, shape)
        images.append((img, pos))
    return dates, mjds, images


def _background_factor(wavelength: float) -> float:
    return 1.0 / max(wavelength, 0.4) ** 0.8


def _candidate_factor(scenario_id: str, wavelength: float) -> float:
    base = _background_factor(wavelength)
    if scenario_id == "neo_asteroid":
        ice = 0.55 * np.exp(-((wavelength - 3.05) ** 2) / (2 * 0.18 ** 2))
        return base * (1.0 - ice) + 0.4 * np.exp((wavelength - 4.0) * 0.6)
    if scenario_id == "supernova_transient":
        return base + 0.6 * np.exp(-((wavelength - 1.28) ** 2) / (2 * 0.05 ** 2)) \
            + 0.4 * np.exp(-((wavelength - 2.16) ** 2) / (2 * 0.08 ** 2))
    if scenario_id == "brown_dwarf":
        ch4 = 0.7 * np.exp(-((wavelength - 3.3) ** 2) / (2 * 0.25 ** 2))
        factor = base * (1.0 - ch4)
        if wavelength > 4.2:
            factor *= 1.0 + 1.8 * (wavelength - 4.2)
        return factor
    ice_h2o = 0.65 * np.exp(-((wavelength - 3.05) ** 2) / (2 * 0.22 ** 2))
    ice_co2 = 0.45 * np.exp(-((wavelength - 4.26) ** 2) / (2 * 0.08 ** 2))
    return base * (1.0 - ice_h2o - ice_co2)


def build_demo_dataset(scenario_id: str) -> dict:
    scenario = SCENARIO_BY_ID.get(scenario_id, SCENARIOS[0])
    dates, mjds, images = _base_images(scenario["id"])
    epochs = []
    frames = {}
    for epoch_index, ((image, pos), date, mjd) in enumerate(zip(images, dates, mjds)):
        epochs.append({
            "index": epoch_index,
            "day": int(mjd),
            "mjd": mjd,
            "date": date,
            "detectors": ["D1", "D2", "D3", "D4", "D5", "D6"],
            "detectors_available": ["D1", "D2", "D3", "D4", "D5", "D6"],
            "observations": [],
            "demo": True,
        })
        frames[f"{epoch_index}:ALL"] = {
            "image": image.astype(np.float32),
            "variance": np.full(image.shape, 4.0, dtype=np.float32),
            "flags": np.zeros(image.shape, dtype=np.int32),
            "wave": np.full(image.shape, scenario["wavelength_um"], dtype=np.float32),
            "footprint": np.ones(image.shape, dtype=np.float32),
            "meta": {"detector": "ALL", "exptime": 300.0, "demo": True},
        }

    dataset = {
        "id": f"demo_{scenario['id']}",
        "kind": "demo",
        "label": scenario["title"],
        "tag": scenario["tag"],
        "description": scenario["description"],
        "object_type": scenario["object_type"],
        "ra": scenario["ra"],
        "dec": scenario["dec"],
        "fov": 0.35,
        "npix": SIZE,
        "pixel_scale": 6.15,
        "bands": WAVELENGTHS,
        "epochs": epochs,
        "frames": frames,
        "grid": {"ra": scenario["ra"], "dec": scenario["dec"], "fov": 0.35, "npix": SIZE},
        "source": {"archive": "Demo", "query": "simulación"},
        "candidate_positions": [pos for _, pos in images],
        "cube": None,
    }
    return dataset


def ensure_demo_cube(dataset: dict) -> np.ndarray:
    """Build the 102-channel cube on first use (kept in memory)."""
    if dataset.get("cube") is not None:
        return dataset["cube"]

    scenario_id = dataset["id"].replace("demo_", "")
    wavelengths = np.asarray(WAVELENGTHS, dtype=np.float32)
    bg = np.array([_background_factor(w) for w in wavelengths], dtype=np.float32)
    cand = np.array([_candidate_factor(scenario_id, w) for w in wavelengths], dtype=np.float32)
    bg /= max(bg.max(), 1e-6)
    cand /= max(cand.max(), 1e-6)

    cube = []
    for epoch_index, epoch in enumerate(dataset["epochs"]):
        frame = dataset["frames"][f"{epoch_index}:ALL"]
        base = frame["image"].astype(np.float32)
        base = np.clip(base - np.median(base), 0, None)
        peak = max(float(np.percentile(base, 99.7)), 1e-6)
        base /= peak

        pos = dataset["candidate_positions"][epoch_index]
        mask = _gaussian(base.shape, pos[0], pos[1], 7.0, 1.0)
        mask = np.clip(mask / max(mask.max(), 1e-6), 0, 1).astype(np.float32)

        volume = base[None, :, :] * bg[:, None, None]
        volume += mask[None, :, :] * base[None, :, :] * np.clip(cand - bg, 0, None)[:, None, None]
        rng = np.random.default_rng(900 + epoch_index)
        volume += rng.normal(0.0, 0.004, volume.shape).astype(np.float32)
        cube.append(volume.astype(np.float32))

    dataset["cube"] = cube
    return cube
