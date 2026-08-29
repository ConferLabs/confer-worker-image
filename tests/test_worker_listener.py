import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import call, Mock, patch


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
LISTENER_PATH = (
    REPOSITORY_ROOT
    / "mkosi.skeleton/usr/local/libexec/confer-worker-listener")
ED25519_KEY_PREFIX = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"


def load_listener():
  loader = importlib.machinery.SourceFileLoader(
      "confer_worker_listener_test",
      str(LISTENER_PATH))
  specification = importlib.util.spec_from_loader(loader.name, loader)
  if specification is None:
    raise AssertionError("Unable to load worker listener")
  module = importlib.util.module_from_spec(specification)
  loader.exec_module(module)
  return module


def create_ed25519_key(seed: int = 0) -> bytes:
  return ED25519_KEY_PREFIX + bytes(
      (seed + index) % 256 for index in range(32))


def format_public_key(key: bytes) -> str:
  return "ssh-ed25519 " + base64.b64encode(key).decode("ascii")


def create_request(challenge: bytes = bytes(range(32)),
                   client_key: bytes = create_ed25519_key()) -> bytes:
  return json.dumps({
      "version": 4,
      "challenge": base64.urlsafe_b64encode(challenge).rstrip(b"=").decode(
          "ascii"),
      "clientKey": format_public_key(client_key),
  }, separators=(",", ":")).encode("utf-8")


def create_tcp_pair() -> tuple[socket.socket, socket.socket]:
  listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  listener.bind(("127.0.0.1", 0))
  listener.listen()
  client = socket.create_connection(listener.getsockname())
  server, _address = listener.accept()
  listener.close()
  return server, client


class WorkerListenerTest(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.listener = load_listener()

  def test_attestation_binds_both_ssh_keys_and_preserves_release_evidence(self):
    challenge = bytes(range(32))
    host_key = create_ed25519_key(10)
    client_key = create_ed25519_key(100)
    manifest = '{"artifactType":"confer-worker-image"}\n\n'
    bundle = '{"mediaType":"application/vnd.dev.sigstore.bundle+json"}\n'

    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      host_key_path = root / "host-key.pub"
      authorized_keys_path = root / "authorized_keys"
      manifest_path = root / "manifest.json"
      bundle_path = root / "bundle.json"
      host_key_path.write_text(
          f"{format_public_key(host_key)} ignored-comment\n",
          encoding="ascii")
      manifest_path.write_text(manifest, encoding="utf-8")
      bundle_path.write_text(bundle, encoding="utf-8")

      with (
          patch.object(self.listener, "HOST_KEY_PATH", host_key_path),
          patch.object(self.listener, "AUTHORIZED_KEYS_PATH", authorized_keys_path),
          patch.object(self.listener, "MANIFEST_PATH", manifest_path),
          patch.object(self.listener, "MANIFEST_BUNDLE_PATH", bundle_path),
          patch.object(
              self.listener,
              "create_quote",
              return_value=b"tdx-quote") as create_quote,
      ):
        previous_umask = os.umask(0o077)
        try:
          encoded = self.listener.create_attestation(
              create_request(challenge, client_key))
        finally:
          os.umask(previous_umask)

      document = json.loads(encoded)
      expected_report_data = hashlib.sha512(
          self.listener.REPORT_DOMAIN
          + host_key
          + client_key
          + challenge).digest()
      create_quote.assert_called_once_with(expected_report_data)
      self.assertEqual(
          authorized_keys_path.read_text(encoding="ascii"),
          f"restrict {format_public_key(client_key)}\n")
      self.assertEqual(stat.S_IMODE(authorized_keys_path.stat().st_mode), 0o444)
      self.assertEqual(document, {
          "version": 4,
          "platform": "TDX",
          "protocol": "SSH-2.0",
          "challenge": self.listener.encode_urlsafe(challenge),
          "hostKey": format_public_key(host_key),
          "clientKey": format_public_key(client_key),
          "quote": self.listener.encode_urlsafe(b"tdx-quote"),
          "manifest": manifest,
          "manifestBundle": bundle,
      })

  def test_attestation_request_is_strict_and_bounded(self):
    challenge = self.listener.encode_urlsafe(bytes(range(32)))
    key = format_public_key(create_ed25519_key())
    valid_fields = f'"challenge":"{challenge}","clientKey":"{key}"'
    invalid = [
        b"",
        b"[]",
        b"{",
        b"\xff",
        bytes(self.listener.MAX_REQUEST_BYTES + 1),
        f'{{"version":4,{valid_fields},"extra":true}}'.encode(),
        f'{{"version":4,"version":4,{valid_fields}}}'.encode(),
        f'{{"version":true,{valid_fields}}}'.encode(),
        f'{{"version":3,{valid_fields}}}'.encode(),
        create_request(bytes(31)),
        create_request(bytes(33)),
    ]
    for encoded in invalid:
      with self.subTest(encoded=encoded[:80]), self.assertRaises(
          self.listener.WorkerError):
        self.listener.parse_request(encoded)

  def test_keys_and_challenges_require_canonical_encodings(self):
    challenge = self.listener.encode_urlsafe(bytes(range(32)))
    key = base64.b64encode(create_ed25519_key()).decode("ascii")
    invalid_challenges = [
        challenge + "=",
        challenge[:-1],
        "+" + challenge[1:],
        "\N{SNOWMAN}" * len(challenge),
    ]
    invalid_keys = [
        f"ssh-rsa {key}",
        f"ssh-ed25519 {key}=",
        f"ssh-ed25519 {key} comment",
        "ssh-ed25519 not-base64!",
        7,
    ]
    for invalid in invalid_challenges:
      encoded = json.dumps({
          "version": 4,
          "challenge": invalid,
          "clientKey": f"ssh-ed25519 {key}",
      }).encode()
      with self.subTest(challenge=invalid), self.assertRaises(
          self.listener.WorkerError):
        self.listener.parse_request(encoded)
    for invalid in invalid_keys:
      encoded = json.dumps({
          "version": 4,
          "challenge": challenge,
          "clientKey": invalid,
      }).encode()
      with self.subTest(key=invalid), self.assertRaises(
          self.listener.WorkerError):
        self.listener.parse_request(encoded)

  def test_authorized_key_is_created_once(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "authorized_keys"
      self.listener.write_authorized_key(path, create_ed25519_key())
      self.assertEqual(
          path.read_text(encoding="ascii"),
          f"restrict {format_public_key(create_ed25519_key())}\n")
      with self.assertRaises(FileExistsError):
        self.listener.write_authorized_key(path, create_ed25519_key(1))

  def test_release_evidence_is_exact_utf8_and_bounded(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "manifest.json"
      expected = '{"release":"worker"}\n\n'
      path.write_text(expected, encoding="utf-8")
      self.assertEqual(self.listener.read_release_evidence(path), expected)
      for value in [
          b"",
          bytes(self.listener.MAX_RELEASE_EVIDENCE_BYTES + 1),
          b"\xff",
      ]:
        path.write_bytes(value)
        with self.subTest(size=len(value)), self.assertRaises(
            self.listener.WorkerError):
          self.listener.read_release_evidence(path)

  def test_quote_is_bounded_and_report_entry_is_removed(self):
    report_data = bytes(64)
    for quote in [b"quote", b"", bytes(self.listener.MAX_QUOTE_BYTES + 1)]:
      with self.subTest(size=len(quote)), tempfile.TemporaryDirectory() as directory:
        reports_path = Path(directory)

        with patch.object(
            self.listener.secrets,
            "token_hex",
            return_value="fixed"), patch.object(
                Path,
                "write_bytes",
                autospec=True,
                return_value=len(report_data)) as write_bytes, patch.object(
                    Path,
                    "read_bytes",
                    autospec=True,
                    return_value=quote) as read_bytes:
          if quote and len(quote) <= self.listener.MAX_QUOTE_BYTES:
            self.assertEqual(
                self.listener.create_quote(report_data, reports_path),
                quote)
          else:
            with self.assertRaises(self.listener.WorkerError):
              self.listener.create_quote(report_data, reports_path)
        entry = reports_path / "confer-fixed"
        write_bytes.assert_called_once_with(entry / "inblob", report_data)
        read_bytes.assert_called_once_with(entry / "outblob")
        self.assertEqual(list(reports_path.iterdir()), [])

  def test_instance_identity_and_admission_cutoff_are_bounded(self):
    self.assertEqual(
        self.listener.create_reservation_acknowledgement("42\n"),
        b"CONFER-WORKER-RESERVED 1 42\n")
    self.assertEqual(
        self.listener.create_reservation_acknowledgement("00042\n"),
        b"CONFER-WORKER-RESERVED 1 42\n")
    for value in ["", "worker-42", "-1", str(2**64)]:
      with self.subTest(value=value), self.assertRaises(
          self.listener.WorkerError):
        self.listener.create_reservation_acknowledgement(value)

    with patch.object(
        self.listener.secrets,
        "randbelow",
        return_value=17):
      expected = self.listener.ADMISSION_CUTOFF_EARLIEST_SECONDS + 17
      self.assertEqual(
          self.listener.create_admission_cutoff_boot_age(),
          expected)

  def test_listener_can_start_only_once_per_boot(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "listener-started"
      self.listener.claim_listener_start(path)
      self.assertEqual(path.read_bytes(), b"started\n")
      self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
      with self.assertRaisesRegex(
          self.listener.WorkerError,
          "Worker listener has already started"):
        self.listener.claim_listener_start(path)

    with patch.object(
        self.listener,
        "claim_listener_start",
        side_effect=self.listener.WorkerError("already started")), patch.object(
            self.listener.socket,
            "socket") as create_socket:
      with self.assertRaises(self.listener.WorkerError):
        self.listener.serve()
      create_socket.assert_not_called()

  def test_acknowledgement_and_attestation_frame_share_one_connection(self):
    server, client = create_tcp_pair()
    request_bytes = create_request()
    result = {}

    def receive():
      with server:
        self.listener.send_acknowledgement(server, b"reserved\n")
        result["request"] = self.listener.receive_attestation_request(server)

    thread = threading.Thread(target=receive)
    thread.start()
    self.assertEqual(client.recv(len(b"reserved\n")), b"reserved\n")
    client.sendall(struct.pack(">I", len(request_bytes)) + request_bytes)
    thread.join(timeout=2)
    client.close()
    self.assertFalse(thread.is_alive())
    self.assertEqual(result["request"], request_bytes)

  def test_malformed_partial_and_oversized_frames_are_rejected(self):
    cases = [
        struct.pack(">I", 0),
        struct.pack(">I", self.listener.MAX_REQUEST_BYTES + 1),
        struct.pack(">I", 12) + b"short",
        struct.pack(">I", 1) + b"\xff",
    ]
    for encoded in cases:
      server, client = create_tcp_pair()
      client.sendall(encoded)
      client.shutdown(socket.SHUT_WR)
      with self.subTest(encoded=encoded), server, client, self.assertRaises(
          self.listener.WorkerError):
        self.listener.receive_attestation_request(server)

  def test_admission_cutoff_withdraws_readiness(self):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    with tempfile.TemporaryDirectory() as directory:
      ready = Path(directory) / "ready"
      with self.assertRaises(self.listener.AdmissionClosed):
        self.listener.accept_until(
            listener,
            ready,
            time.monotonic() + 0.02)
      self.assertFalse(ready.exists())

  def test_accepted_connection_is_processed_after_admission_cutoff(self):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    client = socket.create_connection(listener.getsockname())
    monotonic = Mock(side_effect=[10.0, 20.0])
    with tempfile.TemporaryDirectory() as directory:
      ready = Path(directory) / "ready"
      connection = self.listener.accept_until(
          listener,
          ready,
          10.1,
          monotonic)
      with connection, client:
        client.sendall(b"accepted")
        self.assertEqual(connection.recv(8), b"accepted")
      self.assertFalse(ready.exists())
    monotonic.assert_called_once_with()

  def test_attestation_frame_is_followed_by_the_same_socket_stream(self):
    server, client = create_tcp_pair()
    attestation = b'{"version":4}'
    with server, client:
      self.listener.send_attestation(server, attestation)
      server.sendall(b"SSH-2.0-test\r\n")
      self.assertEqual(
          client.recv(4),
          struct.pack(">I", len(attestation)))
      self.assertEqual(client.recv(len(attestation)), attestation)
      self.assertEqual(client.recv(64), b"SSH-2.0-test\r\n")

  def test_sshd_replaces_the_listener_on_the_connected_socket(self):
    server, client = create_tcp_pair()
    descriptor = server.fileno()
    with patch.object(self.listener.os, "dup2") as duplicate, patch.object(
        self.listener.os,
        "execv",
        side_effect=OSError("expected test stop")) as execute:
      with self.assertRaisesRegex(OSError, "expected test stop"):
        self.listener.execute_sshd(server)
    client.close()
    self.assertEqual(
        duplicate.call_args_list,
        [
            call(descriptor, 0, inheritable=True),
            call(descriptor, 1, inheritable=True),
        ])
    execute.assert_called_once_with(
        self.listener.SSHD_PATH,
        [
            self.listener.SSHD_PATH,
            "-i",
            "-e",
            "-f",
            self.listener.SSHD_CONFIG,
        ])


if __name__ == "__main__":
  unittest.main()
