import subprocess
from contextlib import contextmanager
from typing import Optional

from .Workspace import Workspace


class DockerWorkspace(Workspace):
    def __init__(self,
                 image_name: str,
                 pwd: str) -> None:
        super(DockerWorkspace, self).__init__(pwd)
        self.container_id = subprocess.check_output([
            "docker", "run", "-t", "-v", f"{pwd}:{pwd}", "-d", "--entrypoint", "cat", image_name
        ]).decode('utf-8').strip()

    def run(self, command: str) -> None:
        subprocess.check_call([
            "docker", "exec", "-w", self.pwd, self.container_id, "/bin/sh", "-c", command
        ], cwd=self.pwd)

    def write_file(
            self,
            file_name: str,
            content: str) -> None:
        raise Exception("not implemented")

    def rm(self, path: Optional[str]=None) -> None:
        raise Exception("not implemented")

    def mkdir(self, path: str=None) -> None:
        raise Exception("not implemented")

    def _destroy(self):
        subprocess.check_call([
            "docker", "rm", "-f", self.container_id
        ])


@contextmanager
def inside(context,
           image_name: str):
    w = None

    try:
        w = DockerWorkspace(image_name, pwd=context.workspace.pwd)
        yield w
    finally:
        if w is not None:
            w._destroy()
