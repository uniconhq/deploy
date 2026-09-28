# deploy

The Unicon stack, as compose files and the scripts that prepare it. This repo
produces no image of its own. It holds the configuration for the four pieces
Unicon does not write (Forgejo, Woodpecker, Garage, Postgres), the files that
give Forgejo's own account pages the Unicon look, the reverse proxy that puts
the backend and the frontend on one origin, and a bootstrap tool that turns a
fresh stack into one the backend can talk to.

| Service | Image | Reached at |
|---|---|---|
| `postgres` | `postgres:16-alpine` | `postgres:5432` in the network, `127.0.0.1:55432` in development |
| `forgejo` | `codeberg.org/forgejo/forgejo:15.0.8` | `http://forge.localhost:8080` in a browser, through the proxy; `http://forgejo:3000` in the network; `127.0.0.1:3300` in development for `check-lfs.py` |
| `woodpecker-server` | `woodpeckerci/woodpecker-server:v3.18.1` | `http://localhost:8000` in development |
| `garage` | `dxflrs/garage:v2.4.1` | `garage:3900` in the network; S3 and admin on `127.0.0.1` in development |
| `proxy` | `nginx:1.27-alpine` | `http://localhost:8080`, the app, and `http://forge.localhost:8080`, the forge's people pages |
| `backend`, `frontend` | built from the siblings in development | behind the proxy |

No grading machine is part of the stack: a machine that runs contestant code
is a separate host, enrolled at the CI with its own token. The server carries
no shared agent secret, so a machine that presents no issued token is refused.
`compose.dev.yaml` can run one agent on the laptop under `--profile agent`,
with a token from the CI's Agents page in `WOODPECKER_DEV_AGENT_TOKEN`.

## Running it

You need Docker with Compose v2, and [uv](https://docs.astral.sh/uv/). From this
directory:

```sh
uv run bootstrap
```

That is the whole thing. It generates every secret, writes `.env`, and starts
the services in the order they need each other: Garage first, because Forgejo
needs an S3 key before it opens its LFS storage; then Forgejo, because
Woodpecker will not start without an OAuth client that only a Forgejo
administrator can create; then the proxy, because the Woodpecker sign-in goes
through Forgejo's pages on it; then Woodpecker.

Run it again whenever you like. Every step checks before it creates, so a second
run reports what was already there and creates nothing new. It is not a no-op:
it re-applies the three database role passwords and both Forgejo bot passwords
from `.env`, re-applies the bucket grants, rewrites `.env`, and recreates the
`backend` container if any `UNICON_*` value changed under it, because a running
container never re-reads its environment.

**If `.env` is gone, or a secret in it is, do not just run bootstrap.** It
refuses to start when a secret that data on disk depends on is missing while
the volume holding that data is still on the machine, and names both. Fresh
secrets are not a fresh start: Forgejo's `SECRET_KEY` decrypts what is
already in its database, including the OAuth client secrets, the backend's
token key decrypts the credentials in Postgres, and Garage's RPC secret is
what its node was formed with; a new value cannot read any of it. The check is
per key, so a `.env` that lost one line, or is empty, is refused the same as
one that is gone. The two ways forward are the two the message names: put a
backed-up `.env` back, or throw the volumes away with `down -v` and bootstrap
an empty stack. With no volumes present and no `.env`, bootstrap builds
everything from nothing, which is the ordinary first run. `.env` is written
whole or not at all, so an interrupted run never leaves a truncated one.

Afterwards:

```sh
docker compose -f compose.yaml -f compose.dev.yaml ps
docker compose -f compose.yaml -f compose.dev.yaml down        # keep the data
docker compose -f compose.yaml -f compose.dev.yaml down -v     # throw it away
```

Without `-f compose.dev.yaml` you get the production shape: images by tag,
nothing published to the host except the proxy on 8080, Forgejo with
registration closed, and the landing page not offering Create account.
Forgejo has no port of its own: people reach its sign-in, sign-up, OAuth and
account pages through the proxy on `FORGEJO_PUBLIC_URL`, its own hostname,
and the proxy answers 404 for everything else of it, the web UI, the API,
token minting and git over HTTP included. Bootstrap works in that shape too,
because it drives Garage and Forgejo's API through the containers' own
command lines rather than a published port. The one thing it does need is
that `WOODPECKER_PUBLIC_URL` resolves from the machine running it: Woodpecker
has no way to mint an API token except through its web UI. In development the
dev override publishes Woodpecker on loopback for exactly that; a real
deployment has it behind its own ingress under a real name.

Every port the dev override opens is bound to `127.0.0.1`, so a laptop on a
shared network does not put its database and object store on that network.
Postgres is published on **55432**, not 5432: a developer machine often already
runs a PostgreSQL of its own, and two listeners on 5432 do not fail, they make
`psql -h localhost` a coin toss.

## Running the stack in development

The whole sequence, from nothing to a signed-in browser:

```sh
uv run bootstrap
COMPOSE="docker compose -f compose.yaml -f compose.dev.yaml --profile app"
$COMPOSE up -d --build backend-migrate backend
$COMPOSE up -d --build frontend
```

`backend-migrate` runs `unicon migrate` and exits; the backend image does not
migrate on its own, and `backend` waits for it to finish successfully. Both use
the same build and the same image tag, so the build happens once. The build
takes the `backend` checkout as its context, which has to sit beside this
repo.

The frontend is a separate line on purpose. Everything else runs without it, and
the proxy starts whether or not the app services are there, so you can work on
one repo with the rest of the stack up. Start it whenever it builds.

The Forgejo URL is compiled into the frontend bundle, for the account page
links, so a production image has to be built with `--build-arg` setting
`VITE_FORGE_URL` to the real Forgejo URL. The dev override passes
`FORGEJO_PUBLIC_URL` for it.

Then:

```sh
curl http://localhost:8080/api/v1/time      # {"now": "..."}
curl -i http://localhost:8080/readyz        # 200 once Postgres answers
curl -i http://localhost:8080/-/proxy       # 204, nginx itself
curl http://localhost:8080/openapi.json     # what the frontend generates from
```

One origin holds all of it: the frontend on `/`, the API on `/api/`, the health
endpoints, `/-/proxy` for the proxy's own healthcheck, and `/openapi.json`,
which the frontend reads with
`pnpm gen:api --from http://localhost:8080/openapi.json`. The access log drops
the query string of `/api/v1/auth/callback`, so the login `code` and `state`
never reach disk.

The app is at `http://localhost:8080`, Forgejo at `http://forge.localhost:8080`,
on the same proxy under its own hostname, so the two never share cookies. Any
name under `.localhost` is loopback for browsers and most resolvers, so no
hosts entry is needed; the Woodpecker container reaches the same name through
the Docker host gateway for its OAuth token request. Sign in through the app;
Forgejo is where the account lives. In development Forgejo accepts new
registrations with no mail server, so you can make one. The dev override also
publishes Forgejo on `127.0.0.1:3300` for `scripts/check-lfs.py`, which
drives its API from this machine; nothing else uses that port.

## What bootstrap made

Two Forgejo accounts. `unicon-backend` is a site administrator and holds the
provisioning token: the backend uses it to create organisations, repositories,
teams and protected tags, and never to act for a person. `unicon-ci` is an
ordinary account that Woodpecker signs in as. Two accounts rather than one,
because Woodpecker insists on a real forge login for its own user and the
site-administrator token must not sit in Woodpecker's database.

Two Forgejo OAuth applications, one for Unicon and one for Woodpecker. Bootstrap
talks to Forgejo's API with the curl inside the Forgejo container, configured
over standard input so no password or token is ever a command-line argument,
and to Garage through `garage json-api` inside its container, which speaks the
same admin API over Garage's internal RPC. Neither needs a port open for it. Four Garage buckets: `forgejo-lfs` for Forgejo, and `unicon-uploads` for
what a browser uploads before a submit, `unicon-results` for grading logs and
`unicon-exports`, reserved, for the backend, with a separate access key for
each side. It creates no other bucket and removes none. A Woodpecker API token
for `unicon-ci`. No CI agent: a grading machine enrols itself with a token from
the CI's Agents page.

All of it lands in `.env`, which is git-ignored. `.env.example` lists every key
with a comment and is the template bootstrap fills, so a key added there appears
in the next generated `.env`.

## The forge's account pages

Forgejo serves its own sign-in, sign-up, OAuth consent and account settings
pages, and people see them whenever the app sends them to the forge. What makes
those pages look like Unicon is under `forgejo/custom/`, which compose mounts
read-only into Forgejo's custom path, `/data/gitea`:

- `public/assets/img/` holds the Unicon mark as `logo.svg` and `favicon.svg`,
  the PNG sizes Forgejo wants beside them, and `avatar_default.png`. These are
  the file names Forgejo looks up itself, so they stand in for its own.
- `public/assets/css/unicon.css` is one stylesheet. It hides Forgejo's
  navigation bar and footer and sets Forgejo's colour, radius and font
  variables to the frontend theme's values, so the forms take the app's look.
  The font families are named with fallbacks only, and the page fetches
  nothing from outside.
- `templates/custom/header.tmpl` is the one template, and it is Forgejo's
  extension point rather than one of its pages: Forgejo includes it at the end
  of `<head>`, and it carries the link to the stylesheet.

Forgejo's own templates are not edited. The files live in this repo rather
than in the Forgejo volume, so an upgrade of the image keeps them, and a change
to them is a commit here and a restart of the `forgejo` service. The mark is
the one the frontend draws in `src/ui/brand/`; a change to it is made in both
places.

## Checking the large-file path

```sh
uv run scripts/check-lfs.py
```

This one does use the Garage admin API on `127.0.0.1:3903`, to read the bucket
size before and after, and Forgejo's API on `127.0.0.1:3300`, so it needs
`-f compose.dev.yaml` or an equivalent publish. It is a check for a person at
a keyboard, not part of a deployment.

Commits a 500 MB random file through Forgejo's contents API into a repository
whose `.gitattributes` sends `*.bin` to LFS, reads it back through `/media`,
compares the sha256, and confirms the bytes arrived in the Garage bucket rather
than on the Forgejo container disk. If a size fails it halves and tries again,
so the output names a ceiling that works.

## Checks

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run yamllint .

# compose reads .env, and every secret in it is required, so fill the template
# with a placeholder to validate the files without one. Into a file of its own,
# not over .env: that file is the running stack's only copy of its secrets.
sed -E 's/^([A-Za-z_][A-Za-z0-9_]*)=$/\1=placeholder/' .env.example > .env.check
docker compose --env-file .env.check -f compose.yaml config --quiet
docker compose --env-file .env.check -f compose.yaml -f compose.dev.yaml config --quiet
```

CI runs exactly these, and then boots the whole stack: it checks out `backend`
and `frontend` beside this repo, runs bootstrap against `compose.yaml` and
`compose.dev.yaml`, starts the `app` profile, and checks that `/readyz`
answers ready and the frontend serves its page through the proxy on one
origin. It then tries the proxy from outside: the listed paths of both hosts
answer, an unlisted path on either is 404, Forgejo's API, web UI and git
endpoints are 404 on the forge host, every answer carries the security
headers, and no container publishes Forgejo on every interface. It then runs
bootstrap a second time and fails if `.env` changed.
Every push and pull request runs it, so a change to compose, a config file or
bootstrap is caught the day it breaks. Later end-to-end tests run on top of
this job rather than starting a stack of their own.

## Three rules that are cheap now and expensive later

**Woodpecker runs on Postgres, never SQLite.** SQLite is the default. Under
concurrent dispatch it answers HTTP 500 with an empty body and loses pipelines,
between eight and fifty-one per cent of them depending on concurrency. Serial
development traffic never shows it. At a contest deadline it would silently drop
a share of every submission sent for grading.

**`forgejo/app.ini` is the record.** Compose copies it into the container on
every start, so anything changed through Forgejo's web UI is reverted by the
next deploy. Change Forgejo here, in a commit, or the change does not exist.
The same holds for `forgejo/custom/`, the look of its account pages.

**Nothing reads Forgejo's database.** Unicon has its own database on the same
server and talks to Forgejo over its API. Forgejo keeps half its state on disk
and caches permissions in memory, so a direct read is wrong as often as it is
fast.
