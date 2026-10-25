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
| `woodpecker-agent` | development only, under `--profile agent` | nothing; the agent dials out to the server |

No grading machine is part of the stack: a machine that runs contestant code
is a separate host, enrolled at the CI with its own token. The server carries
no shared agent secret, so a machine that presents no issued token is refused.
`compose.dev.yaml` can run one on the laptop under `--profile agent`: a CI
agent, with its token minted by bootstrap. It is the only place an agent and
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
bootstrap says so. On a development stack it does the same for the agent when
it is running. The rewrite drops the keys bootstrap knows nothing reads, ones an
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

## Upgrading a running stack

After a new release, with this repo at the release:

```sh
COMPOSE="docker compose -f compose.yaml --profile app"
$COMPOSE pull backend-migrate backend frontend
$COMPOSE up -d backend-migrate backend frontend
uv run bootstrap
$COMPOSE exec -T backend unicon-forge reconcile
```

The first two lines bring the app to the new images; `backend` waits for
`backend-migrate` to apply the release's migrations. Bootstrap then seeds
any new primitive or workflow version and moves a seeded version that
differs only in its words, as [What bootstrap made](#what-bootstrap-made)
says. It comes after the backend, since an older backend may not read what
the new versions say.

The last line runs the forge's `reconcile` once. Like `backend-migrate`'s
`unicon-forge migrate`, it is the forge package's command from the backend
image, but it reads every `UNICON_*` setting the backend has, so it runs in
the running `backend` container rather than a job of its own. It puts back
whatever keeps each contest and task right at the forge where it is missing
or wrong, so places made before the upgrade get what the release now gives
each new one. Among those is the protection on each contest's and task's
branch that lets the platform's account alone push or merge to it, which
contests and tasks made before that protection came in lack until it runs.
It also gives every submission at the forge without a grading its
grading, against its task's current publication, which on a stack that
kept running finds nothing to do. It logs what it did as
`reconcile.done`, with how many places it secured again, and running it
again does no harm. A stack bootstrapped fresh on the release needs none of
this: every contest and task is secured as it is made.

On a development stack the same holds with `-f compose.dev.yaml` added to
`COMPOSE`, the `pull` line left out and `--build` added to the `up` line;
[Back to a known state](#back-to-a-known-state) starts from nothing instead.

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
(`runner`, `primitive-compile` and `primitive-exec`, beside this repo),
pushes them to a registry on
`localhost:5000` and writes `images.local.json`, which bootstrap then reads
instead of `images.json`; see [The image manifest](#the-image-manifest). The
last line starts the grading machine, whose agent bootstrap has already
enrolled. Without the first line bootstrap reads `images.json`, and seeds
the forge only when that pins every primitive version the built-in
workflows use.

Each primitive checkout fills the version at the forge that is the major of
its own version, `v2` for 2.0.0, and the versions it does not fill come from
`images.json` as released. After a change in one of those checkouts, run the
first two lines again. A rebuilt image under the same declaration moves its
version at the forge to the new digest, as a patch release does, and so does
a `primitive.yaml` changed only in its words, a title, a description or what
an outcome means. One changed in anything else stops bootstrap before it
seeds anything, naming the version that differs; `uv run bootstrap
--rewrite` rewrites it in place instead.
Either way a task already published keeps grading with the image its plan
pinned until it is saved again.

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
them. A grading machine's calls, each step's door under
`/api/v1/gradings/`, go to the backend with the rest of `/api/`. The
access log drops the query string of `/api/v1/auth/callback`, so the login
`code` and `state` never reach disk, and the same on the forge's host for
the sign-in's start, `/login/oauth/authorize`, and for the address
confirmation and password reset links in Forgejo's mail. Forgejo and Garage
keep queries out of their own logs too: Forgejo's per-request lines are
written only at Warn (`forgejo/app.ini`, `[log.router]`), and Garage's
request log, which would hold presigned signatures, only for server errors
(`RUST_LOG` in `compose.yaml`).

Two object storage paths go to Garage, for what a grading machine moves
straight to and from it: `/unicon-results/` takes PUT, a run's log and a
step's outputs, and GET, an earlier step's output, and `/forgejo-lfs/lfs/`
takes GET, a task's large file. Each request carries a presigned signature
the backend made for `UNICON_MACHINE_URL`, so the proxy passes the Host
header through as it came, and Garage, which reads the bucket from the path,
checks the signature that was made. A GET is routed only with a presigned
query and the backend signs a read of `forgejo-lfs` with a key that may only
read it. Nothing else of Garage is reachable: no listing, no write to
`forgejo-lfs`, no other bucket.

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

Downloads come back the same way. `/-/downloads/<org>/<contest>/<task>/<number>/files/...`
is one file of a contestant's own submission, and
`/-/downloads/<org>/<contest>/<task>/versions/<commit>/<path>` one of a task's
own files at a version, for whoever holds a role reaching the task. Each takes
GET alone, and nginx asks the backend first; the backend answers with the
forge's media endpoint for that file, in a submission's repository or the
task's, and the person's own credential, and nginx streams Forgejo's answer
whatever its size, a range included, as an attachment with its own security
headers and the app's cookie dropped. Two maps hold each answer to the one
shape its address may lead to, and every refusal, nginx's, the backend's or
Forgejo's, reads as 404.

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

### Back to a known state

```sh
uv run scripts/reset-dev.py            # --build to rebuild the backend and frontend too
```

throws the dev stack's data away (`down -v`), bootstraps an empty stack,
starts both profiles from the images already built, and leaves two people
and one contest to try things on:

- `alice` and `bob`, with the passwords and email addresses in `.env`,
  which you add by hand and bootstrap keeps:

  ```sh
  DEV_ALICE_PASSWORD=...
  DEV_ALICE_EMAIL=...
  DEV_BOB_PASSWORD=...
  DEV_BOB_EMAIL=...
  ```

- the org `demo`, alice its admin, and its contest `sample`, "Sample
  contest", running from an hour before the reset for 30 days, open to
  anyone who signs up and approved by an organiser;
- its task `sum`, on `unicon/classic@v3`: A + B in Python, its statement,
  three tests and a submission rate of 10 in any 60 seconds, in one save
  before the contest opens; the contest opens once that save is published,
  so a submission grades.

Bob has not registered. It takes about three minutes, and it refuses before
throwing anything away when a key is missing. Sign in again from a new tab
on the app: bootstrap registers the app at Forgejo afresh, so a page open from
before the reset sends Forgejo a client id it no longer has ("Client ID not
registered"). Alice and bob are signed in through Forgejo's pages and the
org, the contest and the task made through the API as alice, with the
browser tests' own steps (`tests/e2e/stack.py` and `tests/e2e/api.py`), so
it needs `uv run playwright install chromium` once.

## What bootstrap made

Two Forgejo accounts. `unicon-backend` is a site administrator and holds the
admin token, `UNICON_FORGE_ADMIN_TOKEN`: the backend uses it to create
organisations, repositories, teams, protected tags and protected branches,
and to write each change an organiser saves to a contest or a task, with
the organiser named as the change's author; it is otherwise never used to
act for a person.
`compose.yaml` names it to the backend as `UNICON_FORGE_PLATFORM_ACCOUNT`, and
the backend reserves the protected tags for that one account and lets it
alone push or merge to a contest's or a task's branch, repository admins
included, so an organiser changes those files only through the app's save.
The name has to be the account the admin token belongs to, or every save is
refused at the forge. `unicon-ci` is an
ordinary account that Woodpecker signs in as. Two accounts rather than one,
because Woodpecker insists on a real forge login for its own user and the
site-administrator token must not sit in Woodpecker's database.

Two Forgejo OAuth applications, one for Unicon and one for Woodpecker. Bootstrap
talks to Forgejo's API with the curl inside the Forgejo container, configured
over standard input so no password or token is ever a command-line argument,
and to Garage through `garage json-api` inside its container, which speaks the
same admin API over Garage's internal RPC. Neither needs a port open for it. Two Garage buckets: `forgejo-lfs` for
Forgejo, which holds every file a person uploads, and `unicon-results` for
the backend, which holds grading logs and steps' outputs, with a separate
access key for each side, and a third, `unicon-backend-lfs-read`, that may
only read `forgejo-lfs`, which the backend signs a grading machine's
downloads of a task's large files with. It creates no other bucket and removes none. A Woodpecker API token
for `unicon-ci`.

The primitives and the built-in workflows at the forge, in the platform org
`unicon`, which bootstrap makes, owned by `unicon-backend`. A task's first
save has to find a workflow and its primitives the organiser can read, or
nothing can be published.

Each primitive the image manifest pins is mirrored from its own repository
into the public repository `unicon/<name>.primitive` with the topic
`unicon-primitive`: `compile` and `exec`. A primitive's
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

A version is that file, and its ports never change. A tag already at the
forge that holds the same declaration is left as it is, and the run makes
nothing. The two are compared as the compiler reads them, parsed under YAML
1.2's core schema: the ports with their types and options, the limits with
`gpus`, `limits_from`, `network` and `batch`. One that differs only in
what the compiler does not read, its image, its `title`, its own or a port's
`description`, the outcome port's `outcomes`, a comment or the layout, is a patch release, a fixed image with the same ports, or a
reworded one: bootstrap moves the tag to a commit of the release's files
with the manifest's digest written in, and says `(moved)`. One whose
declaration differs in anything the compiler reads stops the run. Bootstrap compares
every version with the forge before it seeds any, so a run that finds one
differing seeds nothing and names each that differs; new ports are a new
version in the manifest. A plan pins each step's image by digest, so a task
already published keeps grading with the image it was published with until
it is next saved, and the CI's privileged list keeps every image a version
of `exec` has named, read from the repository's history. A change to the other files
alone is no difference, since the program that runs is the one in the image
and a rebuilt image is a new digest. A run that finds nothing changed makes
nothing.

Each built-in workflow is a folder under `workflows/`, and each is the
public repository `unicon/<name>.workflow` with the topic
`unicon-workflow`. Each of a workflow's versions is a folder in its own,
`workflows/classic/v3/` and `workflows/interactive/v1/`, holding
every file the version's tag holds: its `workflow.yaml` and `README.md`,
and the files its steps read from the workflow itself, such as
`classic@v3`'s diff script under `diff/` and `interactive@v1`'s verdict
script under `verdict/`. Python's caches are left out, and a link is
refused. Bootstrap publishes each to its own tag by the same rule: a tag
that is not there is made, and one that is there and holds the same files
is left as it is. One whose definition differs only in what does not
grade, an input's `title`, `help` or `unit`, an output's `title` or
`unit`, a comment or the layout, or whose README differs, with every other
file the same, is moved to the new files, and the run says `(moved)`; so a
version is fixed in how it grades, while the words a person reads at its
tag can change. One whose definition differs in anything that grades, or
any of whose other files differs, stops the run the same way. The
definitions are compared as the forge reads them: an input written as a
bare type, `time_limit: number`, is `{type: number}`, an output written as
a bare reference is one given `from` alone, and a flag set `false` is one
left out.
`unicon/classic@v3` is one run of the entrant's program, in the format of
`SIMPLER-CORE.md` 7: compiled with `compile@v3`, run with `exec@v1` and
compared word by word by its diff script; a task repeats it over its tests.
It gives the run's time and memory, which pages name "Run time", in ms, and
"Memory used", in KB, and the compiler's "Compile log". `unicon/interactive@v1` runs the
entrant's program against the task's judge with `exec@v1`'s programs form, and its
verdict script decides the run. A definition changed in how it grades is a
new folder. Every primitive version a
definition `use:`s has to be one the manifest pins: bootstrap refuses one
that is not before it writes anything, since the forge would refuse every
task saved against that workflow version.

The one exception to both is a development stack. `uv run bootstrap
--rewrite` rewrites every listed version of each workflow and of each
primitive in place to what is here now, with a new commit and the tag moved
to it, rather than stopping at the version at the forge that differs, so a
declaration changed on the laptop is what the next save compiles against.
It refuses to run without `compose.dev.yaml` among the compose files.

`unicon/classic@v2` and `unicon/classic-folder@v1` are not seeded. A
development stack that still has their tags, including one rewritten when
their `fold: max` lines went, should be started fresh.

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
grading runs beside the primitives, from the image manifest:
`UNICON_PLUGIN_GIT_IMAGE`, Woodpecker's git plugin, which checks the
submission out and is the one image the CI lends a repository's credential
to, and `UNICON_PRIMITIVE_BASE_IMAGE`, the step wrapper's base image, which
runs a pipeline's last step; the backend puts both into what it hands the
CI. `UNICON_WITHDRAWN_IMAGES` is set by hand: images by digest no grading
may run any more, matched by the digest whatever name they are given under,
which bootstrap keeps out of the images the CI lets run privileged, saying
which entry no primitive at the forge runs, and which the forge refuses. `.env.example` lists every key
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
    "worker": "ghcr.io/uniconhq/worker@sha256:<64 hex>",
    "plugin-git": "docker.io/woodpeckerci/plugin-git@sha256:<64 hex>"
  },
  "primitives": {
    "compile": {
      "v1": {
        "image": "ghcr.io/uniconhq/primitive-compile@sha256:<64 hex>",
        "source": {"github": "uniconhq/primitive-compile", "tag": "v1.1.1"}
      },
      "v3": {
        "image": "ghcr.io/uniconhq/primitive-compile@sha256:<64 hex>",
        "source": {"github": "uniconhq/primitive-compile", "tag": "v3.0.0"}
      }
    },
    "exec": {
      "v1": {
        "image": "ghcr.io/uniconhq/primitive-exec@sha256:<64 hex>",
        "source": {"github": "uniconhq/primitive-exec", "tag": "v1.0.1"}
      }
    }
  }
}
```

`images` holds the runner's worker and Woodpecker's git plugin, and on a
local build the runner's `primitive-base`; `plugin-git` is required, since
bootstrap writes it into `.env` as the one image the CI lends a
repository's credential to. `primitives` holds each
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
patch release of a primitive is a new pin under the same version: on a stack
that holds that version, a new image under the same declaration moves the
version to it, and any other change to the declaration stops bootstrap,
since a version's ports never change.

Today it pins runner `v0.10.0`, `compile`'s `v1` at `v1.1.1` and its `v3`
at `v3.0.0`, and `exec`'s `v1` at `v1.0.1`.

On a development machine and in CI, `uv run scripts/build-images.py` builds
every one of these images from the sibling checkouts instead: the runner's
five from `../runner`, `primitive-base` first, each primitive from
`../primitive-<name>`, as they are on disk, committed or not, each built on
that `primitive-base` by Docker's own builder. It runs `registry:2.8.3` on `127.0.0.1:5000` in a
container of its own, `unicon-registry`, outside the compose stack and with
its images in the volume `unicon-registry`, pushes each image there to learn
its digest, and writes `images.local.json` in the same shape. Each
primitive's checkout fills the version that is the major of the version in
its `pyproject.toml`, with its source the checkout,
`{"path": "../primitive-compile"}`; every other version of the primitive is
copied from `images.json` as released, so a stack built this way seeds
`compile`'s `v1` with the released image from ghcr.io and its `v3` with
the checkout's. That file is git-ignored. Bootstrap reads it whenever it
is there and `images.json` otherwise; `--images <file>` names one. Docker
pulls from a registry on localhost over plain HTTP without being told to,
by digest, the same way it pulls from ghcr.io, so the agent needs nothing
different.
The build attaches no provenance attestation, which would carry the build's
time and make every build a new digest: an image that did not change keeps
its digest, and bootstrap then leaves its primitive as it is.

## A grading machine

`machine/grading-machine.py` makes an Ubuntu 24.04 host a grading machine
and checks it, run as root with the machine's settings in a JSON file; its
docstring and `Settings` say what each setting is:

```sh
python3 grading-machine.py setup /etc/unicon/machine.json
python3 grading-machine.py self-test /etc/unicon/machine.json
```

It builds the one shape stage 4 picked: rootless Docker under a throwaway
user, `unicon-grader`, with its data root on a filesystem of its own, and
an nftables table on that user's uid that lets its traffic reach only the
addresses the settings allow, IPv4 and IPv6 alike. The daemon tells
containers to ask `192.0.2.1` for names, which answers nothing; every step
gets `/etc/unicon/hosts` as its `/etc/hosts` and `/etc/unicon/cache-cap`,
and the names the daemon and the agent use are pinned in the machine's own.
The root Docker is disabled, and the cgroup hierarchy counts each cgroup's
memory kills alone, so a box killed for its memory does not fail its step.
The machine is cut into slots, each of one size of `machine/slots.json`,
the one table of slot sizes, which bootstrap also writes into `.env` as
`UNICON_SLOT_SIZES` for the forge to plan each run task pipeline into
(keep the table beside the script on the machine). A `small` slot is one
core and 2 GB, which holds a compile, an `exec` step of a 1 GB run or
of two 512 MB programs; a `big` slot is two cores and
8 GB. Setup keeps a core and 1.5 GB for the system, the agents and the
daemon, so the smallest machine it takes has 2 cores and 3584 MB of
memory as the kernel reports it (`MemTotal`), as a 4 GB machine does,
and runs one small slot; setup refuses a smaller one and says so. It
gives a big slot only while at least two small ones still fit beside it,
then as many small ones as fit: a 4-core 8 GB machine runs three small
slots, an 8-core 16 GB one a big and three small, a 16-core 64 GB one a
big and thirteen small. `slots` in the settings gives the counts instead.
Every slot takes any grading, first come, first served; settings that
still name `early_share` are refused as unknown. Each slot has an
agent of its own, `unicon-agent@<slot>.service`, which takes one workflow at a
time, pins every container of it to the slot's cores and caps it at the
slot's memory with no swap, and has the labels
`pool=platform,shape=rootless,size=<size>`; a big slot has `size=*`, so
small work overflows into it, though Woodpecker gives a pipeline to the
agent its labels match most exactly first. The throwaway user's
slice, which holds every container, is capped at the machine's memory
less 1 GB.
The slots are fixed until setup runs again. Every agent runs as a user of
its own, `unicon-agent`, from a binary checked against
`agent_binary_sha256`, and starts only while the wall is loaded. `setup`
checks every setting and works the slots out before it changes anything,
disables every agent first, kills the throwaway user's containers and
stops its daemon before it takes the wall down, and enables the agents
only when every check of the self-test passes, each slot's limits among
them; a check that could not be tried says "not tested". It prints `network wall: on`
or `off`. Run again, it takes new settings and new pinned addresses. A
restart or reload of `nftables.service`, whose rules file flushes every
table, loads the wall again after it.

`agent_binary_sha256` pins the agent binary inside `agent_image` for each
architecture, by `uname -m`'s name. To find the digests, copy the binary
out of the pinned image for each platform and hash it, on any machine
with Docker:

```sh
image='woodpeckerci/woodpecker-agent:v3.18.1@sha256:...'  # agent_image
for platform in linux/amd64 linux/arm64; do  # x86_64, then aarch64
  id=$(docker create --platform "$platform" "$image")
  docker cp "$id:/bin/woodpecker-agent" - | tar -xO | sha256sum
  docker rm "$id" > /dev/null
done
```

It has run on a real Ubuntu 24.04 (arm64, in a VM), where the self-test
passed, and on a stand-in, an Ubuntu 24.04 container booting systemd over
Docker Desktop's kernel, where the kernel settings, the block devices and
the cgroup option fail, as they should on that kernel.

## The grading machine in development

`--profile agent` starts what a grading machine is, from
`compose.dev.yaml`: `woodpecker-agent`, with the token bootstrap minted and
the label `pool=platform`, which every grading run asks for, and any slot
size, `size=*`, which a run task's pipeline asks for. It also says
`shape=rootless`, which a pipeline that runs `exec` asks for, though Docker
Desktop runs Docker as root: the CI server hands every step
`UNICON_DEV_ROOT_DOCKER=1` (`WOODPECKER_ENVIRONMENT`), and exec then runs
under root Docker, warning on every step, where it otherwise refuses. Only
this overlay sets it; a real grading machine is rootless. Its step containers
join the network `unicon-grading` (`WOODPECKER_BACKEND_DOCKER_NETWORK`), on
which the proxy is the only service of the stack, so the checkout reaches
Forgejo through the proxy's git listener and the other steps reach the
platform at `http://proxy`, which is why the dev overlay sets
`UNICON_MACHINE_URL` to that. The database, the object store's admin API,
the CI's API and the backend's own port are not on that network. The agent
holds the real Docker socket, and no step is given it.

A grading's checkout clones the submission from the URL Forgejo gives it,
on `forge.localhost`, and the CI lends its credential for that host name
alone. Inside a step container the name
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
`proxy` and `host.docker.internal`, so a step reaches the platform
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
a sign-out of its own only as a form post with its own token. Every forge
page people see starts with a "Back to Unicon" link: to the proxy's
`/-/back`, which sends them to the app's Account page, or for someone not
signed in at the forge to `/-/home`, the app's home. The page asking them to
approve an app also links to `/-/home`: they are signed in at the forge
there but not yet in the app, whose Account page would lead them back to
it. The forge's own front
page, `/`, sends the browser to the app's sign-in and back to the app's
home, which is where Forgejo sends a new account once its address is
confirmed, so confirming lands the person in the app, signed in. Someone
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
- `templates/custom/header.tmpl` and `templates/custom/body_inner_pre.tmpl`
  are Forgejo's extension points rather than its pages: Forgejo includes the
  first at the end of `<head>`, where it links the stylesheet, and the second
  at the top of every page's body, where it puts the "Back to Unicon" link
  and, on Unicon's consent page, a line saying it is asked once.

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

## Browser tests

```sh
uv run playwright install chromium     # once
UNICON_E2E_URL=http://localhost:8080 uv run pytest tests/e2e
```

`tests/e2e` drives Chromium through the running stack, both profiles up,
and the API with a signed-in browser's session. Each test makes its people
with `unicon create-account` and signs them in through Forgejo's pages.
`test_leaderboards.py` ranks a contest on three leaderboards built on its
Leaderboards page, before and after a test group's reveal, its entrants'
solutions graded by the real CI on the dev agent with the primitives at
the forge. `test_downloads.py` downloads one of a task's files through the
proxy's download door, an uploaded one and a typed one, as the organiser,
checking the bytes and the door's headers, and finds that an entrant and
someone signed out get no such file. `test_forge_pages.py` follows "Back
to Unicon" from Forgejo's consent page to the app's home.
`test_memory.py` and `test_escape.py` grade a task on `unicon/classic@v3`,
and the runner's escape fixtures, through the API; they need a grading
machine with `exec`'s shape, so they are skipped unless `UNICON_E2E_ESCAPE`
is set.
Every name carries a random suffix, so it runs again on the same stack
without clearing anything; it takes about a minute. Without `UNICON_E2E_URL`
it is skipped, so `uv run pytest` stays a unit run. `UNICON_E2E_COMPOSE` is
the compose command the accounts are made with, from this directory; it
defaults to the dev files.

In CI it runs in the stack job, on the freshly bootstrapped stack, once the
dev agent has connected. That job builds everything from each sibling's
`main`, or, on a pull request, from the sibling's branch of the pull
request's own name when it has one, so a change made across repos is tried
together before any of it merges; run by hand, it takes the branch its
`siblings` input names. On `main` it passes only once each of these is on
its `main`:

- `forge`: the submit, grading, extension and step services, which the backend image builds against through the `forge`
  context while it develops by path, or through the release it pins;
- `backend`: the upload, submission, grading and machine routes, and the
  `Dockerfile` with the `forge` stage;
- `frontend`: the submit panel and the submissions list;
- `runner`: `primitive-base` and the worker;
- `primitive-compile` and `primitive-exec`: each with a `Dockerfile` at
  the root, built on `primitive-base`.

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
`forge`, `backend`, `frontend`, `runner`, `primitive-compile` and
`primitive-exec` beside this repo, builds the grading images with `scripts/build-images.py`, runs
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
changed or the run made anything, and checks that `compile` at `v1` and
`v3`, `exec` at `v1`, `classic` at `v3` and `interactive` at `v1` are at
the forge, each as this run would write it. Between the two it starts the `agent`
profile, waits for the dev agent to connect, runs the browser tests above,
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
