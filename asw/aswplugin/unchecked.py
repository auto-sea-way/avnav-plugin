"""Remembers which generated routes the sailor has not checked yet."""
import contextlib
import json
import os
import tempfile

from .route import unchecked_name


class Registry:
    def __init__(self, path):
        self.path = path
        self.routes, self.damaged = [], False
        try:
            with open(path) as f:
                data = json.load(f)
            self.routes = [{"name": e["name"], "key": e["key"]} for e in data["routes"]]
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError):
            self.routes, self.damaged = [], True
            with contextlib.suppress(OSError):
                os.replace(path, path + ".bad")

    def _save(self):
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"routes": self.routes}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def add(self, name, key):
        self.routes = [e for e in self.routes if e["name"] != name]
        self.routes.append({"name": name, "key": key})
        self._save()

    def remove(self, name):
        self.routes = [e for e in self.routes if e["name"] != name]
        self._save()

    def is_unchecked(self, name, key):
        if name.endswith(unchecked_name("")):
            return True
        return any(e["name"] == name or e["key"] == key for e in self.routes)

    def sync(self, routes):
        """routes maps route file name -> points key. Follow renames and edits."""
        changed = False
        for e in self.routes:
            if e["name"] in routes:
                if routes[e["name"]] != e["key"]:
                    e["key"] = routes[e["name"]]
                    changed = True
            else:
                new = next((n for n, k in routes.items() if k == e["key"]), None)
                if new:
                    e["name"] = new
                    changed = True
        if changed:
            self._save()
