"""Linux process locks; lock files must stay in place to avoid inode races."""

import fcntl
from pathlib import Path


class BusyError(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.file = self.path.open("a+b")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.file.close()
            self.file = None
            raise BusyError(
                "Another operation is running; retry when it finishes"
            ) from exc
        return self

    def release(self):
        if self.file is not None:
            self.file.close()
            self.file = None

    def __exit__(self, *args):
        self.release()
