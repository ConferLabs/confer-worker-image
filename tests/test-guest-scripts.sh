#!/bin/bash
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(/usr/bin/mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  /usr/bin/printf '%s\n' "$1" >&2
  exit 1
}

assert_rejected() {
  if "$@" >/dev/null 2>&1; then
    fail "unexpectedly accepted: $*"
  fi
}

get_mode() {
  if /usr/bin/stat -c '%a' "$1" >/dev/null 2>&1; then
    /usr/bin/stat -c '%a' "$1"
  else
    /usr/bin/stat -f '%Lp' "$1"
  fi
}

source "$repository_root/mkosi.skeleton/usr/local/libexec/confer-worker-config"

require_instance_id 18446744073709551615

assert_rejected require_instance_id ''
assert_rejected require_instance_id worker-42
assert_rejected require_instance_id -1

runtime_directory=$test_root/runtime
/bin/mkdir -p "$runtime_directory"
get_metadata() {
  case "$1" in
    instance/id)
      /usr/bin/printf '%s' 42
      ;;
    *)
      return 1
      ;;
  esac
}
release_manifest_source=$test_root/release-manifest-source.json
release_bundle_source=$test_root/release-manifest-bundle-source.json
/usr/bin/printf '{"release":"manifest"}\n\n' > "$release_manifest_source"
/usr/bin/printf '{"release":"bundle"}\n' > "$release_bundle_source"
get_metadata_file() {
  case "$1" in
    instance/attributes/confer-worker-release-manifest)
      /bin/cp "$release_manifest_source" "$2"
      ;;
    instance/attributes/confer-worker-release-manifest-bundle)
      /bin/cp "$release_bundle_source" "$2"
      ;;
    *)
      return 1
      ;;
  esac
}

(configure_worker "$runtime_directory")

/usr/bin/printf '42\n' > "$test_root/expected-instance-id"
/usr/bin/cmp "$test_root/expected-instance-id" "$runtime_directory/instance-id"
[[ "$(get_mode "$runtime_directory/ssh_host_ed25519_key")" == 600 ]]
[[ "$(get_mode "$runtime_directory/ssh_host_ed25519_key.pub")" == 644 ]]
[[ "$(get_mode "$runtime_directory/instance-id")" == 444 ]]
[[ "$(get_mode "$runtime_directory/release-manifest.json")" == 444 ]]
[[ "$(get_mode "$runtime_directory/release-manifest.bundle.json")" == 444 ]]
/usr/bin/cmp \
  "$release_manifest_source" \
  "$runtime_directory/release-manifest.json"
/usr/bin/cmp \
  "$release_bundle_source" \
  "$runtime_directory/release-manifest.bundle.json"
[[ ! -e "$runtime_directory/pip.conf" ]]
[[ ! -e "$runtime_directory/python-index-url" ]]
[[ ! -e "$runtime_directory/hosts" ]]

invalid_runtime=$test_root/invalid-runtime
/bin/mkdir "$invalid_runtime"
invalid_evidence=$test_root/invalid-evidence
get_metadata_file() {
  /bin/cp "$invalid_evidence" "$2"
}
: > "$invalid_evidence"
assert_rejected fetch_release_evidence \
  "$invalid_runtime" evidence empty.json
/usr/bin/python3 -c \
  'from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b"a" * (32 * 1024 + 1))' \
  "$invalid_evidence"
assert_rejected fetch_release_evidence \
  "$invalid_runtime" evidence oversized.json
/usr/bin/printf '\377' > "$invalid_evidence"
assert_rejected fetch_release_evidence \
  "$invalid_runtime" evidence invalid-utf8.json

source "$repository_root/mkosi.skeleton/usr/local/libexec/confer-worker-health"

/usr/bin/printf \
  'GET / HTTP/1.1\r\nHost: worker\r\n\r\n' \
  | respond_to_health_request "$runtime_directory/ready" \
  > "$test_root/unready-response"
/usr/bin/printf \
  'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n' \
  > "$test_root/expected-unready-response"
/usr/bin/cmp \
  "$test_root/expected-unready-response" \
  "$test_root/unready-response"

/usr/bin/touch "$runtime_directory/ready"
/usr/bin/printf \
  'GET / HTTP/1.1\r\nHost: worker\r\n\r\n' \
  | respond_to_health_request "$runtime_directory/ready" \
  > "$test_root/ready-response"
/usr/bin/printf \
  'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 3\r\nContent-Type: text/plain\r\n\r\nok\n' \
  > "$test_root/expected-ready-response"
/usr/bin/cmp "$test_root/expected-ready-response" "$test_root/ready-response"
