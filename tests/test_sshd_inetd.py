import socket
import subprocess
import sys
import threading


def main(arguments: list[str]) -> int:
  if len(arguments) != 2:
    return 2
  config, client_key = arguments
  listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  listener.bind(("127.0.0.1", 0))
  listener.listen(1)
  port = listener.getsockname()[1]
  server_result = {}

  def serve():
    connection, _address = listener.accept()
    listener.close()
    with connection:
      server = subprocess.Popen(
          ["/usr/sbin/sshd", "-i", "-e", "-f", config],
          stdin=connection,
          stdout=connection,
          stderr=subprocess.PIPE,
          text=True)
    try:
      _stdout, stderr = server.communicate(timeout=10)
    except subprocess.TimeoutExpired:
      server.kill()
      _stdout, stderr = server.communicate()
      server_result["error"] = "sshd timed out"
    server_result["stderr"] = stderr

  thread = threading.Thread(target=serve)
  thread.start()
  client = subprocess.run(
      [
          "/usr/bin/ssh",
          "-F", "/dev/null",
          "-i", client_key,
          "-p", str(port),
          "-o", "BatchMode=yes",
          "-o", "IdentitiesOnly=yes",
          "-o", "LogLevel=ERROR",
          "-o", "StrictHostKeyChecking=no",
          "-o", "UserKnownHostsFile=/dev/null",
          "confer-job@127.0.0.1",
          "/usr/bin/printf worker-ssh-ok",
      ],
      capture_output=True,
      text=True,
      timeout=10)
  thread.join(timeout=10)
  if thread.is_alive():
    raise RuntimeError("sshd did not exit")
  if client.returncode != 0:
    raise RuntimeError(
        f"ssh failed: {client.stderr.strip()}; "
        f"sshd: {server_result.get('stderr', '').strip()}")
  if client.stdout != "worker-ssh-ok":
    raise RuntimeError(f"unexpected SSH output: {client.stdout!r}")
  if "error" in server_result:
    raise RuntimeError(server_result["error"])
  return 0


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
