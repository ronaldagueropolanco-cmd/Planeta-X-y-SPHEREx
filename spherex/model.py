"""Learned candidate classifier.

The viewer already has a physics-based detector (difference imaging, dipole
pairing, variance maps).  This module adds a small supervised model on top of
it: a multinomial logistic regression that estimates whether a detection looks
like a moving object, a transient, a variable star or a subtraction artefact.

Training data come from three places:

* demo fields, where the injected source and its position are known;
* real SPHEREx archive fields, weakly labelled with the Gaia DR3 / AllWISE
  cross-match (a residual on a known star is an artefact, a clean dipole is a
  moving candidate);
* data augmentation, used only to balance the training set.  Every metric in
  ``instance/models/candidate_model.json`` records how many samples came from
  each source so the result is not overstated.

The model is intentionally small and interpretable.  It is a screening aid, not
a discovery claim, and the assistant is instructed to say so.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

import numpy as np

from . import config

MODEL_DIR = os.path.abspath(
    os.environ.get("SPHEREX_MODEL_DIR", os.path.join(config.INSTANCE_DIR, "models"))
)
MODEL_NPZ = os.path.join(MODEL_DIR, "candidate_model.npz")
MODEL_JSON = os.path.join(MODEL_DIR, "candidate_model.json")

CLASSES = ["artifact", "moving", "transient", "variable"]
CLASS_LABELS = {
    "artifact": "Artefacto / estrella mal restada",
    "moving": "Objeto en movimiento",
    "transient": "Transitorio",
    "variable": "Variable",
}

FEATURES = [
    "log_snr",
    "log_area",
    "abs_log_flux_ratio",
    "log_delta_flux",
    "has_motion",
    "log_motion_rate",
    "log_baseline_days",
    "log_bands",
    "log_threshold",
    "gaia_match",
    "gaia_sep_score",
    "wise_match",
    "wise_sep_score",
    "catalog_unknown",
    "log_pixel_scale",
]

FEATURE_LABELS = {
    "log_snr": "Señal-ruido",
    "log_area": "Área del residuo",
    "abs_log_flux_ratio": "Cambio relativo de flujo",
    "log_delta_flux": "Flujo diferencial",
    "has_motion": "Dipolo / movimiento",
    "log_motion_rate": "Tasa de movimiento",
    "log_baseline_days": "Línea base temporal",
    "log_bands": "Canales usados",
    "log_threshold": "Umbral de detección",
    "gaia_match": "Contrapartida Gaia",
    "gaia_sep_score": "Separación Gaia",
    "wise_match": "Contrapartida WISE",
    "wise_sep_score": "Separación WISE",
    "catalog_unknown": "Catálogo no disponible",
    "log_pixel_scale": "Escala de píxel",
}

_MODEL_CACHE: dict | None = None


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------
def _safe_log(value: float, floor: float = 0.0) -> float:
    return float(math.log10(max(float(value), floor) + 1.0))


def features_from_candidate(candidate: dict, context: dict | None = None) -> np.ndarray:
    context = context or {}
    snr = abs(float(candidate.get("snr") or 0.0))
    area = abs(float(candidate.get("area") or 0.0))
    flux_a = abs(float(candidate.get("flux_a") or 0.0))
    flux_b = abs(float(candidate.get("flux_b") or 0.0))
    delta = abs(float(candidate.get("delta_flux") or (flux_b - flux_a)))
    ratio = abs(math.log10((flux_b + 1e-9) / (flux_a + 1e-9)))
    motion = candidate.get("motion") or {}
    rate = abs(float(motion.get("rate_arcsec_day") or 0.0))
    baseline = float(context.get("baseline_days") or motion.get("baseline_days") or 1.0)
    bands = float(context.get("bands") or len(context.get("matched_bands") or []) or 1)
    threshold = float(context.get("threshold") or 4.0)
    pixel_scale = float(context.get("pixel_scale") or 6.15)

    catalog = candidate.get("catalog") or {}
    available = catalog.get("available")
    gaia = catalog.get("gaia") or {}
    wise = catalog.get("wise") or {}
    gaia_sep = gaia.get("separation_arcsec")
    wise_sep = wise.get("separation_arcsec")
    max_sep = float(catalog.get("max_sep_arcsec") or 8.0)

    def match_score(sep, present):
        if sep is None or not present:
            return 0.0
        return 1.0 / (1.0 + max(float(sep), 0.0))

    values = [
        _safe_log(snr),
        _safe_log(area),
        min(ratio, 4.0),
        _safe_log(delta),
        1.0 if motion else 0.0,
        _safe_log(rate),
        _safe_log(baseline),
        _safe_log(bands),
        _safe_log(threshold),
        1.0 if (gaia_sep is not None and float(gaia_sep) <= max_sep) else 0.0,
        match_score(gaia_sep, bool(gaia)),
        1.0 if (wise_sep is not None and float(wise_sep) <= max_sep) else 0.0,
        match_score(wise_sep, bool(wise)),
        0.0 if available else 1.0,
        _safe_log(pixel_scale),
    ]
    return np.asarray(values, dtype=np.float64)


def context_from_result(result: dict, dataset: dict | None = None) -> dict:
    return {
        "baseline_days": result.get("baseline_days"),
        "bands": len(result.get("matched_bands") or []),
        "threshold": result.get("threshold_sigma"),
        "pixel_scale": (dataset or {}).get("pixel_scale"),
        "kind": (dataset or {}).get("kind"),
    }


# ---------------------------------------------------------------------------
# Training-set construction
# ---------------------------------------------------------------------------
_MOVING_SCENARIOS = {"neo_asteroid", "brown_dwarf", "active_comet"}


def _distance_px(a: tuple, b: tuple) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _demo_samples() -> tuple[list[np.ndarray], list[int], dict]:
    from . import analysis, store

    xs: list[np.ndarray] = []
    ys: list[int] = []
    counts = {"demo": 0}
    for summary in store.list_datasets():
        if summary.get("kind") != "demo":
            continue
        dataset = store.get(summary["id"])
        if dataset is None:
            continue
        scenario = dataset["id"].replace("demo_", "")
        positions = dataset.get("candidate_positions") or []
        n_epochs = len(dataset.get("epochs", []))
        for epoch_a in range(n_epochs):
            for epoch_b in range(epoch_a + 1, n_epochs):
                for threshold in (4.0, 6.0):
                    try:
                        result = analysis.detect_candidates(
                            dataset, epoch_a, epoch_b, threshold_sigma=threshold, min_area=3
                        )
                    except Exception:
                        continue
                    context = context_from_result(result, dataset)
                    truth = [positions[epoch_a], positions[epoch_b]]
                    for candidate in result.get("candidates", []):
                        position = candidate.get("position") or {}
                        point = (position.get("x", -999), position.get("y", -999))
                        near = min(_distance_px(point, item) for item in truth) <= 12.0
                        if scenario == "supernova_transient":
                            label = "transient" if near else "artifact"
                        elif scenario in _MOVING_SCENARIOS:
                            label = (
                                "moving"
                                if near and candidate.get("type") == "moving"
                                else "artifact"
                            )
                        else:
                            label = "artifact"
                        xs.append(features_from_candidate(candidate, context))
                        ys.append(CLASSES.index(label))
                        counts["demo"] += 1
    return xs, ys, counts


def _archive_samples(include_archive: bool) -> tuple[list[np.ndarray], list[int], dict]:
    from . import analysis, crossmatch, store

    xs: list[np.ndarray] = []
    ys: list[int] = []
    counts = {"archive": 0, "archive_labelled": 0}
    if not include_archive:
        return xs, ys, counts
    for summary in store.list_datasets():
        if summary.get("kind") != "archive":
            continue
        dataset = store.get(summary["id"])
        if dataset is None or len(dataset.get("epochs", [])) < 2:
            continue
        pairs = [(0, len(dataset["epochs"]) - 1)]
        if len(dataset["epochs"]) > 2:
            pairs.append((0, 1))
        for epoch_a, epoch_b in pairs:
            for threshold in (4.0, 6.0):
                try:
                    result = analysis.detect_candidates(
                        dataset, epoch_a, epoch_b, threshold_sigma=threshold, min_area=3
                    )
                except Exception:
                    continue
                candidates = result.get("candidates", [])
                if not candidates:
                    continue
                try:
                    crossmatch.annotate_candidates(candidates, dataset)
                except Exception:
                    continue
                context = context_from_result(result, dataset)
                for candidate in candidates:
                    catalog = candidate.get("catalog") or {}
                    if not catalog.get("available"):
                        continue
                    matched = bool(catalog.get("has_counterpart"))
                    kind = candidate.get("type")
                    if kind == "moving":
                        label = "artifact" if matched else "moving"
                    elif kind == "transient":
                        label = "artifact" if matched else "transient"
                    elif kind == "variable":
                        label = "variable" if matched else "transient"
                    else:
                        label = "artifact"
                    xs.append(features_from_candidate(candidate, context))
                    ys.append(CLASSES.index(label))
                    counts["archive"] += 1
                    if not matched:
                        counts["archive_labelled"] += 1
    return xs, ys, counts


def _synthetic_sample(label: str, rng: np.random.Generator) -> np.ndarray:
    """Physically motivated fallback used only for balancing."""

    def between(low, high):
        return float(rng.uniform(low, high))

    snr = between(4.0, 10.0) if label == "artifact" else between(5.0, 20.0)
    area = between(1.0, 10.0) if label == "artifact" else between(3.0, 35.0)
    if label == "artifact":
        ratio = between(0.1, 1.6)
        delta = between(0.05, 1.4)
        motion = 0.0
        rate = 0.0
        gaia = 1.0 if rng.random() < 0.85 else 0.0
        wise = 1.0 if rng.random() < 0.7 else 0.0
        unknown = 0.0
    elif label == "moving":
        ratio = between(0.0, 0.45)
        delta = between(0.1, 1.2)
        motion = 1.0
        rate = between(0.1, 5.0)
        gaia = 1.0 if rng.random() < 0.1 else 0.0
        wise = 1.0 if rng.random() < 0.1 else 0.0
        unknown = 0.25 if rng.random() < 0.3 else 0.0
    elif label == "transient":
        ratio = between(0.4, 3.0)
        delta = between(0.5, 2.2)
        motion = 0.0
        rate = 0.0
        gaia = 1.0 if rng.random() < 0.1 else 0.0
        wise = 1.0 if rng.random() < 0.1 else 0.0
        unknown = 0.3 if rng.random() < 0.35 else 0.0
    else:  # variable
        ratio = between(0.1, 1.2)
        delta = between(0.2, 1.4)
        motion = 0.0
        rate = 0.0
        gaia = 1.0 if rng.random() < 0.75 else 0.0
        wise = 1.0 if rng.random() < 0.6 else 0.0
        unknown = 0.0

    sep_gaia = between(0.1, 6.0) if gaia else None
    sep_wise = between(0.1, 7.0) if wise else None
    max_sep = 8.0
    values = [
        _safe_log(snr),
        _safe_log(area),
        ratio,
        _safe_log(delta),
        motion,
        _safe_log(rate),
        _safe_log(between(30.0, 420.0)),
        _safe_log(between(1.0, 10.0)),
        _safe_log(between(4.0, 8.0)),
        gaia,
        1.0 / (1.0 + sep_gaia) if sep_gaia is not None else 0.0,
        wise,
        1.0 / (1.0 + sep_wise) if sep_wise is not None else 0.0,
        unknown,
        _safe_log(6.15),
    ]
    return np.asarray(values, dtype=np.float64)


def _augment(xs: list[np.ndarray], ys: list[int], target: int = 160,
             seed: int = 42) -> tuple[list[np.ndarray], list[int], dict]:
    rng = np.random.default_rng(seed)
    xs = list(xs)
    ys = list(ys)
    counts = {"augmented": 0}
    for label_index, label in enumerate(CLASSES):
        current = [x for x, y in zip(xs, ys) if y == label_index]
        missing = target - len(current)
        if missing <= 0:
            continue
        if current:
            for _ in range(missing):
                base = current[int(rng.integers(0, len(current)))]
                noise = rng.normal(0.0, 0.03, size=base.shape)
                xs.append(base + noise)
                ys.append(label_index)
                counts["augmented"] += 1
        else:
            for _ in range(target):
                xs.append(_synthetic_sample(label, rng))
                ys.append(label_index)
                counts["augmented"] += 1
    return xs, ys, counts


# ---------------------------------------------------------------------------
# Optimisation
# ---------------------------------------------------------------------------
def _one_hot(y: np.ndarray, n_classes: int) -> np.ndarray:
    out = np.zeros((y.size, n_classes), dtype=np.float64)
    out[np.arange(y.size), y] = 1.0
    return out


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.maximum(np.sum(exp, axis=1, keepdims=True), 1e-12)


def _fit_softmax(
    x: np.ndarray,
    y: np.ndarray,
    *,
    epochs: int = 700,
    learning_rate: float = 0.15,
    l2: float = 2e-3,
) -> tuple[np.ndarray, np.ndarray]:
    n_samples, n_features = x.shape
    n_classes = len(CLASSES)
    weights = np.zeros((n_features, n_classes), dtype=np.float64)
    bias = np.zeros(n_classes, dtype=np.float64)
    targets = _one_hot(y, n_classes)

    # Balanced class weights so a dominant artefact class does not win.
    class_counts = np.bincount(y, minlength=n_classes).astype(np.float64)
    class_weights = np.where(class_counts > 0, n_samples / (n_classes * class_counts), 0.0)
    sample_weights = class_weights[y]

    velocity_w = np.zeros_like(weights)
    velocity_b = np.zeros_like(bias)
    beta = 0.9
    for _epoch in range(epochs):
        logits = x @ weights + bias
        probs = _softmax(logits)
        error = (probs - targets) * sample_weights[:, None] / max(n_samples, 1)
        grad_w = x.T @ error + l2 * weights
        grad_b = error.sum(axis=0)
        velocity_w = beta * velocity_w + (1.0 - beta) * grad_w
        velocity_b = beta * velocity_b + (1.0 - beta) * grad_b
        weights -= learning_rate * velocity_w
        bias -= learning_rate * velocity_b
    return weights, bias


def _metrics(
    x: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
    bias: np.ndarray,
) -> dict:
    probs = _softmax(x @ weights + bias)
    predicted = probs.argmax(axis=1)
    n_classes = len(CLASSES)
    confusion = np.zeros((n_classes, n_classes), dtype=int)
    for actual, pred in zip(y, predicted):
        confusion[int(actual), int(pred)] += 1
    per_class = {}
    for index, label in enumerate(CLASSES):
        tp = int(confusion[index, index])
        fp = int(confusion[:, index].sum() - tp)
        fn = int(confusion[index, :].sum() - tp)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[label] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "support": int(confusion[index, :].sum()),
        }
    accuracy = float((predicted == y).mean()) if y.size else 0.0
    return {
        "accuracy": round(accuracy, 3),
        "per_class": per_class,
        "confusion": confusion.tolist(),
        "classes": CLASSES,
    }


def _split(x: np.ndarray, y: np.ndarray, seed: int = 7) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    train_idx: list[int] = []
    test_idx: list[int] = []
    for label in range(len(CLASSES)):
        indices = np.where(y == label)[0]
        rng.shuffle(indices)
        if indices.size == 0:
            continue
        if indices.size == 1:
            train_idx.extend(indices.tolist())
            continue
        cut = max(1, int(round(indices.size * 0.75)))
        train_idx.extend(indices[:cut].tolist())
        test_idx.extend(indices[cut:].tolist())
    return (
        x[train_idx],
        y[train_idx],
        x[test_idx],
        y[test_idx],
    )


# ---------------------------------------------------------------------------
# Public training API
# ---------------------------------------------------------------------------
def train(
    *,
    include_archive: bool = True,
    seed: int = 42,
    augment_target: int = 160,
) -> dict:
    """Build the training set, fit the model and persist it with metrics."""
    xs, ys, counts = _demo_samples()
    axs, ays, acounts = _archive_samples(include_archive)
    xs.extend(axs)
    ys.extend(ays)
    counts.update(acounts)

    if not xs:
        raise RuntimeError("No hay muestras de entrenamiento disponibles.")

    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.int64)
    x_train, y_train, x_test, y_test = _split(x, y, seed=seed)

    x_train, y_train, aug_counts = _augment(
        list(x_train), list(y_train), target=augment_target, seed=seed
    )
    counts.update(aug_counts)
    x_train = np.asarray(x_train, dtype=np.float64)
    y_train = np.asarray(y_train, dtype=np.int64)

    mean = x_train.mean(axis=0)
    std = x_train.std(axis=0)
    std[std < 1e-9] = 1.0
    x_train_norm = (x_train - mean) / std

    # Guarantee that every class is represented in the test set, using the
    # synthetic fallback only when the real data has no example of that class.
    rng = np.random.default_rng(seed + 1)
    present = set(y_test.tolist())
    test_extra = []
    test_extra_y = []
    for label_index, label in enumerate(CLASSES):
        if label_index in present:
            continue
        for _ in range(24):
            test_extra.append(_synthetic_sample(label, rng))
            test_extra_y.append(label_index)
    if test_extra:
        x_test = np.vstack([x_test, np.asarray(test_extra)]) if x_test.size else np.asarray(test_extra)
        y_test = np.concatenate([y_test, np.asarray(test_extra_y, dtype=np.int64)])
        counts["synthetic_test"] = len(test_extra)

    x_test_norm = (x_test - mean) / std
    weights, bias = _fit_softmax(x_train_norm, y_train)
    metrics = _metrics(x_test_norm, y_test, weights, bias)
    metrics.update(
        {
            "version": "1.0",
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_train": int(y_train.size),
            "n_test": int(y_test.size),
            "sources": counts,
            "features": FEATURES,
            "feature_labels": FEATURE_LABELS,
            "classes_es": CLASS_LABELS,
            "include_archive": bool(include_archive),
            "note": (
                "Regresión logística multiclase sobre características del motor "
                "de detección. Etiquetas demo conocidas, etiquetas débiles de "
                "Gaia/WISE en campos reales y aumento de datos para equilibrar. "
                "Es una ayuda al cribado, no una confirmación física."
            ),
        }
    )

    os.makedirs(MODEL_DIR, exist_ok=True)
    np.savez_compressed(
        MODEL_NPZ,
        mean=mean,
        std=std,
        weights=weights,
        bias=bias,
        classes=np.asarray(CLASSES),
        features=np.asarray(FEATURES),
    )
    with open(MODEL_JSON, "w", encoding="utf-8") as handle:
        json.dump(metrics, handle, ensure_ascii=False, indent=1)

    global _MODEL_CACHE
    _MODEL_CACHE = None
    return metrics


def _load_model() -> dict | None:
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    if not (os.path.isfile(MODEL_NPZ) and os.path.isfile(MODEL_JSON)):
        return None
    try:
        with np.load(MODEL_NPZ, allow_pickle=False) as data:
            model = {
                "mean": data["mean"],
                "std": data["std"],
                "weights": data["weights"],
                "bias": data["bias"],
            }
        with open(MODEL_JSON, "r", encoding="utf-8") as handle:
            model["meta"] = json.load(handle)
        _MODEL_CACHE = model
        return model
    except Exception:
        return None


def ensure_model() -> dict | None:
    """Load the persisted model, training a quick demo-only bootstrap if absent."""
    model = _load_model()
    if model is not None:
        return model
    try:
        train(include_archive=False)
    except Exception:
        return None
    return _load_model()


def predict(features: np.ndarray) -> dict:
    model = ensure_model()
    if model is None:
        return {
            "available": False,
            "label": None,
            "label_es": "Sin modelo",
            "confidence": None,
            "probabilities": {},
        }
    normalized = (np.asarray(features, dtype=np.float64) - model["mean"]) / model["std"]
    probs = _softmax(normalized.reshape(1, -1) @ model["weights"] + model["bias"])[0]
    best = int(np.argmax(probs))
    return {
        "available": True,
        "label": CLASSES[best],
        "label_es": CLASS_LABELS[CLASSES[best]],
        "confidence": round(float(probs[best]), 3),
        "probabilities": {
            label: round(float(probs[index]), 3) for index, label in enumerate(CLASSES)
        },
        "version": (model.get("meta") or {}).get("version"),
    }


def classify_candidate(candidate: dict, context: dict | None = None) -> dict:
    return predict(features_from_candidate(candidate, context))


def enrich_candidates(
    candidates: list[dict],
    context: dict | None = None,
    *,
    annotate_catalog: bool = False,
    dataset: dict | None = None,
) -> dict:
    """Add ``ml`` (and optionally ``catalog``) to every candidate in place."""
    summary = {"model": ensure_model() is not None, "n": len(candidates)}
    if annotate_catalog and dataset is not None and candidates:
        try:
            from . import crossmatch

            summary["catalog"] = crossmatch.annotate_candidates(candidates, dataset)
        except Exception as exc:  # noqa: BLE001 - catalog is optional
            summary["catalog"] = {"available": False, "error": str(exc)}
    for candidate in candidates:
        candidate["ml"] = classify_candidate(candidate, context)
    return summary


def status() -> dict:
    model = _load_model()
    if model is None:
        return {
            "available": False,
            "classes": CLASSES,
            "classes_es": CLASS_LABELS,
            "features": FEATURES,
            "note": "El clasificador se entrena en la primera consulta o con python -m spherex.model --train.",
        }
    meta = model.get("meta") or {}
    return {
        "available": True,
        "version": meta.get("version"),
        "trained_at": meta.get("trained_at"),
        "accuracy": meta.get("accuracy"),
        "n_train": meta.get("n_train"),
        "n_test": meta.get("n_test"),
        "per_class": meta.get("per_class", {}),
        "sources": meta.get("sources", {}),
        "classes": CLASSES,
        "classes_es": CLASS_LABELS,
        "features": FEATURES,
        "feature_labels": FEATURE_LABELS,
        "include_archive": meta.get("include_archive"),
        "note": meta.get("note"),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Train the SPHEREx candidate classifier.")
    parser.add_argument("--train", action="store_true", help="fit and persist the model")
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="skip real archive fields (faster, no network)",
    )
    parser.add_argument("--status", action="store_true", help="show model status as JSON")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.train:
        metrics = train(include_archive=not args.no_archive)
        print(json.dumps(metrics, ensure_ascii=False, indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
