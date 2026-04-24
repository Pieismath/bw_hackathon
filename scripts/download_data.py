"""Download every dataset under the Bridgewater Hugging Face organization.

Each teammate runs this once. Files land in ./data/<dataset-name>/ (gitignored).
Override the org slug with BW_ORG if the actual org name differs.
Private datasets require HF_TOKEN to be set (get one at huggingface.co/settings/tokens).

Usage:
  python scripts/download_data.py
  BW_ORG=bridgewater-associates python scripts/download_data.py
"""
import io
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi, snapshot_download

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

ORG = os.environ.get("BW_ORG", "BridgewaterAIHackathon")
TOKEN = os.environ.get("HF_TOKEN")

# Extra public datasets always pulled in addition to the BW org.
# Referenced by data/BW-AI-Hackathon/Structured_Data/Kalshi Trades pointer.
EXTRA_REPOS = [
    "TrevorJS/kalshi-trades",
]

# Ken French Data Library — factor returns used across RA replications.
# Each entry is a zipped CSV on Dartmouth's FTP; we unpack into data/ken-french/.
KEN_FRENCH_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
KEN_FRENCH_ZIPS = [
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
    "F-F_Momentum_Factor_daily_CSV.zip",
    "F-F_Research_Data_Factors_CSV.zip",
    "ME_Breakpoints_CSV.zip",
    "BE-ME_Breakpoints_CSV.zip",
]


def download_ken_french(target: Path) -> list[tuple[str, str]]:
    target.mkdir(parents=True, exist_ok=True)
    failed: list[tuple[str, str]] = []
    for zip_name in KEN_FRENCH_ZIPS:
        url = f"{KEN_FRENCH_BASE}/{zip_name}"
        print(f"\n=== {url} → {target} ===")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read()
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                zf.extractall(target)
        except Exception as e:
            print(f"  FAILED: {e}")
            failed.append((zip_name, str(e)))
    return failed


def main() -> None:
    api = HfApi(token=TOKEN)

    try:
        datasets = list(api.list_datasets(author=ORG))
    except Exception as e:
        print(f"[error] could not list datasets for org '{ORG}': {e}")
        sys.exit(1)

    if not datasets:
        print(f"[error] no datasets found under org '{ORG}'.")
        print("        set BW_ORG to the correct org slug (check huggingface.co/<slug>)")
        sys.exit(1)

    repo_ids = [d.id for d in datasets] + EXTRA_REPOS
    print(f"Found {len(datasets)} datasets under '{ORG}' + {len(EXTRA_REPOS)} extra:")
    for rid in repo_ids:
        print(f"  - {rid}")
    print()

    failed = []
    for rid in repo_ids:
        name = rid.split("/")[-1]
        target = DATA_DIR / name
        print(f"\n=== {rid} → {target} ===")
        try:
            snapshot_download(
                repo_id=rid,
                repo_type="dataset",
                local_dir=str(target),
                token=TOKEN,
            )
        except Exception as e:
            print(f"  FAILED: {e}")
            failed.append((rid, str(e)))

    print("\n=== Ken French Data Library ===")
    kf_failed = download_ken_french(DATA_DIR / "ken-french")
    failed.extend(kf_failed)

    print(f"\nAll data in: {DATA_DIR}")
    if failed:
        print(f"\n{len(failed)} downloads failed:")
        for repo_id, err in failed:
            print(f"  - {repo_id}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
