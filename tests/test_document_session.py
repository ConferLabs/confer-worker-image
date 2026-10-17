import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SESSION_PATH = (
    REPOSITORY_ROOT
    / "mkosi.skeleton/usr/local/libexec/confer-document-session")


class DocumentSessionTest(unittest.TestCase):
  def run_session(self, socket_path: str, request: bytes) -> subprocess.CompletedProcess:
    source = SESSION_PATH.read_text(encoding="utf-8").replace(
        'SOCKET_PATH = "/run/confer-document/worker.sock"',
        f"SOCKET_PATH = {socket_path!r}")
    return subprocess.run(
        ["python3", "-I", "-c", source],
        input=request,
        capture_output=True,
        timeout=10,
        check=False)

  def test_relays_both_directions_until_the_worker_closes(self):
    with tempfile.TemporaryDirectory() as directory:
      socket_path = os.path.join(directory, "worker.sock")
      server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
      server.bind(socket_path)
      server.listen(1)
      received = bytearray()

      def serve() -> None:
        connection, _ = server.accept()
        with connection:
          while True:
            chunk = connection.recv(65536)
            if not chunk:
              break
            received.extend(chunk)
          connection.sendall(b"response:" + bytes(received[:5]) * 50000)

      worker = threading.Thread(target=serve)
      worker.start()
      request = os.urandom(3 * 1024 * 1024)
      completed = self.run_session(socket_path, request)
      worker.join(timeout=10)
      server.close()

    self.assertEqual(0, completed.returncode)
    self.assertEqual(request, bytes(received))
    self.assertEqual(b"response:" + request[:5] * 50000, completed.stdout)

  def test_fails_without_a_worker_socket(self):
    with tempfile.TemporaryDirectory() as directory:
      completed = self.run_session(os.path.join(directory, "missing.sock"), b"")

    self.assertEqual(1, completed.returncode)
    self.assertEqual(b"", completed.stdout)


if __name__ == "__main__":
  unittest.main()
