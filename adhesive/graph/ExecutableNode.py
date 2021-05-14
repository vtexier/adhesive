from typing import Optional

from adhesive.execution.ExecutionData import ExecutionData
from adhesive.graph.ProcessNode import ProcessNode


class ExecutableNode(ProcessNode):
    def __init__(self, *args, id: str, name: str, parent_process: Optional['adhesive.graph.Process.Process']):
        """
        Init ExecutableNode

        :param args:
        :param id:
        :param name:
        :param parent_process:
        """
        super().__init__(*args, id=id, name=name, parent_process=parent_process)

        self.loop = None
        self.mapping = None
        self.headers = {}
        self.input = ExecutionData()
        self.output = ExecutionData()
