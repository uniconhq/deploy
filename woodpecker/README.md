# Woodpecker

No configuration files. Woodpecker is configured entirely by environment
variables, which are in `compose.yaml` next to the service, and by the API token
that `bootstrap` mints.

The settings below are load-bearing and easy to undo by accident.

**`WOODPECKER_DATABASE_DRIVER: postgres`.** The default is SQLite, which loses
pipelines under concurrent dispatch. Never change it back.

**`WOODPECKER_EXPERT_FORGE_OAUTH_HOST`.** Woodpecker makes its API calls to
Forgejo over the compose network but has to send a browser to the public Forgejo
URL. Without this variable the sign-in redirect points at `http://forgejo:3000`,
which resolves nowhere outside Docker. The catch is that Woodpecker uses this
same host for the server-side token request, so the public URL has to be
reachable from inside the Woodpecker container as well. On a real deployment,
where Forgejo has a name in DNS, it already is; in development
`compose.dev.yaml` adds a host entry pointing `forge.localhost` at the Docker
host, so `http://forge.localhost:8080` reaches Forgejo through the proxy from
inside the container too.

**No `WOODPECKER_AGENT_SECRET` on the server.** With no shared secret an
agent connects only with a token the server issued for it, so a machine
becomes an agent by enrolment and nothing else. No agent runs in
`compose.yaml`; `compose.dev.yaml` runs one on the laptop under
`--profile agent` with `WOODPECKER_DEV_AGENT_TOKEN`, the token of the global
agent `laptop`, which bootstrap enrols through the administrator API
(`POST /api/agents`).

**The configuration extension, exclusive.** `WOODPECKER_CONFIG_EXTENSION_ENDPOINT`
points at the backend's `/api/v1/ci/config` inside the network, and
`WOODPECKER_CONFIG_EXTENSION_EXCLUSIVE` means Woodpecker asks it for every run
and never reads a pipeline file from a repository. `WOODPECKER_EXTENSIONS_ALLOWED_HOSTS`
is `private` because the backend is at a private address: with the default,
`external`, the call is refused and a dispatch answers 204 with no pipeline
rather than an error. The proxy answers 404 for the extension's path from
outside.

**`WOODPECKER_PLUGINS_TRUSTED_CLONE`.** The platform's clone image, by the
digest in `UNICON_CLONE_IMAGE`, and nothing else. A clone step whose image is
on this list gets the repository credential even though task repositories are
not trusted with it, and no other step gets it. Woodpecker compares an entry
that carries a digest with the whole reference, so only that build of the
clone image is lent the credential. Setting the variable replaces
Woodpecker's own list, so its git plugin is lent nothing; no grading run uses
it, since the extension's answer names the clone image for both checkouts.

**`WOODPECKER_DEFAULT_PIPELINE_TIMEOUT: "30"`.** Every task repository gets
this timeout when the forge activates it, and the forge's grading clock is
built on it: `RUN_TIMEOUT` is 30 minutes, of which the checkouts are allowed
4 and the reports 1, so a harness's wall time is never more than 25. A change
here goes with a change there.

**Which host the credential is lent for.** A clone step on the trusted list
gets `CI_NETRC_MACHINE`, `CI_NETRC_USERNAME` and `CI_NETRC_PASSWORD`, and the
clone image, which is Woodpecker's git plugin with one line of git
configuration added, writes them to `~/.netrc`. The machine is the host of the
repository's clone URL as Forgejo reports it, from its `ROOT_URL`, with the
port dropped; it is never `WOODPECKER_FORGEJO_URL`, and nothing in Woodpecker
3.18.1 overrides it. Git sends the credential only to that host, so a clone
step's remote has to keep it: pointing a remote at `http://forgejo:3000`
clones with no credential, and Forgejo's `REQUIRE_SIGNIN_VIEW` refuses that.
In production the host is the forge's public name. In development it is
`forge.localhost`, which curl sends to loopback inside a step container, so
`compose.dev.yaml` sets **`WOODPECKER_BACKEND_HTTP_PROXY`** to
`http://proxy:3128`, the proxy's development git listener, and
**`WOODPECKER_BACKEND_NO_PROXY`** to `proxy,host.docker.internal`, where
the harness reaches the platform. The server passes both to every
step as `http_proxy` and `no_proxy` (and their capitals), plugins included,
and an environment it adds does not stop a step counting as a plugin. A step
may not set `extra_hosts` or its network itself unless the repository is
trusted with the network, which task repositories are not. See the deploy
README, "The grading machine in development".

**`WOODPECKER_GRPC_SECRET` on the server, `WOODPECKER_AGENT_CONFIG_FILE` on the
agent.** The first signs the tokens the server issues to its agents; left unset
the server makes a new one at every start, says so in a warning, and every agent
has to register again. The second points the agent's saved identity at the
volume it already has, because the default path is in a directory this image
does not create. Together they mean a restart reconnects the agent that was
there rather than leaving a dead row behind and registering a new one.

**`WOODPECKER_OPEN: "false"` with `WOODPECKER_ADMIN: unicon-ci`.** Only that one
account can sign in. Woodpecker is not a place people log into; the backend talks
to it with the API token.

This directory exists so that is written down somewhere other than a comment.
