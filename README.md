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
| `mailpit` | `axllent/mailpit:v1.31.3`, development only | `http://localhost:8025`, the mail Forgejo and the backend send |
| `woodpecker-agent`, `socket-filter`, `grading-volumes` | development only, under `--profile agent` | nothing; the agent dials out to the server |

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
from `.env`, re-applies the bucket grants, rewrites `.env`, and runs
`docker compose up -d` for the `backend` container when it is running, because
a running container never re-reads its environment. Compose recreates it
exactly when its configuration, the values from `.env` included, changed, and
bootstrap says so. On a development stack it does the same for the agent and
the socket filter when they are running. The rewrite drops the keys bootstrap knows nothing reads, ones an
earlier bootstrap wrote, and names them; a key it does not know, such as one
added by hand, is kept at the end under a comment of its own.

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

Without `-f compose.dev.yaml` you get the production shape: third-party
images by release tag, the backend and frontend from their `main`, nothing
published to the host except the proxy on 8080, and sign-up closed.
`SIGN_UP_OPEN` in `.env` is the one switch for people making their own
accounts: bootstrap writes Forgejo's `DISABLE_REGISTRATION`, which takes or
refuses a sign-up, and the backend's `UNICON_FORGE_REGISTRATION_OPEN`, which
decides whether the landing page offers Create account, from it on every run,
so the two always agree. Empty means closed, and open on a development stack.
Opening sign-up in the production shape takes a mail server in `.env`
(`MAIL_SMTP_ADDR` and the rest): everyone who signs themselves up confirms
their address by the link in a mail, a contest's email pattern trusts only
confirmed addresses, and Forgejo drops the confirmation without a word when
it cannot send mail, so bootstrap refuses `SIGN_UP_OPEN=true` without one,
and refuses a mail server without `MAIL_FROM` or with a port that is not a
number, since the backend would not start on either.
The backend sends invite mail through the same server, from the same keys
(`UNICON_MAIL_*` in `compose.yaml`); without one an invite still works, and
its person finds it in Unicon once signed in.
The proxy slows form posts to the forge's pages per client address, 30 a
minute: when a crowd arrives at once from one network, 240 posts over that
pass at once, a lab of 80 signing in for the first time, and only after
those is a post refused with a page asking to wait a minute. Loading a page
is never slowed. It holds at most 3000 open live streams across the site
(`UNICON_LIVE_STREAMS`) and 1000 from any one address
(`UNICON_LIVE_STREAMS_PER_ADDRESS`, the largest lab you expect), so sign-in
and pages always keep connections of their own: a tab whose stream is turned
away asks for what changed on its own, a submission being graded every 5 to
15 seconds and the rest every 30 to 60, and tries the stream again after a
wait that grows to a minute. `proxy/main.conf` gives nginx the connections
those streams fit in, and bootstrap refuses a cap nginx would not start on. The proxy also turns away
a body over 16 MiB anywhere but the upload door.

`UNICON_SESSION_HARD_TTL`, how long a Unicon session lives, is likewise the
one length for both sides: bootstrap writes Forgejo's refresh-token lifetime
from it, in hours, rounded up, so a session never outlives the Forgejo
refresh token it acts with.

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
enrolled. Without the first line bootstrap reads `images.json`, and seeds
the forge only when that pins every primitive version the built-in workflow
uses.

Each primitive checkout fills the version at the forge that is the major of
its own version, `v2` for 2.0.0, and the versions it does not fill come from
`images.json` as released. After a change in one of those checkouts, run the
first two lines again. A version already at the forge is never edited, so a
rebuilt image stops bootstrap before it seeds anything, naming the version
that differs; `uv run bootstrap --rewrite` rewrites it in place instead, so
the tasks already there grade with what was just built.

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

The frontend image holds nothing about the deployment. Where Forgejo is, for
the sign-in, account and sign-out links, it asks the backend at
`GET /api/v1/auth/forge-url`, which answers `FORGEJO_PUBLIC_URL`; so the
published image runs on any stack as it is.

Then:

```sh
curl http://localhost:8080/api/v1/time      # {"now": "..."}
curl -i http://localhost:8080/readyz        # 200 once Postgres answers
curl -i http://localhost:8080/-/proxy       # 204, nginx itself
```

One origin holds all of it: the frontend on `/` and its pages, every organiser
page under `/orgs`, a contest's page and its tasks' pages under `/contests/`,
the API on `/api/`, the health endpoints, and `/-/proxy` for the
proxy's own healthcheck. The backend's API document, `/openapi.json`, is
answered 404: the frontend generates its client from the copy committed in
the backend's repository. Two API paths are
refused: `/api/v1/events/`, where the forge pushes an org's events, and
`/api/v1/ci/`, the CI's configuration extension. Both are called inside the
stack, at `http://backend:8000`, and nothing outside has a reason to call
them. A grading machine's two calls, a grading's envelope and its callback
under `/api/v1/gradings/`, go to the backend with the rest of `/api/`. The
access log drops the query string of `/api/v1/auth/callback`, so the login
`code` and `state` never reach disk, and the same on the forge's host for
the sign-in's start, `/login/oauth/authorize`, and for the address
confirmation and password reset links in Forgejo's mail. Forgejo and Garage
keep queries out of their own logs too: Forgejo's per-request lines are
written only at Warn (`forgejo/app.ini`, `[log.router]`), and Garage's
request log, which would hold presigned signatures, only for server errors
(`RUST_LOG` in `compose.yaml`).

One object storage path goes to Garage, for the one write that comes from
outside: `/unicon-results/` takes PUT, a grading machine's run log. The
request carries a presigned signature the backend made for
`UNICON_MACHINE_URL`, so the proxy passes the Host header through as it came,
and Garage, which reads the bucket from the path, checks the signature that
was made. Nothing else of Garage is reachable, reads and listings included.

What people upload does not come this way. `/-/uploads/<id>` takes PUT, one
file, and before nginx reads a byte of the body it asks the backend whether
that upload may start (`auth_request`). The backend answers, for the owner's
own waiting upload of exactly that length, with where at the forge the bytes
go and the credential to present there, and nginx streams the body to
Forgejo's large-file endpoint with that credential in place of whatever the
browser sent and the app's cookie dropped. Forgejo hashes the body as it
stores it and refuses anything that is not what the address names. Its
refusals name the object store's address in them, so every answer but a
success and the size refusal becomes a bare 403. The door is on the app's own
origin, so it needs no CORS and the session cookie goes with it.

The app is at `http://localhost:8080`, Forgejo at `http://forge.localhost:8080`,
on the same proxy under its own hostname, so the two never share cookies. Any
name under `.localhost` is loopback for browsers and most resolvers, so no
hosts entry is needed; the Woodpecker container reaches the same name through
the Docker host gateway for its OAuth token request. Sign in through the app;
Forgejo is where the account lives. In development Forgejo takes new
sign-ups unless `.env` says `SIGN_UP_OPEN=false`, and sends its mail to
Mailpit, which delivers nothing, as does the backend: the
confirmation link for an account you make, and every invite, is at http://localhost:8025. The dev override also
publishes Forgejo on `127.0.0.1:3300` for `scripts/check-lfs.py`, which
drives its API from this machine; nothing else uses that port.

## What bootstrap made

Two Forgejo accounts. `unicon-backend` is a site administrator and holds the
admin token, `UNICON_FORGE_ADMIN_TOKEN`: the backend uses it to create
organisations, repositories, teams and protected tags, and never to act for a
person.
`compose.yaml` names it to the backend as `UNICON_FORGE_PLATFORM_ACCOUNT`, and
the backend reserves the protected tags for that one account. `unicon-ci` is an
ordinary account that Woodpecker signs in as. Two accounts rather than one,
because Woodpecker insists on a real forge login for its own user and the
site-administrator token must not sit in Woodpecker's database.

Two Forgejo OAuth applications, one for Unicon and one for Woodpecker. Bootstrap
talks to Forgejo's API with the curl inside the Forgejo container, configured
over standard input so no password or token is ever a command-line argument,
and to Garage through `garage json-api` inside its container, which speaks the
same admin API over Garage's internal RPC. Neither needs a port open for it. Two Garage buckets: `forgejo-lfs` for
Forgejo, which holds every file a person uploads, and `unicon-results` for
the backend, which holds grading logs, with a separate access key for each
side. It creates no other bucket and removes none. A Woodpecker API token
for `unicon-ci`.

Three primitives and one workflow at the forge, in the platform org
`unicon`, which bootstrap makes, owned by `unicon-backend`. A task's first
save has to find a workflow and its primitives the organiser can read, or
nothing can be published.

Each primitive the image manifest pins is mirrored from its own repository
into the public repository `unicon/<name>.primitive` with the topic
`unicon-primitive`: `compile`, `sandbox-run` and `diff-check`. A primitive's
version at the forge is its port set, the major of its release tag: the
release `v1.1.1` is `v1`, and `v2.0.0`, which changes the ports, is `v2`. The
manifest lists each version a deployment carries, and each is mirrored to its
own tag: the primitive repository's files at that release, less its CI
configuration, in one commit, with the tag pointing at it. The forge's
compiler reads `primitive.yaml` at the root of a version. A primitive's own
repository names no image in that file, so bootstrap writes the `image`, the
manifest's digest, in as its first line. A `v1` release's declaration still
starts with `name` and `version` lines; for it bootstrap writes `version` and
`image` under `name`, which is how every `v1` at the forge was written.

A version is that file, and a version is never edited. A tag already at the
forge is left as it is: when it holds the same declaration the run makes
nothing, and when it holds another, with a different digest or any other
change, the run stops. Bootstrap compares every version with the forge before
it seeds any, so a run that finds one differing seeds nothing and names each
that differs. A new image for a primitive is a new version in the manifest, so
a published task keeps grading against what it was published with. A change
to the other files alone is no difference, since the program that runs is the
one in the image and a rebuilt image is a new digest. A run that finds nothing
changed makes nothing.

The workflow is the public repository `unicon/classic.workflow` with the
topic `unicon-workflow`. Each of its versions is a folder here,
`workflows/classic/v1/` and `workflows/classic/v2/`, holding the
`workflow.yaml` and `README.md` the version's tag holds, and bootstrap
publishes each to its own tag by the same rule: a tag that is not there is
made, one that is there is never edited, and one that differs from its
folder stops the run the same way. That is the built-in workflow: compile the
submission, run it on every test, compare each output with the answer.
`unicon/classic@v1`
wires the primitives' `v1` in the format they were built for;
`unicon/classic@v2` wires their `v2` in the format of `TASK-FORMAT.md`, with
each test's `input` and `answer`, and reports the time and memory of every
run. A changed definition is a new folder. Every primitive version a
definition `use:`s has to be one the manifest pins: bootstrap refuses one
that is not before it writes anything, since the forge would refuse every
task saved against that workflow version.

The one exception to both is a development stack. `uv run bootstrap
--rewrite` rewrites every listed version of the workflow and of each
primitive in place to what is here now, with a new commit and the tag moved
to it, rather than stopping at the version at the forge that differs, so a
primitive rebuilt on the laptop is what the tasks already there grade with.
It refuses to run without `compose.dev.yaml` among the compose files.

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
in the next generated `.env`. The rest of what the backend reads,
`compose.yaml` writes itself: the database URL, from `UNICON_DB_PASSWORD`; the
Forgejo a browser uses, from `FORGEJO_PUBLIC_URL`; the two bucket names and
the platform account, which are what bootstrap makes; and the addresses inside
the stack, Forgejo at `http://forgejo:3000`, the CI at
`http://woodpecker-server:8000`, Garage at `http://garage:3900` in the region
`garage/garage.toml` names, and the backend itself at `http://backend:8000`,
which is where every org's event push and the CI's configuration extension
point, and why `forgejo/app.ini` allows the host `backend` for webhooks. The
backend marks its cookies Secure exactly when `UNICON_PUBLIC_URL` is https.
Two more keys of `.env` are settings rather than secrets, with defaults
bootstrap writes as they are: `UNICON_ORG_CREATION_OPEN`, `true`, lets any signed-in
person create an org, and a deployment open to strangers sets it to `false`
and creates orgs with `unicon create-org`; `UNICON_MACHINE_URL`, empty, is where a grading machine
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
      "v1": {
        "image": "ghcr.io/uniconhq/primitive-compile@sha256:<64 hex>",
        "source": {"github": "uniconhq/primitive-compile", "tag": "v1.1.1"}
      },
      "v2": {
        "image": "ghcr.io/uniconhq/primitive-compile@sha256:<64 hex>",
        "source": {"github": "uniconhq/primitive-compile", "tag": "v2.0.0"}
      }
    }
  }
}
```

`images` holds the runner's images; `harness`, `clone` and `socket-filter` are
required, since bootstrap writes them into `.env`. `primitives` holds each
primitive by its name at the forge, and under it each version by its tag at
the forge, `v1`, `v2` and on: the image that version's steps run, and where
its repository is at that release. A version is the major of its release
tag, so a release tag under a key of another major, `v2.0.0` under `v1`, is
refused. Bootstrap fetches each tag's archive from GitHub and mirrors it to
the version's tag. Every image is a reference by digest, never a tag, because
a tag can be moved under a running stack; a manifest that names one is
refused before anything starts. A new release of the runner reaches a
deployment as a commit changing this file, and so does a new major of a
primitive, as one more version beside the ones before it. A new minor or
patch release of a primitive is a new pin under the same version, so it
reaches a stack that does not hold that version yet; on a stack that does,
its other image or declaration stops bootstrap, since a version at the forge
is never edited.

Today it pins runner `v0.5.0` and the primitives' `v1`: `sandbox-run` at
`v1.3.0`, and `compile` and `diff-check` at `v1.1.1`.

On a development machine and in CI, `uv run scripts/build-images.py` builds
every one of these images from the sibling checkouts instead: the runner's
four from `../runner`, each primitive from `../primitive-<name>`, as they are
on disk, committed or not. It runs `registry:2.8.3` on `127.0.0.1:5000` in a
container of its own, `unicon-registry`, outside the compose stack and with
its images in the volume `unicon-registry`, pushes each image there to learn
its digest, and writes `images.local.json` in the same shape. Each
primitive's checkout fills the version that is the major of the version in
its `pyproject.toml`, with its source the checkout,
`{"path": "../primitive-compile"}`; every other version of the primitive is
copied from `images.json` as released, so a stack built this way grades
`v1` with the released image from ghcr.io and `v2` with the checkout's. On a
development stack bootstrap pulls every one of those images, since the socket
filter lets a step start only from an image already on the machine. That file
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
  the filter refuses to start. It never pulls an image, so every one of those
  images has to be on the machine before a run needs it: on a development
  stack bootstrap pulls each one this Docker does not hold yet, and on a
  platform machine the worker does it when the machine is enrolled. It is
  healthy once a ping through its own socket reaches the daemon, and the
  agent starts only then.
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
pages, and people see them whenever the app sends them to the forge. Signing
out of the app ends at the proxy's `/-/sign-out` on the forge's host, which
clears Forgejo's `session` and `persistent` cookies and sends the browser back to
the app, so a sign-out leaves nobody signed in to Forgejo either; Forgejo takes
a sign-out of its own only as a form post with its own token. Someone
the operator made with `unicon create-account` also meets its page for
changing the first password, at their first sign-in. What makes
those pages look like Unicon is under `forgejo/custom/`, which compose mounts
read-only into Forgejo's custom path, `/data/gitea`:

- `public/assets/img/` holds the Unicon mark as `logo.svg` and `favicon.svg`,
  the PNG sizes Forgejo wants beside them, and `avatar_default.png`. These are
  the file names Forgejo looks up itself, so they stand in for its own.
- `public/assets/css/unicon.css` is one stylesheet. It hides Forgejo's
  navigation bar and footer, hides every item of the settings menu but the
  three pages the proxy routes, Profile, Account and Security, so a page a
  later Forgejo adds stays hidden too, and sets Forgejo's colour, radius and
  font variables to the frontend theme's values, so the forms take the app's
  look.
  The font families are named with fallbacks only, and the page fetches
  nothing from outside.
- `templates/custom/header.tmpl` is the one template, and it is Forgejo's
  extension point rather than one of its pages: Forgejo includes it at the end
  of `<head>`, and it carries the link to the stylesheet.

`forgejo/app.ini` turns off what those pages would offer and nothing here
supports: deleting the account, which is Unicon's flow, SSH and GPG keys,
since git is closed to people (`USER_DISABLED_FEATURES`), and the package
registry. Forgejo then refuses them itself, not only the proxy.

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
so the output names a ceiling that works. `--exact` fails on the size asked
for, which is how CI runs it.

## Submit and grade from the browser

```sh
uv run playwright install chromium     # once
UNICON_E2E_URL=http://localhost:8080 uv run pytest tests/e2e
```

`tests/e2e/test_submit_and_grade.py` drives Chromium through the running
stack, both profiles up, from a new org to two verdicts. It makes an
organiser and a contestant with `unicon create-account` and signs both in
through Forgejo's pages. The organiser creates an org, a contest and a task
from the organiser pages, opening each one's page as soon as it is made, saves `task.yaml` so the task publishes (with a
looser submission rate than the starter's one per 30 seconds, so the second
submit is not refused), checks that making the task added it to
`contest.yaml`'s tasks, and saves `contest.yaml` so the contest is published
and running. The contestant registers, the organiser approves them from the
contestants table, and the contestant submits the sample solution and then a
wrong one from the submit panel; the test expects `ACCEPTED` and then
`WRONG ANSWER` in their submissions list. Both are graded by the real CI on
the dev agent, through the socket filter, with the primitives at the forge.
Last, the contestant signs out, and signing in again asks Forgejo for their
password: signing out of the app signed the browser out of Forgejo too.
Every name carries a random suffix, so it runs again on the same stack
without clearing anything; it takes about a minute. Without `UNICON_E2E_URL`
it is skipped, so `uv run pytest` stays a unit run. `UNICON_E2E_COMPOSE` is
the compose command the accounts are made with, from this directory; it
defaults to the dev files.

In CI it runs in the stack job, on the freshly bootstrapped stack, once the
dev agent has connected. That job builds everything from the `main` branch
of each sibling, so it passes only once each of these is on its `main`:

- `forge`: the submit, grading, extension, envelope and callback services
  (feature 6), which the backend image builds against through the `forge`
  context while it develops by path, or through the release it pins;
- `backend`: the upload, submission, grading and machine routes, and the
  `Dockerfile` with the `forge` stage;
- `frontend`: the submit panel and the submissions list;
- `runner`: the harness, the clone image and the socket filter at contract
  version 5;
- `primitive-compile`, `primitive-sandbox-run` and `primitive-diff-check`:
  at 2.0.0, their `v2` at the forge, each with a `Dockerfile` at the root.

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
resolved, and every other host. It checks that sign-up is open on both
sides, Forgejo's form and the backend's Create account link. It then runs bootstrap a second time and fails if `.env`
changed or the run made anything, and checks that the three primitives and the
workflow are at the forge at `v1` and at `v2`, each as this run would write it. Between the two it starts the `agent`
profile, waits for the dev agent to connect, runs the browser test above,
and runs `scripts/check-lfs.py --exact` for one 500 MB file. Last, it sends a burst of sign-in posts and expects some through and
some refused with 429, then sets `SIGN_UP_OPEN=false`, runs bootstrap again
and checks that both sides have closed.
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
