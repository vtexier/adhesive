import logging
import os
import shutil
import subprocess
from distutils.dir_util import copy_tree
from typing import Optional

from adhesive.steps.Execution import Execution
from adhesive.workspace.Workspace import Workspace

LOG = logging.getLogger(__name__)


class LocalLinuxWorkspace(Workspace):
    """
    A workspace is a place where work can be done. That means a writable
    folder is being allocated, that will be cleaned up at the end of the
    execution.
    """
    def __init__(self,
                 execution: Execution,
                 pwd: Optional[str]=None,
                 id: Optional[str] = None) -> None:
        super(LocalLinuxWorkspace, self).__init__(
            execution=execution,
            pwd=pwd,
            id=id,
        )

        if not pwd:
            self.pwd = ensure_folder(self)

    def run(self, command: str) -> None:
        subprocess.check_call([
            "/bin/sh", "-c", command
        ], cwd=self.pwd)

    def write_file(
            self,
            file_name: str,
            content: str) -> None:

        full_path = os.path.join(self.pwd, file_name)

        with open(full_path, "wt") as f:
            f.write(content)

    def rm(self, path: Optional[str]=None) -> None:
        if path is None:
            LOG.debug("rmtree {}", self.pwd)
            shutil.rmtree(self.pwd)
            return

        if not path:
            raise Exception("You need to pass a subpath to delete")

        remove_path = os.path.join(self.pwd, path)

        LOG.debug("rmtree {}", remove_path)

        if os.path.isfile(remove_path):
            os.remove(remove_path)
        else:
            shutil.rmtree(remove_path)

    def mkdir(self, path: str=None) -> None:
        LOG.debug("mkdir {}", path)
        os.mkdir(os.path.join(self.pwd, path))

    def copy_to_agent(self,
                      from_path: str,
                      to_path: str):
        LOG.debug("copy {} to {}", from_path, to_path)
        copy_tree(from_path, to_path)

    def copy_from_agent(self,
                        from_path: str,
                        to_path: str):
        LOG.debug("copy {} to {}", from_path, to_path)
        shutil.copytree(from_path, to_path)


from adhesive.storage.ensure_folder import ensure_folder
