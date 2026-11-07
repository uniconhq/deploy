# deploy

The Unicon stack as compose files, and the scripts that set it up. It builds
no image of its own.

`forge` holds the rules and the database, `backend` is a thin API over it,
and `frontend` is the web app. `runner` and `primitive-exec` make the images
a grading runs. This repo runs them with Forgejo, Woodpecker, Garage and
Postgres behind one nginx proxy (`proxy/`). `bootstrap/` makes the secrets,
writes `.env`, starts the services and seeds the forge; it is safe to rerun.
`images.json` pins the grading images by digest.

## Local development

You need Docker with Compose v2, [uv](https://docs.astral.sh/uv/), and the
other five repos checked out beside this one.

```sh
uv run scripts/build-images.py
uv run bootstrap
COMPOSE="docker compose -f compose.yaml -f compose.dev.yaml"
$COMPOSE --profile app up -d --build
$COMPOSE --profile agent up -d
```

`build-images.py` builds the grading images from the sibling repos into
`images.local.json`, which bootstrap prefers over `images.json`. The `agent`
profile is a grading machine on your laptop.

The app is at http://localhost:8080, Forgejo at http://forge.localhost:8080,
and dev mail at http://localhost:8025.

To wipe the stack and seed it with people, orgs and contests
([devseed/](devseed/__init__.py)):

```sh
uv run playwright install chromium   # once
uv run scripts/reset-dev.py          # --build also rebuilds backend and frontend
```

The passwords are in `.env` as `DEV_<NAME>_PASSWORD`.

Checks and tests:

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run yamllint .
UNICON_E2E_URL=http://localhost:8080 uv run pytest tests/e2e   # both profiles up
```

[CI](.github/workflows/ci.yaml) runs these, then boots and tests the whole
stack.

## Production

1. Set `UNICON_PUBLIC_URL`, `FORGEJO_PUBLIC_URL` and `WOODPECKER_PUBLIC_URL`
   in `.env` ([.env.example](.env.example) lists every key).
2. `uv run bootstrap -f compose.yaml` (without `-f` it adds the dev overlay).
3. `docker compose -f compose.yaml --profile app up -d`

To upgrade, with this repo at the new release:

```sh
COMPOSE="docker compose -f compose.yaml --profile app"
$COMPOSE pull backend-migrate backend frontend
$COMPOSE up -d backend-migrate backend frontend
uv run bootstrap -f compose.yaml
$COMPOSE exec -T backend unicon-forge reconcile
```

A grading machine is a separate Ubuntu 24.04 host. Copy
[machine/grading-machine.py](machine/grading-machine.py) and `slots.json` to
it, write its settings (see `Settings` in the script), and run as root:

```sh
python3 grading-machine.py setup /etc/unicon/machine.json
python3 grading-machine.py self-test /etc/unicon/machine.json
```

This sets up rootless Docker, a network wall and one Woodpecker agent per
slot. The agents start only if the self-test passes.

## Releases

deploy is released last, after its siblings. To cut `X.Y.Z`: on a
`chore/cut-X.Y.Z` branch, set `version` in `pyproject.toml`, run `uv lock`,
commit `chore: cut X.Y.Z`, merge, and tag `vX.Y.Z` on `main`.

## Do not break

- Back up `.env`. If a secret in it is lost while the volumes remain,
  bootstrap stops: restore the backup, or `down -v` and start empty.
- Change Forgejo only in `forgejo/app.ini` and `forgejo/custom/`. Web UI
  changes are lost on restart.
- Woodpecker runs on Postgres, never SQLite, which loses pipelines under load.
- Nothing reads Forgejo's database. Use its API.
