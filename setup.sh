#!/usr/bin/env bash
# Paper Replication Machine — environment setup
# Idempotent: safe to run multiple times.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ok()   { printf "\033[32m[OK]\033[0m   %s\n" "$1"; }
fail() { printf "\033[31m[FAIL]\033[0m %s\n" "$1"; }
warn() { printf "\033[33m[WARN]\033[0m %s\n" "$1"; }
info() { printf "\033[36m[..]\033[0m   %s\n" "$1"; }

# --------------------------------------------------------------------
# 1. Verify .env
# --------------------------------------------------------------------
info "Checking .env file..."
if [ ! -f .env ]; then
  fail ".env not found. Copy .env.example to .env and fill in your keys."
  exit 1
fi

# shellcheck disable=SC1091
set -a; . ./.env; set +a

missing=0
for key in ANTHROPIC_API_KEY FRED_API_KEY HF_TOKEN; do
  val="${!key}"
  if [ -z "$val" ]; then
    fail "$key is empty in .env"
    missing=1
  fi
done
if [ "$missing" -eq 1 ]; then
  fail "Fill in all three keys in .env and rerun."
  exit 1
fi
ok ".env present and all three keys non-empty"

# --------------------------------------------------------------------
# 2. Install uv if missing
# --------------------------------------------------------------------
info "Checking for uv..."
if ! command -v uv >/dev/null 2>&1; then
  info "uv not found; installing..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # shellcheck disable=SC1090
  if [ -f "$HOME/.local/bin/env" ]; then . "$HOME/.local/bin/env"; fi
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  fail "uv install failed; check https://docs.astral.sh/uv/"
  exit 1
fi
ok "uv available ($(uv --version))"

# --------------------------------------------------------------------
# 3. Create .venv with Python 3.11+
# --------------------------------------------------------------------
info "Creating virtualenv (.venv) with Python 3.11+..."
if [ ! -d .venv ]; then
  uv venv --python 3.11 .venv
  ok ".venv created"
else
  ok ".venv already exists"
fi

# shellcheck disable=SC1091
. .venv/bin/activate
PY_VER=$(python --version 2>&1)
ok "Using $PY_VER"

# --------------------------------------------------------------------
# 4. Install packages
# --------------------------------------------------------------------
info "Installing Python packages..."
uv pip install --quiet \
  anthropic \
  datasets \
  pandas \
  pyarrow \
  python-dotenv \
  huggingface_hub \
  fredapi \
  requests
ok "Packages installed"

# --------------------------------------------------------------------
# 5. Test Anthropic API key
# --------------------------------------------------------------------
info "Testing Anthropic API key..."
set +e
python - <<'PY'
import os, sys
from anthropic import Anthropic
try:
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{"role": "user", "content": "ping"}],
    )
    txt = "".join(getattr(b, "text", "") for b in msg.content)
    print(f"  response: {txt.strip()[:60]}")
    sys.exit(0)
except Exception as e:
    print(f"  error: {e}", file=sys.stderr)
    sys.exit(1)
PY
rc=$?
set -e
if [ $rc -ne 0 ]; then
  fail "Anthropic API key test failed"
  exit 1
fi
ok "Anthropic API key works"

# --------------------------------------------------------------------
# 6. Test FRED API key
# --------------------------------------------------------------------
info "Testing FRED API key (pulling 1 GDP observation)..."
set +e
python - <<'PY'
import os, sys
from fredapi import Fred
try:
    fred = Fred(api_key=os.environ["FRED_API_KEY"])
    s = fred.get_series("GDP", observation_start="2024-01-01")
    if s is None or len(s) == 0:
        raise RuntimeError("empty series")
    obs = s.iloc[-1]
    print(f"  GDP latest: {obs}")
    sys.exit(0)
except Exception as e:
    print(f"  error: {e}", file=sys.stderr)
    sys.exit(1)
PY
rc=$?
set -e
if [ $rc -ne 0 ]; then
  fail "FRED API key test failed"
  exit 1
fi
ok "FRED API key works"

# --------------------------------------------------------------------
# 7. Test HF token
# --------------------------------------------------------------------
info "Testing Hugging Face token (whoami)..."
set +e
python - <<'PY'
import os, sys
from huggingface_hub import HfApi
try:
    api = HfApi(token=os.environ["HF_TOKEN"])
    who = api.whoami()
    print(f"  logged in as: {who.get('name', who)}")
    sys.exit(0)
except Exception as e:
    print(f"  error: {e}", file=sys.stderr)
    sys.exit(1)
PY
rc=$?
set -e
if [ $rc -ne 0 ]; then
  fail "Hugging Face token test failed"
  exit 1
fi
ok "Hugging Face token works"

# --------------------------------------------------------------------
# 8. Download HF datasets (warn on failure, don't abort)
# --------------------------------------------------------------------
info "Downloading Hugging Face datasets (can take 10-20 min)..."
export HF_DATASETS_CACHE="$SCRIPT_DIR/data/cache/hf_datasets"
mkdir -p "$HF_DATASETS_CACHE"

DATASETS=(
  "defeatbeta/yahoo-finance-data"
  "aufklarer/central-bank-communications"
  "fancyzhx/ag_news"
  "istat-ai/ECB-FED-speeches"
)

for ds in "${DATASETS[@]}"; do
  info "  -> $ds"
  set +e
  python - "$ds" <<'PY'
import os, sys
from datasets import load_dataset
name = sys.argv[1]
try:
    load_dataset(
        name,
        cache_dir=os.environ["HF_DATASETS_CACHE"],
        token=os.environ["HF_TOKEN"],
    )
    sys.exit(0)
except Exception as e:
    print(f"    {e}", file=sys.stderr)
    sys.exit(1)
PY
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    warn "  failed to download $ds (continuing)"
  else
    ok "  $ds cached"
  fi
done

# --------------------------------------------------------------------
# Done
# --------------------------------------------------------------------
echo
ok "Setup complete."
echo
echo "Next steps:"
echo "  1. source .venv/bin/activate"
echo "  2. Drop paper PDF(s) into data/papers/"
echo "  3. Paste the build prompt into a fresh Claude Code session"
echo "     (mention that the environment is already set up)."
