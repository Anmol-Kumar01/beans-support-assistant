#!/usr/bin/env bash
# One-command database setup: PostgreSQL 17 + pgvector in Docker, then the schema.
#
#   ./scripts/setup_db.sh            start (or reuse) the database and apply migrations
#   ./scripts/setup_db.sh --status   show the container and migration status
#   ./scripts/setup_db.sh --stop     stop the database (data is kept)
#   ./scripts/setup_db.sh --reset    DELETE the database and its data, then set it up again
#
# Safe to run again at any time: an existing container and volume are reused, and only
# migrations not yet applied are run. Settings come from .env (see .env.example).

set -euo pipefail
cd "$(dirname "$0")/.."

# --- settings: read only the database lines from .env (other values may contain spaces) ---
if [[ -f .env ]]; then
  while IFS= read -r line; do
    export "${line?}"
  done < <(grep -E '^(POSTGRES_[A-Z_]+|DB_[A-Z_]+|DATABASE_URL)=' .env || true)
fi
POSTGRES_USER="${POSTGRES_USER:-beans}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-beans_local}"
POSTGRES_DB="${POSTGRES_DB:-beans_bot}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
DB_CONTAINER="${DB_CONTAINER:-beans-bot-db}"
DB_VOLUME="${DB_VOLUME:-beans-bot-pgdata}"
DB_IMAGE="${DB_IMAGE:-pgvector/pgvector:pg17}"
export DATABASE_URL="${DATABASE_URL:-postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${POSTGRES_PORT}/${POSTGRES_DB}}"
PYTHON="${PYTHON:-.venv/bin/python}"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- preflight ---
command -v docker >/dev/null || fail "Docker is not installed. Install it: https://docs.docker.com/engine/install/"
if ! docker info >/dev/null 2>&1; then
  fail "Docker is installed but not running (or you lack permission).
       Start it:            sudo systemctl start docker
       Start on boot:       sudo systemctl enable docker
       Use without sudo:    sudo usermod -aG docker \$USER   (then log out and back in)"
fi
[[ -x "$PYTHON" ]] || fail "Python env not found at $PYTHON. Run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"

exists()  { docker container inspect "$DB_CONTAINER" >/dev/null 2>&1; }
running() { [[ "$(docker container inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null)" == "true" ]]; }

case "${1:-}" in
  --status)
    if exists; then docker ps -a --filter "name=^${DB_CONTAINER}$" --format 'container: {{.Names}}  {{.Status}}  {{.Ports}}'; else echo "container: $DB_CONTAINER not created"; fi
    running && "$PYTHON" -m app.db.migrate --status
    exit 0 ;;
  --stop)
    exists && docker stop "$DB_CONTAINER" >/dev/null && say "Stopped $DB_CONTAINER (data kept in volume $DB_VOLUME)." || say "Nothing to stop."
    exit 0 ;;
  --reset)
    read -r -p "This deletes ALL data in $DB_CONTAINER (volume $DB_VOLUME). Type 'reset' to continue: " answer
    [[ "$answer" == "reset" ]] || fail "Cancelled."
    docker rm -f "$DB_CONTAINER" >/dev/null 2>&1 || true
    docker volume rm "$DB_VOLUME" >/dev/null 2>&1 || true
    say "Deleted. Setting up a fresh database..." ;;
  "") ;;
  *) fail "Unknown option: $1 (use --status, --stop or --reset)" ;;
esac

# --- container ---
if exists; then
  if running; then
    say "Database container $DB_CONTAINER is already running."
  else
    say "Starting existing container $DB_CONTAINER..."
    docker start "$DB_CONTAINER" >/dev/null
  fi
else
  if (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -qE "[:.]${POSTGRES_PORT}\b"; then
    fail "Port $POSTGRES_PORT is already in use. Set POSTGRES_PORT=5433 (and DATABASE_URL) in .env, then run again."
  fi
  say "Creating $DB_CONTAINER from $DB_IMAGE (first run downloads the image)..."
  docker run -d \
    --name "$DB_CONTAINER" \
    --restart unless-stopped \
    -e POSTGRES_USER="$POSTGRES_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    -e POSTGRES_DB="$POSTGRES_DB" \
    -p "127.0.0.1:${POSTGRES_PORT}:5432" \
    -v "${DB_VOLUME}:/var/lib/postgresql/data" \
    "$DB_IMAGE" >/dev/null
fi

# --- wait until PostgreSQL accepts connections ---
say "Waiting for PostgreSQL to be ready..."
for _ in $(seq 1 60); do
  if docker exec "$DB_CONTAINER" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 1
done
[[ "${ready:-}" == 1 ]] || fail "PostgreSQL did not become ready in 60s. Check: docker logs $DB_CONTAINER"

# --- schema ---
say "Applying migrations..."
"$PYTHON" -m app.db.migrate

say "Done. Database: postgresql://${POSTGRES_USER}:***@localhost:${POSTGRES_PORT}/${POSTGRES_DB}"
echo "    Open a SQL shell:  docker exec -it $DB_CONTAINER psql -U $POSTGRES_USER -d $POSTGRES_DB"
