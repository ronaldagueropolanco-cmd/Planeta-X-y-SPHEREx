"""Dataset registry with in-memory access and on-disk persistence.

Datasets are plain dictionaries shared across the application.  Frame arrays
are large, so real (archive / upload) datasets are serialised to a compressed
``.npz`` file under ``uploads/cache/datasets/<id>`` while their metadata lives
in ``meta.json``.  Demo datasets are cheap to regenerate and stay in memory.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone

import numpy as np

from .frames import frame_from_serializable, frame_to_serializable

ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "uploads", "cache", "datasets")
)
os.makedirs(ROOT, exist_ok=True)

_lock = threading.RLock()
_memory: dict[str, dict] = {}
_demo_ready = False


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------
def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _safe_key(key: str) -> str:
    return key.replace(":", "_").replace("/", "_").replace("\\", "_")


def _dataset_dir(dataset_id: str) -> str:
    return os.path.join(ROOT, dataset_id)


def _meta_path(dataset_id: str) -> str:
    return os.path.join(_dataset_dir(dataset_id), "meta.json")


def _frames_path(dataset_id: str) -> str:
    return os.path.join(_dataset_dir(dataset_id), "frames.npz")


def _strip_large(dataset: dict) -> dict:
    meta = {k: v for k, v in dataset.items() if k not in ("frames", "cube")}
    meta["frame_keys"] = sorted(dataset.get("frames", {}).keys())
    meta["frame_meta"] = {
        key: _jsonable(frame.get("meta", {}))
        for key, frame in dataset.get("frames", {}).items()
    }
    return _jsonable(meta)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def save(dataset: dict) -> None:
    """Persist metadata and frame arrays for a dataset."""
    dataset_id = dataset["id"]
    folder = _dataset_dir(dataset_id)
    os.makedirs(folder, exist_ok=True)

    payload: dict[str, np.ndarray] = {}
    for key, frame in dataset.get("frames", {}).items():
        serial = frame_to_serializable(frame)
        prefix = _safe_key(key)
        for field, array in serial.items():
            payload[f"{prefix}__{field}"] = np.asarray(array)

    tmp = _frames_path(dataset_id) + ".part.npz"
    np.savez_compressed(tmp, **payload)
    os.replace(tmp, _frames_path(dataset_id))

    tmp_meta = _meta_path(dataset_id) + ".part"
    with open(tmp_meta, "w", encoding="utf-8") as handle:
        json.dump(_strip_large(dataset), handle, ensure_ascii=False, indent=1)
    os.replace(tmp_meta, _meta_path(dataset_id))


def _load(dataset_id: str) -> dict | None:
    meta_path = _meta_path(dataset_id)
    if not os.path.exists(meta_path):
        return None

    with open(meta_path, "r", encoding="utf-8") as handle:
        meta = json.load(handle)

    frames: dict[str, dict] = {}
    frame_meta = meta.pop("frame_meta", {})
    frame_keys = meta.pop("frame_keys", [])
    arrays = {}
    frames_path = _frames_path(dataset_id)
    if os.path.exists(frames_path):
        with np.load(frames_path, allow_pickle=False) as data:
            arrays = {name: data[name] for name in data.files}

    for key in frame_keys:
        prefix = _safe_key(key)
        if f"{prefix}__image" not in arrays:
            continue
        data = {
            "image": arrays[f"{prefix}__image"],
            "variance": arrays[f"{prefix}__variance"],
            "flags": arrays[f"{prefix}__flags"],
            "wave": arrays[f"{prefix}__wave"],
            "footprint": arrays[f"{prefix}__footprint"],
        }
        frames[key] = frame_from_serializable(data, frame_meta.get(key, {}))

    meta["frames"] = frames
    meta["cube"] = None
    return meta


def get(dataset_id: str) -> dict | None:
    with _lock:
        if dataset_id in _memory:
            return _memory[dataset_id]
    dataset = _load(dataset_id)
    if dataset is not None:
        with _lock:
            _memory[dataset_id] = dataset
    return dataset


def put(dataset: dict, *, persist: bool | None = None) -> dict:
    dataset.setdefault("created", datetime.now(timezone.utc).isoformat())
    if persist is None:
        persist = dataset.get("kind") != "demo"
    with _lock:
        _memory[dataset["id"]] = dataset
    if persist:
        save(dataset)
    return dataset


def delete(dataset_id: str) -> bool:
    with _lock:
        _memory.pop(dataset_id, None)
    folder = _dataset_dir(dataset_id)
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
        return True
    return False


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def summary(dataset: dict) -> dict:
    epochs = []
    frames = dataset.get("frames", {})
    per_epoch: dict[int, list[str]] = {}
    for key, frame in frames.items():
        try:
            epoch = int(key.split(":", 1)[0])
        except ValueError:
            continue
        detector = frame.get("meta", {}).get("detector", key.split(":", 1)[-1])
        per_epoch.setdefault(epoch, []).append(str(detector))

    for index, epoch in enumerate(dataset.get("epochs", [])):
        idx = int(epoch.get("index", index))
        epochs.append(
            {
                "index": idx,
                "day": epoch.get("day"),
                "mjd": epoch.get("mjd"),
                "date": epoch.get("date"),
                "detectors": sorted(per_epoch.get(idx, epoch.get("detectors", []))),
                "n_frames": len(per_epoch.get(idx, [])),
                "demo": bool(epoch.get("demo", False)),
            }
        )

    return {
        "id": dataset["id"],
        "kind": dataset.get("kind", "archive"),
        "label": dataset.get("label", dataset["id"]),
        "tag": dataset.get("tag", ""),
        "description": dataset.get("description", ""),
        "ra": dataset.get("ra"),
        "dec": dataset.get("dec"),
        "fov": dataset.get("fov"),
        "npix": dataset.get("npix", 192),
        "pixel_scale": dataset.get("pixel_scale"),
        "grid": dataset.get("grid", {}),
        "source": dataset.get("source", {}),
        "created": dataset.get("created"),
        "epochs": epochs,
        "n_epochs": len(epochs),
        "n_frames": len(frames),
        "bands_total": 102,
        "object_type": dataset.get("object_type"),
    }


def list_datasets() -> list[dict]:
    ensure_demo_datasets()
    items = []
    with _lock:
        for dataset in _memory.values():
            items.append(summary(dataset))

    if os.path.isdir(ROOT):
        for name in os.listdir(ROOT):
            if name in _memory:
                continue
            if os.path.exists(_meta_path(name)):
                dataset = get(name)
                if dataset:
                    items.append(summary(dataset))

    # Real archive datasets first, newest first, so a freshly ingested field is
    # the one the viewer opens by default.
    items.sort(key=lambda d: d["label"])
    items.sort(key=lambda d: d.get("created") or "", reverse=True)
    items.sort(key=lambda d: d["kind"] != "archive")
    return items


# ---------------------------------------------------------------------------
# Demo datasets
# ---------------------------------------------------------------------------
def ensure_demo_datasets() -> None:
    global _demo_ready
    with _lock:
        if _demo_ready:
            return
        _demo_ready = True
    from .demo import SCENARIOS, build_demo_dataset

    for scenario in SCENARIOS:
        dataset = build_demo_dataset(scenario["id"])
        dataset.setdefault("created", datetime.now(timezone.utc).isoformat())
        with _lock:
            _memory.setdefault(dataset["id"], dataset)
