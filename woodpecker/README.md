# Woodpecker

No configuration files. Woodpecker is configured entirely by environment
variables, which are in `compose.yaml` next to the service, and by the API token
that `bootstrap` mints.

Four things in those service definitions are load-bearing and easy to undo by
accident.

**`WOODPECKER_DATABASE_DRIVER: postgres`.** The default is SQLite, which loses
pipelines under concurrent dispatch. Never change it back.

**`WOODPECKER_EXPERT_FORGE_OAUTH_HOST`.** Woodpecker makes its API calls to
Forgejo over the compose network but has to send a browser to the public Forgejo
URL. Without this variable the sign-in redirect points at `http://forgejo:3000`,
which resolves nowhere outside Docker. The catch is that Woodpecker uses this
same host for the server-side token request, so the public URL has to be
reachable from inside the Woodpecker container as well. On a real deployment,
where Forgejo has a name in DNS, it already is; in development
`compose.dev.yaml` adds a `localhost` host entry pointing at the Docker host so
that `http://localhost:3300` means Forgejo from inside the container too.

**No `WOODPECKER_AGENT_SECRET` on the server.** With no shared secret an
agent connects only with a token the server issued for it, so a machine
becomes an agent by enrolment and nothing else. No agent runs in
`compose.yaml`; `compose.dev.yaml` runs one on the laptop under
`--profile agent` with `WOODPECKER_DEV_AGENT_TOKEN`, a token from the Agents
page.

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
