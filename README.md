# basis-bot

Delta-neutral funding-rate basis bot: long spot, short perpetual, earn
funding. The design spec and phase plan live in
[docs/spec.md](docs/spec.md) and are the source of truth.

Current state: **phase 0 (skeleton)**. The core process connects to
Postgres, records its config version and start/stop events, and idles.
It does not talk to any venue yet.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync                      # create .venv from the locked dependencies
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run basis-bot --config config/config.example.yaml check-config
```

## Running the stack

Secrets live outside the repo. Create a directory for them, then point
Compose at it:

```sh
mkdir -p ~/basis-bot-secrets && chmod 700 ~/basis-bot-secrets
openssl rand -base64 32 > ~/basis-bot-secrets/postgres_password
chmod 600 ~/basis-bot-secrets/postgres_password
# Compose file secrets are bind mounts that keep host ownership; the bot
# runs as uid 10001 and must be able to read the file.
sudo chown 10001:10001 ~/basis-bot-secrets/postgres_password
cp deploy/.env.example deploy/.env   # then edit the paths in it
```

```sh
docker compose -f deploy/docker-compose.yml up --build
docker compose -f deploy/docker-compose.yml down
```

`migrate` runs `alembic upgrade head` and exits; `core` starts only after
it succeeds and refuses to run against any other schema revision.

Exit codes: 0 clean stop, 1 runtime error, 2 invalid config.
