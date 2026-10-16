import os
import selectors
import socket
import sys

sock = socket.create_connection(("127.0.0.1", 18765), timeout=30)
sock.settimeout(None)
selector = selectors.DefaultSelector()
selector.register(sys.stdin.buffer, selectors.EVENT_READ)
selector.register(sock, selectors.EVENT_READ)
try:
    while True:
        for key, _ in selector.select():
            if key.fileobj is sock:
                data = sock.recv(65536)
                if not data:
                    raise SystemExit(0)
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
            else:
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    raise SystemExit(0)
                sock.sendall(data)
finally:
    sock.close()
