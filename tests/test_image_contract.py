import json
import os
from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def read(path: str) -> str:
  return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def directives(path: str) -> list[str]:
  return [
      line.strip()
      for line in read(path).splitlines()
      if line.strip() and not line.lstrip().startswith("#")
  ]


class ImageContractTest(unittest.TestCase):
  def test_worker_is_one_shot_attested_ssh(self):
    service = set(directives(
        "mkosi.skeleton/etc/systemd/system/confer-worker.service"))
    self.assertTrue({
        "ExecStart=/usr/local/libexec/confer-worker-listener",
        "ExecStopPost=-/bin/rm -f /run/confer/ready",
        "Restart=no",
        "RefuseManualStart=yes",
        "SuccessAction=poweroff-force",
        "FailureAction=poweroff-force",
        "KillMode=control-group",
        "NoNewPrivileges=yes",
        "AmbientCapabilities=CAP_SETUID",
        "ProtectSystem=strict",
        "ProtectProc=invisible",
        "ReadWritePaths=/run/confer /var/lib/confer/workspace /sys/kernel/config/tsm/report /sys/devices/virtual/misc/tdx_guest/measurements/rtmr3:sha384",
    }.issubset(service))

    ssh = set(directives("mkosi.skeleton/etc/ssh/sshd_config"))
    self.assertTrue({
        "HostKey /run/confer/ssh_host_ed25519_key",
        "AuthorizedKeysFile /run/confer/authorized_keys",
        "AuthenticationMethods publickey",
        "AllowUsers confer-job",
        "DisableForwarding yes",
        "MaxSessions 32",
        "PermitTTY no",
        "UsePAM no",
    }.issubset(ssh))

  def test_health_endpoint_is_separate_and_bounded(self):
    socket_unit = set(directives(
        "mkosi.skeleton/etc/systemd/system/confer-worker-health.socket"))
    service = set(directives(
        "mkosi.skeleton/etc/systemd/system/confer-worker-health@.service"))
    self.assertTrue({
        "ListenStream=8080",
        "Accept=yes",
        "MaxConnections=64",
    }.issubset(socket_unit))
    self.assertTrue({
        "DynamicUser=yes",
        "StandardInput=socket",
        "StandardOutput=socket",
        "RuntimeMaxSec=5s",
    }.issubset(service))

  def test_document_worker_is_sandboxed_per_connection(self):
    socket_unit = set(directives(
        "mkosi.skeleton/etc/systemd/system/confer-document-worker.socket"))
    service = set(directives(
        "mkosi.skeleton/etc/systemd/system/confer-document-worker@.service"))
    slice_unit = set(directives(
        "mkosi.skeleton/etc/systemd/system/confer-document.slice"))
    self.assertTrue({
        "ListenStream=/run/confer-document/worker.sock",
        "Accept=yes",
        "MaxConnections=32",
        "SocketUser=confer-job",
        "SocketMode=0600",
    }.issubset(socket_unit))
    self.assertTrue({
        "Slice=confer-document.slice",
        "Environment=OMP_THREAD_LIMIT=1",
        "DynamicUser=yes",
        "StandardInput=socket",
        "StandardOutput=socket",
        "PrivateNetwork=yes",
        "IPAddressDeny=any",
        "RestrictAddressFamilies=none",
        "InaccessiblePaths=/mnt /run/confer /var/lib/confer",
        "MemoryMax=8G",
        "RuntimeMaxSec=5min",
        "ExecStart=/usr/bin/python3 -S -m confer_document_worker.main",
    }.issubset(service))
    self.assertTrue({"MemorySwapMax=0"}.issubset(slice_unit))
    self.assertTrue(any(line.startswith("MemoryMax=") for line in slice_unit))

    postinst = read("mkosi.postinst.chroot")
    self.assertIn('--requirement "$SRCDIR/document-worker/requirements-runtime.txt"', postinst)
    self.assertIn("confer_document_worker.main --check", postinst)
    self.assertIn("systemctl enable confer-document-worker.socket", postinst)

  def test_job_network_policy_rejects_blocked_connections(self):
    policy = set(directives(
        "mkosi.extra/etc/nftables.conf"))
    self.assertTrue({
        "policy drop;",
        "tcp dport { 22, 8080 } accept",
        "ip daddr 169.254.169.254 meta skuid confer-job reject with icmpx type admin-prohibited",
        "tcp dport 443 meta skuid confer-job accept",
        "meta skuid confer-job meta l4proto tcp reject with tcp reset",
        "meta skuid confer-job reject with icmpx type admin-prohibited",
    }.issubset(policy))

  def test_document_environment_is_pinned(self):
    image = set(directives("mkosi.conf"))
    packages = {
        "python3-docx",
        "python3-openpyxl",
        "python3-xlsxwriter",
        "python3-reportlab",
        "python3-pil",
        "python3-pypdf",
        "poppler-utils",
        "tesseract-ocr",
        "tesseract-ocr-eng",
        "fonts-dejavu-core",
        "fonts-liberation",
        "fonts-noto-core",
    }
    self.assertTrue(packages.issubset(image))
    self.assertEqual(
        directives("mkosi.skeleton/etc/pip.conf"),
        [
            "[global]",
            "index-url = https://pypi.org/simple/",
            "disable-pip-version-check = true",
            "no-input = true",
        ])
    requirements = read(
        "mkosi.skeleton/usr/local/share/confer/document-core-requirements.txt")
    self.assertIn("python-pptx==1.0.2", requirements)
    self.assertIn("--hash=sha256:", requirements)

  def test_image_inputs_are_reproducible(self):
    image = set(directives("mkosi.conf"))
    self.assertTrue({
        "Mirror=https://snapshot.ubuntu.com/ubuntu/20261022T000000Z",
        "SourceDateEpoch=0",
        "Seed=01379291-3258-40cc-bb5f-57a7a9c4db2e",
        "OutputDirectory=mkosi.output",
        "Bootloader=uki",
        "Format=disk",
    }.issubset(image))
    self.assertEqual(
        directives("mkosi.repart/10-root.conf"),
        [
            "[Partition]",
            "Type=root",
            "Label=root",
            "Format=squashfs",
            "CopyFiles=/",
            "Minimize=guess",
            "Verity=data",
            "VerityMatchKey=root",
        ])
    sources = read("mkosi.skeleton/etc/apt/sources.list").splitlines()
    self.assertEqual(len(sources), 3)
    for source in sources:
      self.assertIn("https://snapshot.ubuntu.com/ubuntu/20261022T000000Z/", source)
      self.assertIn("signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg", source)

  def test_runtime_state_is_ephemeral(self):
    image = set(directives("mkosi.conf"))
    self.assertTrue({
        "RemoveFiles=/root/.cache",
        "RemoveFiles=/.cache",
        "RemoveFiles=/tmp/*",
    }.issubset(image))
    self.assertEqual(
        read("mkosi.extra/etc/resolv.conf"),
        "nameserver 169.254.169.254\n")
    self.assertEqual(
        directives("mkosi.skeleton/etc/fstab"),
        [
            "tmpfs  /tmp                    tmpfs  mode=1777,strictatime,nosuid,nodev,size=2G  0 0",
            "tmpfs  /var/log                tmpfs  mode=0755,strictatime,nosuid,nodev,size=256M  0 0",
            "tmpfs  /var/lib/confer         tmpfs  mode=0711,strictatime,nosuid,nodev,size=8G  0 0",
        ])

  def test_guest_programs_are_executable(self):
    for path in [
        "mkosi.skeleton/usr/local/libexec/confer-worker-config",
        "mkosi.skeleton/usr/local/libexec/confer-worker-health",
        "mkosi.skeleton/usr/local/libexec/confer-worker-listener",
    ]:
      self.assertTrue(os.access(REPOSITORY_ROOT / path, os.X_OK), path)

  def test_release_profile_is_replayable(self):
    profile = json.loads(read("release/gcp-tdx-platform-profile.json"))
    self.assertEqual(profile["version"], 1)
    self.assertEqual(profile["rtmr0Fixture"]["platform"], {
        "provider": "GCP",
        "region": "us-east4",
        "machineType": "c3-standard-8",
        "confidentialInstanceType": "TDX",
        "shieldedInstanceConfig": {
            "secureBoot": False,
            "virtualTpm": True,
            "integrityMonitoring": True,
        },
    })
    self.assertEqual(len(profile["rtmr0Fixture"]["events"]), 14)

  def test_release_workflow_signs_and_publishes_immutably(self):
    workflow = read(".github/workflows/release.yml")
    self.assertIn("workflow_dispatch:", workflow)
    self.assertIn("https://fulcio.sigstage.dev", workflow)
    self.assertIn("https://fulcio.sigstore.dev", workflow)
    self.assertIn(
        "worker-releases@conferlabs.iam.gserviceaccount.com",
        workflow)
    self.assertIn("cosign sign-blob", workflow)
    self.assertIn("x-goog-if-generation-match: 0", workflow)
    self.assertIn("nix develop --command make prepare-release", workflow)
    self.assertGreater(
        workflow.rfind("upload SHA256SUMS text/plain"),
        workflow.rfind("upload manifest.bundle.json application/json"))


if __name__ == "__main__":
  unittest.main()
