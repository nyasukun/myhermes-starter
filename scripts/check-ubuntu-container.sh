#!/bin/sh
# Explicit local-only Ubuntu acceptance. Never mount host directories or secrets.
set -eu

cd "$(dirname "$0")/.."
case "$(docker context inspect --format '{{(index .Endpoints "docker").Host}}')" in
  unix://*) ;;
  *) echo 'Refusing a non-local Docker endpoint.' >&2; exit 3 ;;
esac
if [ -n "${DOCKER_HOST:-}" ]; then
  case "$DOCKER_HOST" in unix://*) ;; *) echo 'Refusing a non-local DOCKER_HOST.' >&2; exit 3 ;; esac
fi

MYHERMES_CHECK_IMAGE='ubuntu@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254'
MYHERMES_CHECK_TEMP=$(mktemp -d)
MYHERMES_CHECK_CONTAINER=''
cleanup() {
  if [ -n "$MYHERMES_CHECK_CONTAINER" ]; then
    docker rm --force "$MYHERMES_CHECK_CONTAINER" >/dev/null 2>&1 || true
  fi
  rm -rf "$MYHERMES_CHECK_TEMP"
}
trap cleanup EXIT HUP INT TERM

# A fixed public-source allowlist, no repo root, .git, .venv, node_modules, private
# sibling repository, .env files, databases or native credential stores.
COPYFILE_DISABLE=1 tar --format=ustar --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='*.egg-info' --exclude='.env' --exclude='.env.*' \
  --exclude='*.sqlite*' --exclude='*.db*' --exclude='*.pem' \
  -cf "$MYHERMES_CHECK_TEMP/source.tar" pyproject.toml src tests scripts docs monitoring README.md .agents hermes-skills
docker pull "$MYHERMES_CHECK_IMAGE"
MYHERMES_CHECK_CONTAINER=$(docker run --detach --memory 3g --cpus 2 --pids-limit 256 \
  "$MYHERMES_CHECK_IMAGE" sleep 3600)
docker cp "$MYHERMES_CHECK_TEMP/source.tar" "$MYHERMES_CHECK_CONTAINER:/tmp/source.tar"
docker exec "$MYHERMES_CHECK_CONTAINER" sh -ec '
  apt-get update >/tmp/apt-update.log 2>&1
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git ca-certificates dbus-x11 gnome-keyring libsecret-tools >/tmp/apt-install.log 2>&1
  useradd --create-home --uid 10001 myhermes-test
  mkdir /tmp/myhermes-public
  tar -xf /tmp/source.tar -C /tmp/myhermes-public
  rm /tmp/source.tar
  chown -R 10001:10001 /tmp/myhermes-public
  cat /etc/os-release
  python3 --version
'
docker exec --user 10001:10001 --workdir /tmp/myhermes-public "$MYHERMES_CHECK_CONTAINER" sh -ec '
  python3 -m venv /tmp/myhermes-venv
  /tmp/myhermes-venv/bin/python -m pip install -e ".[dev]" >/tmp/myhermes-pip.log 2>&1
  /tmp/myhermes-venv/bin/python -m unittest discover -s tests -v
  /tmp/myhermes-venv/bin/ruff check src tests
'
docker exec --user 10001:10001 --workdir /tmp/myhermes-public "$MYHERMES_CHECK_CONTAINER" dbus-run-session -- sh -ec '
  python3 -c "import secrets,sys;sys.stdout.write(secrets.token_urlsafe(32))" | \
    gnome-keyring-daemon --unlock --components=secrets >/tmp/myhermes-keyring-start.log 2>&1
  MYHERMES_NATIVE_KEYRING=1 /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_native_keyring.py -v
'

# Opt-in because the official upstream dependency install is substantially
# larger. It never authorizes a real provider call or copies an API key.
if [ "${MYHERMES_CHECK_UPSTREAM:-0}" = '1' ]; then
  docker exec --user 10001:10001 --workdir /tmp/myhermes-public "$MYHERMES_CHECK_CONTAINER" sh -ec '
    /tmp/myhermes-venv/bin/myhermes --state-dir /home/myhermes-test/myhermes-install-state setup \
      --server https://example.invalid --hermes-home /home/myhermes-test/myhermes-install-home \
      --upstream /home/myhermes-test/hermes-agent --label "Synthetic Ubuntu installation check"
    /tmp/myhermes-venv/bin/myhermes --state-dir /home/myhermes-test/myhermes-install-state install-runtime --python python3
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_runtime_relay.py -v
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_runtime_environment.py -v
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_runtime_auxiliary.py -v
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_runtime_credential_scrub.py -v
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_runtime_terminal.py -v
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_runtime_signals.py -v
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_skill_frontmatter.py -v
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_hermes_oneshot.py -v
    MYHERMES_TEST_UPSTREAM=/home/myhermes-test/hermes-agent /tmp/myhermes-venv/bin/python -m unittest discover -s tests -p test_hermes_memory_sync.py -v
  '
fi
