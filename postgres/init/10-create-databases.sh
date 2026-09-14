#!/bin/sh
# One Postgres server, three databases, three roles: forgejo, woodpecker,
# unicon. Each role owns exactly its own database and can reach no other.
#
# Never one shared database. Forgejo runs its own migrations and already owns
# tables called user, team, org and session, and its state is only half in the
# database anyway. Unicon never reads Forgejo tables directly.
#
# The Postgres entrypoint runs this once, on the first start of an empty data
# directory. Changing a password in .env afterwards does not change it here;
# use ALTER ROLE, or throw the volume away.
set -eu

create_database() {
  role="$1"
  password="$2"
  if [ -z "$password" ]; then
    echo "10-create-databases.sh: no password given for role $role" >&2
    exit 1
  fi
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    --set "role=$role" --set "password=$password" <<'SQL'
CREATE ROLE :"role" LOGIN PASSWORD :'password';
-- LC_COLLATE C is what Forgejo asks for, and the same collation everywhere
-- keeps index behaviour identical across the three databases.
CREATE DATABASE :"role"
  OWNER :"role"
  ENCODING 'UTF8'
  LC_COLLATE 'C'
  LC_CTYPE 'C'
  TEMPLATE template0;
REVOKE ALL ON DATABASE :"role" FROM PUBLIC;
SQL
  echo "10-create-databases.sh: created database and role $role"
}

create_database forgejo "${FORGEJO_DB_PASSWORD:-}"
create_database woodpecker "${WOODPECKER_DB_PASSWORD:-}"
create_database unicon "${UNICON_DB_PASSWORD:-}"
