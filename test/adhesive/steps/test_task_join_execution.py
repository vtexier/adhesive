import unittest

from adhesive.model.WorkflowExecutor import WorkflowExecutor
from adhesive.xml.bpmn import read_bpmn_file

from .test_tasks import adhesive, _async


class TestTaskJoinExecution(unittest.TestCase):
    """
    Test if the workflow executor can process exclusive gateways.
    """
    def test_task_join_execution(self):
        """
        Load a workflow with a gateway and test it..
        """
        adhesive.process.workflow = read_bpmn_file("test/adhesive/xml/task-join.bpmn")

        workflow_executor = WorkflowExecutor(adhesive.process)
        data = _async(workflow_executor.execute())

        self.assertEqual({
            'Build Germanium Image': [1],
            'Test Chrome': [1],
            'Test Firefox': [1, 1]
        }, data.steps)
        self.assertFalse(workflow_executor.events,
                         "Some events were not unregistered and/or executed.")


if __name__ == '__main__':
    unittest.main()