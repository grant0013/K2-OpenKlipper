"""Coalesced, selectable notifications for local renderer connections."""
import socket
import threading


class WakeChannel:
    def __init__(self):
        self.reader, self.writer = socket.socketpair()
        self.reader.setblocking(False)
        self.writer.setblocking(False)
        self.lock = threading.Lock()
        self.pending = False
        self.closed = False

    def signal(self):
        with self.lock:
            if self.pending or self.closed:
                return
            try:
                self.writer.send(b'\0')
                self.pending = True
            except (BlockingIOError, OSError):
                pass

    def drain(self):
        with self.lock:
            if self.closed:
                return
            while True:
                try:
                    if not self.reader.recv(4096):
                        break
                except (BlockingIOError, OSError):
                    break
            self.pending = False

    def close(self):
        with self.lock:
            self.closed = True
            self.reader.close()
            self.writer.close()
