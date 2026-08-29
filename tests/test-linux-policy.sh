#!/bin/bash
set -euo pipefail

repository_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(mktemp -d /run/confer-worker-test.XXXXXX)
trap 'rm -rf "$test_root"' EXIT

if ((EUID != 0)); then
  printf 'test-linux-policy must run as root\n' >&2
  exit 1
fi
/bin/chmod 0755 "$test_root"
/usr/bin/install -d -m 0755 /run/sshd

assert_effective_setting() {
  if ! grep -Fqx -- "$1" "$test_root/sshd-effective"; then
    printf 'Missing effective sshd policy: %s\n' "$1" >&2
    exit 1
  fi
}

command -v sshd >/dev/null
command -v nft >/dev/null
getent passwd confer-job >/dev/null

ssh-keygen \
  -q \
  -t ed25519 \
  -N '' \
  -f "$test_root/ssh_host_ed25519_key"
ssh-keygen \
  -q \
  -t ed25519 \
  -N '' \
  -f "$test_root/ssh_client_ed25519_key"
printf 'restrict %s\n' \
  "$(ssh-keygen -y -f "$test_root/ssh_client_ed25519_key")" \
  > "$test_root/authorized_keys"
chmod 0444 "$test_root/authorized_keys"
sed \
  -e "s|^HostKey .*|HostKey $test_root/ssh_host_ed25519_key|" \
  -e "s|^AuthorizedKeysFile .*|AuthorizedKeysFile $test_root/authorized_keys|" \
  "$repository_root/mkosi.skeleton/etc/ssh/sshd_config" \
  > "$test_root/sshd_config"

sshd -t -f "$test_root/sshd_config"
sshd \
  -T \
  -f "$test_root/sshd_config" \
  -C user=confer-job,host=worker,addr=10.42.0.2 \
  > "$test_root/sshd-effective"

assert_effective_setting "allowusers confer-job"
assert_effective_setting "authenticationmethods publickey"
assert_effective_setting "authorizedkeysfile $test_root/authorized_keys"
assert_effective_setting "disableforwarding yes"
assert_effective_setting "kbdinteractiveauthentication no"
assert_effective_setting "maxauthtries 2"
assert_effective_setting "maxsessions 1"
assert_effective_setting "passwordauthentication no"
assert_effective_setting "permitrootlogin no"
assert_effective_setting "permittty no"
assert_effective_setting "permituserrc no"
assert_effective_setting "pubkeyauthentication yes"
assert_effective_setting "usepam no"

nft \
  --check \
  --file "$repository_root/mkosi.extra/etc/nftables.conf"

python3 \
  "$repository_root/tests/test_sshd_inetd.py" \
  "$test_root/sshd_config" \
  "$test_root/ssh_client_ed25519_key"
