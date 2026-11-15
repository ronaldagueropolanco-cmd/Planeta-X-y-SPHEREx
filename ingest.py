"""Pre-cache a real SPHEREx field so the viewer opens instantly.

Usage (from the project root):

    python ingest.py                 # deep field, two epochs ~6 months apart
    python ingest.py --ra 210.8 --dec 54.3 --fov 0.4
    python ingest.py --epochs 3 --detectors 6

The command downloads level-2 cutouts from NASA/IPAC IRSA, reprojects them to a
common north-up grid and stores the result under uploads/cache/datasets.
"""

from __future__ import annotations

import argparse
import sys
import time

from spherex import pipeline, store

DEEP_FIELD = (276.2639, 64.8240)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-cache a real SPHEREx field")
    parser.add_argument("--ra", type=float, default=DEEP_FIELD[0])
    parser.add_argument("--dec", type=float, default=DEEP_FIELD[1])
    parser.add_argument("--fov", type=float, default=0.35, help="field of view in degrees")
    parser.add_argument("--npix", type=int, default=192)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--detectors", type=int, default=4)
    parser.add_argument("--epoch-days", type=str, default=None,
                        help="comma separated integer MJD days")
    parser.add_argument("--label", type=str, default=None)
    parser.add_argument("--list", action="store_true", help="list cached datasets")
    args = parser.parse_args(argv)

    store.ensure_demo_datasets()

    if args.list:
        for item in store.list_datasets():
            print(f"{item['id']:<28} {item['kind']:<8} "
                  f"{item['n_epochs']} épocas · {item['n_frames']} frames · {item['label']}")
        return 0

    epoch_days = None
    if args.epoch_days:
        epoch_days = [int(float(part)) for part in args.epoch_days.split(",") if part]

    started = time.time()
    print(f"Consultando IRSA en RA={args.ra} Dec={args.dec} …")
    try:
        dataset = pipeline.build_archive_dataset(
            args.ra,
            args.dec,
            fov=args.fov,
            npix=args.npix,
            max_epochs=args.epochs,
            max_detectors=args.detectors,
            epoch_days=epoch_days,
            label=args.label,
            progress=lambda message: print(f"  {message}", flush=True),
        )
    except Exception as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1

    elapsed = time.time() - started
    print("\nDataset almacenado:")
    print(f"  id        {dataset['id']}")
    print(f"  épocas    {len(dataset['epochs'])}")
    print(f"  frames    {len(dataset['frames'])}")
    print(f"  tamaño    {dataset['fov']}° · {dataset['npix']} px")
    print(f"  tiempo    {elapsed:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
