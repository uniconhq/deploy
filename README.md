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
| `woodpecker-agent`, `socket-filter` | development only, under `--profile agent` | nothing; the agent dials out to the server |

No grading machine is part of the stack: a machine that runs contestant code
is a separate host, enrolled at the CI with its own token. The server carries
no shared agent secret, so a machine that presents no issued token is refused.
`compose.dev.yaml` can run one on the laptop under `--profile agent`: a CI
agent, the socket filter beside it and the shared store for large task files,
with the agent's token minted by bootstrap. It is the only place an agent and
the databases share a Docker daemon.

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
Opening sign-up there takes a mail server in `.env` (`MAIL_SMTP_ADDR` and the
rest): everyone who signs themselves up confirms their address by the link
in a mail, a contest's email pattern trusts only confirmed addresses, and
Forgejo drops the confirmation without a word when it cannot send mail, so
bootstrap refuses `UNICON_FORGE_REGISTRATION_OPEN=true` without one.
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

The whole sequence, from nothing to a signed-in browser and a machine that
grades:

```sh
uv run scripts/build-images.py
uv run bootstrap
COMPOSE="docker compose -f compose.yaml -f compose.dev.yaml --profile app"
$COMPOSE up -d --build backend-migrate backend
$COMPOSE up -d --build frontend
docker compose -f compose.yaml -f compose.dev.yaml --profile agent up -d
```

The first line builds every image a grading runs from the sibling checkouts
(`runner`, `primitive-compile`, `primitive-sandbox-run` and
`primitive-diff-check`, beside this repo), pushes them to a registry on
`localhost:5000` and writes `images.local.json`, which bootstrap then reads
instead of `images.json`; see [The image manifest](#the-image-manifest). The
last line starts the grading machine, whose agent bootstrap has already
enrolled. Without the first line bootstrap seeds no primitive, and nothing
can be graded.

After a change in one of those checkouts, run the first two lines again. A
primitive whose image or declaration changed gets its next version at the
forge, while `unicon/classic@v1` still names `v1` of each; `uv run bootstrap
--rewrite-v1` instead rewrites `v1` in place, so the built-in workflow grades
with what was just built.

`backend-migrate` runs `unicon-forge migrate`, the forge package's command,
from the backend image, and exits; the backend image does not
migrate on its own, and `backend` waits for it to finish successfully. Both use
the same build and the same image tag, so the build happens once. The build
takes the `backend` checkout as its context and the `forge` checkout as the
named context `forge`, and both have to sit beside this repo. While the
backend develops against the forge checkout by path, that is where its forge
package comes from; while it pins a forge release, the package comes from
the release and the checkout goes unused.

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

One origin holds all of it: the frontend on `/` and its pages, every organiser
page under `/orgs`, a contest's page and its tasks' pages under `/contests/`,
the API on `/api/`, the health endpoints, `/-/proxy` for the
proxy's own healthcheck, and `/openapi.json`, which the frontend reads with
`pnpm gen:api --from http://localhost:8080/openapi.json`. Two API paths are
refused: `/api/v1/events/`, where the forge pushes an org's events, and
`/api/v1/ci/`, the CI's configuration extension. Both are called inside the
stack, at `UNICON_INTERNAL_URL`, and nothing outside has a reason to call
them. A grading machine's two calls, a grading's envelope and its callback
under `/api/v1/gradings/`, go to the backend with the rest of `/api/`. The
access log drops the query string of `/api/v1/auth/callback`, so the login
`code` and `state` never reach disk.

Two object storage paths go to Garage, each for the writes it exists for:
`/unicon-uploads/` takes POST and PUT, a browser's upload before a submit,
and `/unicon-results/` takes PUT, a grading machine's run log. Each request
carries a presigned signature the backend made for the host it handed out,
`UNICON_PUBLIC_URL` for a browser and `UNICON_MACHINE_URL` for a machine, so
the proxy passes the Host header through as it came, and Garage, which reads
the bucket from the path, checks the signature that was made. Nothing else of
Garage is reachable, reads and listings included. An upload is on the app's
own origin, so it needs no CORS.

The app is at `http://localhost:8080`, Forgejo at `http://forge.localhost:8080`,
on the same proxy under its own hostname, so the two never share cookies. Any
name under `.localhost` is loopback for browsers and most resolvers, so no
hosts entry is needed; the Woodpecker container reaches the same name through
the Docker host gateway for its OAuth token request. Sign in through the app;
Forgejo is where the account lives. In development Forgejo takes new
sign-ups and sends its mail to Mailpit, which delivers nothing: the
confirmation link for an account you make is at http://localhost:8025. The dev override also
publishes Forgejo on `127.0.0.1:3300` for `scripts/check-lfs.py`, which
drives its API from this machine; nothing else uses that port.

## What bootstrap made

Two Forgejo accounts. `unicon-backend` is a site administrator and holds the
provisioning token: the backend uses it to create organisations, repositories,
teams and protected tags, and never to act for a person.
`UNICON_FORGE_PLATFORM_ACCOUNT` names it to the backend, which reserves the
protected tags for that one account. `unicon-ci` is an
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
for `unicon-ci`.

Three primitives and one workflow at the forge, in the platform org
`unicon`, which bootstrap makes, owned by `unicon-backend`. A task's first
save has to find a workflow and its primitives the organiser can read, or
nothing can be published.

Each primitive the image manifest pins is mirrored from its own repository
into the public repository `unicon/<name>.primitive` with the topic
`unicon-primitive`: `compile`, `sandbox-run` and `diff-check`. The mirror is
the primitive repository's files, less its CI configuration, in one commit
tagged `v1`. The forge's compiler reads `primitive.yaml` at the root of a
version. A primitive's own repository names no image in that file, so
bootstrap writes the `version` and the `image`, the manifest's digest, into
it under its `name`. What a version is, is that file: when the manifest's
digest or anything else in the declaration changes, the next run commits the
repository as it is then and tags `v2`, and `v1` still points at what it did,
so a published task keeps grading against what it was published with. A
change to the other files alone makes no version, since the program that runs
is the one in the image and a rebuilt image is a new digest. A run that finds
nothing changed makes nothing.

The workflow is the public repository `unicon/classic.workflow` with the
topic `unicon-workflow`, holding `workflow.yaml` and `README.md` from
`workflows/classic/`, tagged `v1`. That is the built-in workflow
`unicon/classic@v1`: compile the submission, run it on every testcase, diff
each output against the answer. A workflow's versions are made on purpose, so
a rerun leaves `v1` as it is even when the files here have changed, and says
so.

The one exception to both is a development stack. `uv run bootstrap
--rewrite-v1` rewrites `v1` of the workflow and of each primitive in place to
what is here now, with a new commit and the tag moved to it, rather than
leaving it or making `v2`, so a primitive rebuilt on the laptop is what the
tasks already there grade with. It refuses to run without `compose.dev.yaml`
among the compose files.

On a development stack, one CI agent: `laptop`, enrolled as a global agent
through Woodpecker's administrator API, its token written to
`WOODPECKER_DEV_AGENT_TOKEN` for the dev overlay to hand it. A rerun finds it
by name and keeps its token, the one `.env` holds if the CI has two by that
name. It then deletes every other agent that declares `pool=platform`, or is
called `laptop`, and has not been heard from for an hour, so a leftover of an
earlier test can never take a grading run; one that is still reporting is
left and named in the output. A production stack gets no agent from
bootstrap, and bootstrap deletes none there: a grading machine enrols itself.

All of it lands in `.env`, which is git-ignored, together with the images a
grading runs, from the image manifest: `UNICON_HARNESS_IMAGE` and
`UNICON_CLONE_IMAGE`, which the backend puts into what it hands the CI, and
`UNICON_FILTER_IMAGE` and `UNICON_FILTER_IMAGES`, the socket filter the dev
overlay runs and the images it lets containers be made from, which is every
image any version of a primitive at the forge names. `.env.example` lists every key
with a comment and is the template bootstrap fills, so a key added there appears
in the next generated `.env`. Three of its keys are settings rather than
secrets, with defaults bootstrap writes as they are: `UNICON_INTERNAL_URL`,
`http://backend:8000`, is where the forge reaches Unicon inside the stack,
which is where every org's event push and the CI's configuration extension
point, and why `forgejo/app.ini` allows the host `backend` for webhooks;
`UNICON_ORG_CREATION_OPEN`, `true`, lets any signed-in person create an org,
and a deployment open to strangers sets it to `false` and creates orgs with
`unicon create-org`; `UNICON_MACHINE_URL`, empty, is where a grading machine
reaches the platform, `UNICON_PUBLIC_URL` when empty, and `compose.dev.yaml`
sets it to `http://proxy` for the agent it runs, whose steps reach the
proxy on the grading network.

## The image manifest

`images.json` is the release manifest: every image the stack grades with, by
digest, and the release of each primitive's repository that bootstrap mirrors.

```json
{
  "images": {
    "harness": "ghcr.io/uniconhq/harness@sha256:<64 hex>",
    "clone": "ghcr.io/uniconhq/clone@sha256:<64 hex>",
    "socket-filter": "ghcr.io/uniconhq/socket-filter@sha256:<64 hex>",
    "worker": "ghcr.io/uniconhq/worker@sha256:<64 hex>"
  },
  "primitives": {
    "compile": {
      "image": "ghcr.io/uniconhq/primitive-compile@sha256:<64 hex>",
      "source": {"github": "uniconhq/primitive-compile", "tag": "v1.0.0"}
    }
  }
}
```

`images` holds the runner's images; `harness`, `clone` and `socket-filter` are
required, since bootstrap writes them into `.env`. `primitives` holds one
entry per primitive, keyed by its name at the forge: the image its steps run,
and where its repository is at that release. Bootstrap fetches that tag's
archive from GitHub and mirrors it. Every image is a reference by digest,
never a tag, because a tag can be moved under a running stack; a manifest that
names one is refused before anything starts. A new release of the runner or
of a primitive reaches a deployment as a commit changing this file.

Today it pins runner `v0.3.0` and the three primitives, `compile`,
`sandbox-run` and `diff-check`, at `v1.0.0`.

On a development machine and in CI, `uv run scripts/build-images.py` builds
every one of these images from the sibling checkouts instead: the runner's
four from `../runner`, each primitive from `../primitive-<name>`, as they are
on disk, committed or not. It runs `registry:2` on `127.0.0.1:5000` in a
container of its own, `unicon-registry`, outside the compose stack and with
its images in the volume `unicon-registry`, pushes each image there to learn
its digest, and writes `images.local.json` in the same shape, with each
primitive's source its checkout, `{"path": "../primitive-compile"}`. That file
is git-ignored. Bootstrap reads it whenever it is there and `images.json`
otherwise; `--images <file>` names one. Docker pulls from a registry on
localhost over plain HTTP without being told to, by digest, the same way it
pulls from ghcr.io, so the agent and the socket filter need nothing different.
The build attaches no provenance attestation, which would carry the build's
time and make every build a new digest: an image that did not change keeps
its digest, and bootstrap then leaves its primitive as it is.

## The grading machine in development

`--profile agent` starts what a grading machine is, all from
`compose.dev.yaml`:

- `woodpecker-agent`, with the token bootstrap minted and the label
  `pool=platform`, which every grading run asks for. Its step containers join
  the network `unicon-grading` (`WOODPECKER_BACKEND_DOCKER_NETWORK`), on
  which the proxy is the only service of the stack, so the clone steps reach
  Forgejo through the proxy's git listener and the harness reaches the
  platform at `http://proxy`, which is why the dev overlay sets
  `UNICON_MACHINE_URL` to that. The database, the object store's admin API,
  the CI's API and the backend's own port are not on that network. The agent
  holds the real Docker socket.
- `socket-filter`, the image the manifest names, which holds the real socket
  too and serves its own at `/run/unicon/docker.sock` in the volume
  `unicon-filter`. The harness step mounts that volume read-only and never
  sees the real socket, and cannot replace the filter's; the filter exits,
  and is restarted, should its socket ever be replaced. The filter runs as its
  image's own user, uid 10002, so it joins the group that owns the socket,
  `DOCKER_SOCKET_GID`, which bootstrap reads from the socket:
  0 on Docker Desktop, the docker group on Linux. It runs with `pid: host`,
  because it tells one run's harness from another's by the process that
  connects, and it takes containers only from the images in
  `UNICON_FILTER_IMAGES`, compared exactly with the `image` lines of the
  primitives at the forge; with no primitive seeded that list is empty and
  the filter refuses to start. It is healthy once a ping through its own
  socket reaches the daemon, and the agent starts only then.
- `grading-volumes`, which hands `unicon-filter` to uid 10002, the filter's
  account, and exits.

The store for large task files is one volume per org, `unicon-lfs-<org>`,
which the clone steps mount at `/lfs-cache`, so each large file is fetched
once per org on the machine and no org's task is served another org's. The
pipeline names them and Docker makes each on first use; the clone image runs
as root, so nothing here prepares them. `unicon-filter` has a fixed name
rather than the compose project's prefixed one, because the pipeline the
backend hands the CI names it. Task
repositories are trusted with volumes and nothing else, which the forge sets
when it activates one.

The run's two checkouts clone the task and the submission repository from
the URL Forgejo gives them, on `forge.localhost`, and the CI lends its
credential for that host name alone. Inside a step container the name
reaches nothing: curl, which git and git-lfs speak HTTP through, sends every
name under `.localhost` to loopback without asking DNS, and the step's
loopback is the step itself, so a network alias for the name does not help.
The dev overlay therefore hands every step an HTTP proxy,
`WOODPECKER_BACKEND_HTTP_PROXY` on the CI server, pointing at a second
listener of the proxy, `proxy:3128`, from `proxy/dev-git-door.conf`. Through
a proxy curl never resolves the forge's name and still sends the credential,
since the name in the URL is the one it is for. That listener passes a
checkout's reads for the forge's host, the ref advertisement, the fetch and
the LFS downloads, to Forgejo and answers 404 to everything else, pushes and
LFS uploads included, and so does a request for any other host; it is
reachable only from the compose networks and does not exist in the
production shape. `WOODPECKER_BACKEND_NO_PROXY` leaves out
`proxy` and `host.docker.internal`, so the harness reaches the platform
directly, at the proxy, or on the Docker host where the forge's live tests
run it. A grading machine
outside a deployment will read the same repositories through the machine
door on the forge's public host, which is feature 12.

## The forge's account pages

Forgejo serves its own sign-in, sign-up, OAuth consent and account settings
pages, and people see them whenever the app sends them to the forge. Someone
the operator made with `unicon create-account` also meets its page for
changing the first password, at their first sign-in. What makes
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

## Submit and grade from the browser

```sh
uv run playwright install chromium     # once
UNICON_E2E_URL=http://localhost:8080 uv run pytest tests/e2e
```

`tests/e2e/test_submit_and_grade.py` drives Chromium through the running
stack, both profiles up, from a new org to two verdicts. It makes an
organiser and a contestant with `unicon create-account` and signs both in
through Forgejo's pages. The organiser creates an org, a contest and a task
from the organiser pages, saves `task.yaml` so the task publishes (with a
looser submission rate than the starter's one per 30 seconds, so the second
submit is not refused), and saves `contest.yaml` so the contest is published
and running. The contestant registers, the organiser approves them from the
contestants table, and the contestant submits the sample solution and then a
wrong one from the submit panel; the test expects `ACCEPTED` and then
`WRONG ANSWER` in their submissions list. Both are graded by the real CI on
the dev agent, through the socket filter, with the primitives at the forge.
Every name carries a random suffix, so it runs again on the same stack
without clearing anything; it takes about a minute. Without `UNICON_E2E_URL`
it is skipped, so `uv run pytest` stays a unit run. `UNICON_E2E_COMPOSE` is
the compose command the accounts are made with, from this directory; it
defaults to the dev files.

In CI it runs in the stack job, on the freshly bootstrapped stack, once the
dev agent has connected. That job builds everything from the `main` branch
of each sibling, so it passes only once each of these is on its `main`:

- `forge`: the submit, dispatch, extension, envelope and callback services
  (feature 6), which the backend image builds against through the `forge`
  context while it develops by path, or through the release it pins;
- `backend`: the upload, submission, grading and machine routes, and the
  `Dockerfile` with the `forge` stage;
- `frontend`: the submit panel and the submissions list;
- `runner`: the harness, the clone image and the socket filter at contract
  version 3;
- `primitive-compile`, `primitive-sandbox-run` and `primitive-diff-check`:
  their first versions, each with a `Dockerfile` at the root.

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

CI runs exactly these, and then boots the whole stack: it checks out
`forge`, `backend`, `frontend`, `runner` and the three primitive repositories beside
this repo, builds the grading images with `scripts/build-images.py`, runs
bootstrap against `compose.yaml` and `compose.dev.yaml`, starts the `app`
profile, and checks that `/readyz` answers ready and the frontend serves its
page through the proxy on one origin. It then tries the proxy from outside:
the listed paths of both hosts answer, an unlisted path on either is 404,
Forgejo's API, web UI and git endpoints are 404 on the forge host, the CI's
extension is 404, the object storage paths take only their writes, every
answer carries the security headers, and no container publishes Forgejo on
every interface. From inside the proxy it checks that the development git
listener passes a checkout's reads of the forge's host and answers 404 to a
push, the forge's API, a path that only becomes a checkout path once
resolved, and every other host. It then runs bootstrap a second time and fails if `.env`
changed or the run made anything, and checks that the three primitives and the
workflow are at the forge at `v1`. Between the two it starts the `agent`
profile, waits for the dev agent to connect, and runs the browser test
below.
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
