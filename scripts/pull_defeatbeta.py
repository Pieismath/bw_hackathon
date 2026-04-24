"""
Download key parquet files from defeatbeta/yahoo-finance-data.
The dataset is a collection of independent parquet files, not a standard
HF dataset with splits, so we pull each file directly.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from huggingface_hub import hf_hub_download, login

load_dotenv()
login(token=os.getenv("HF_TOKEN"), add_to_git_credential=False)

REPO_ID = "defeatbeta/yahoo-finance-data"
CACHE_DIR = Path("data/cache/hf_datasets/defeatbeta")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FILES = [
    "data/stock_prices.parquet",
    "data/stock_statement.parquet",
    "data/stock_profile.parquet",
    "data/stock_shares_outstanding.parquet",
    "data/stock_earning_calendar.parquet",
    "data/stock_earning_call_transcripts.parquet",
    "data/stock_tailing_eps.parquet",
    "data/stock_dividend_events.parquet",
    "data/stock_split_events.parquet",
    "data/daily_treasury_yield.parquet",
]

for file_path in FILES:
    print(f"\n-> {file_path}")
    try:
        local_path = hf_hub_download(
            repo_id=REPO_ID,
            filename=file_path,
            repo_type="dataset",
            cache_dir=str(CACHE_DIR),
        )
        size_mb = Path(local_path).stat().st_size / (1024 * 1024)
        print(f"   OK ({size_mb:.1f} MB) -> {local_path}")
    except Exception as e:
        print(f"   FAILED: {e}")

print(f"\nDone. Files in: {CACHE_DIR.absolute()}")