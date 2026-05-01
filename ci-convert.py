import os
import argparse
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import pillow_avif
from tqdm import tqdm

MIN_SIZE = 1 * 1024 * 1024  # 1MB


def convert_image(path, quality):
    try:
        if os.path.getsize(path) < MIN_SIZE:
            return "skip_small"

        output = os.path.splitext(path)[0] + ".avif"

        if os.path.exists(output):
            return "skip_exists"

        with Image.open(path) as img:
            img.save(
                output,
                "AVIF",
                quality=quality,
                speed=6
            )

        os.remove(path)

        return "converted"

    except Exception as e:
        return f"error {e}"


def collect_images(folder):
    exts = (".png", ".jpg", ".jpeg")
    files = []

    for root, _, filenames in os.walk(folder):
        for f in filenames:
            if f.lower().endswith(exts):
                files.append(os.path.join(root, f))

    return files


def main():
    parser = argparse.ArgumentParser(description="PNG/JPEG >1MB → AVIF converter")

    parser.add_argument("folder", help="target folder")
    parser.add_argument("--quality", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)

    args = parser.parse_args()

    images = collect_images(args.folder)

    stats = {
        "converted": 0,
        "skip_small": 0,
        "skip_exists": 0,
        "error": 0
    }

    with ThreadPoolExecutor(max_workers=args.workers) as executor:

        results = list(
            tqdm(
                executor.map(lambda x: convert_image(x, args.quality), images),
                total=len(images)
            )
        )

    for r in results:
        if r == "converted":
            stats["converted"] += 1
        elif r == "skip_small":
            stats["skip_small"] += 1
        elif r == "skip_exists":
            stats["skip_exists"] += 1
        elif "error" in r:
            stats["error"] += 1

    print("\nSummary")
    print(stats)


if __name__ == "__main__":
    main()