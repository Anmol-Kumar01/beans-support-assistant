#!/usr/bin/env bash
# One-command setup: everything in the README Quick start, then starts the app.
#
#   ./scripts/setup.sh                 asks you to paste your .env if there isn't one yet
#   ./scripts/setup.sh path/to/env     uses that file as .env
#   ./scripts/setup.sh --no-start      set everything up but don't start the server
#
# Safe to run again at any time: finished steps are reused, and only new or changed
# documents are re-embedded.

set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

START=1
ENV_FILE=""
for arg in "$@"; do
  case "$arg" in
    --no-start) START=0 ;;
    -*) fail "Unknown option: $arg (use --no-start)" ;;
    *) ENV_FILE="$arg" ;;
  esac
done

# --- 1. prerequisites ---
say "Checking prerequisites..."
command -v python3 >/dev/null || fail "Python 3.12+ is not installed: sudo apt install python3 python3-venv"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
  || fail "Python 3.12+ is needed (found $(python3 --version)). Install a newer python3."
python3 -c 'import venv, ensurepip' 2>/dev/null || fail "Python venv is missing: sudo apt install python3-venv"

# Node 20+: use nvm when it's installed (the frontend pins Node 22 in frontend/.nvmrc).
if [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
  set +u  # nvm isn't written for set -u
  # shellcheck disable=SC1091
  source "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
  nvm install "$(cat frontend/.nvmrc)" >/dev/null  # installs it if missing, then uses it
  set -u
fi
command -v node >/dev/null || fail "Node.js 20+ is not installed. Install nvm (https://github.com/nvm-sh/nvm), then: nvm install 22"
[[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ]] \
  || fail "Node.js 20+ is needed (found $(node --version)). Run: nvm install 22 && nvm use 22"

command -v docker >/dev/null || fail "Docker is not installed: sudo apt install docker.io && sudo usermod -aG docker \$USER (then log out and back in)"
if ! docker info >/dev/null 2>&1 && command -v systemctl >/dev/null; then
  say "Starting Docker (may ask for your password)..."
  sudo systemctl start docker || true
fi
docker info >/dev/null 2>&1 || fail "Docker isn't running, or you lack permission.
       Start it:          sudo systemctl start docker
       Use without sudo:  sudo usermod -aG docker \$USER   (then log out and back in)"

# --- 2. API keys (.env) ---
if [[ -n "$ENV_FILE" ]]; then
  [[ -f "$ENV_FILE" ]] || fail "No file at $ENV_FILE"
  cp "$ENV_FILE" .env
  say "Copied $ENV_FILE to .env"
elif [[ ! -f .env ]]; then
  say "No .env yet. Paste its contents below, then press Enter and Ctrl-D."
  echo "    (Press Ctrl-D right away to start from .env.example and fill in the keys yourself.)"
  cat > .env
  if ! grep -q '_API_KEY=.' .env; then
    cp .env.example .env
    fail "Created .env from .env.example. Replace the your-…-key placeholders (see README step 4), then run this script again."
  fi
fi
chmod 600 .env

# --- 3. Python backend ---
say "Installing the Python backend..."
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e '.[dev]'

# --- 4. React frontend ---
say "Building the frontend..."
(cd frontend && npm install --no-audit --no-fund --loglevel=error && npm run build --silent)

# --- 5. check keys and models ---
say "Checking API keys and models..."
.venv/bin/python -m app.core.startup_check \
  || fail "A key or model check failed (see the FAIL line above). Fix .env, then run this script again."

# --- 6. database ---
say "Setting up the database..."
./scripts/setup_db.sh

# --- 7. knowledge base ---
say "Loading the knowledge base (about 6 minutes the first time)..."
.venv/bin/python -m app.ingest \
  || fail "Ingestion stopped (often a free-tier quota). Wait a few minutes and run this script again: it continues where it stopped."

# --- 8. run ---
say "Setup complete."
if [[ "$START" == 1 ]]; then
  say "Starting the app at http://localhost:8001 (Ctrl-C to stop)..."
  exec .venv/bin/uvicorn app.api.main:create_app --factory --port 8001
fi
echo "    Start the app:  .venv/bin/uvicorn app.api.main:create_app --factory --port 8001"
