import logging
from typing import Callable, Optional

from adhesive.execution import token_utils
from adhesive.execution.ExecutionBaseTask import ExecutionBaseTask, ExpressionList, RegexList
from adhesive.logredirect.LogRedirect import redirect_stdout
from adhesive.model.ActiveEvent import ActiveEvent
from .ExecutionToken import ExecutionToken


class ExecutionTask(ExecutionBaseTask):
    """
    A task implementation.
    """
    def __init__(self,
                 *args,
                 code: Callable,
                 expressions: ExpressionList,
                 regex_expressions: RegexList,
                 loop: Optional[str] = None,
                 when: Optional[str] = None,
                 lane: Optional[str] = None,
                 deduplicate: Optional[str] = None,
             ) -> None:
        """
        Create a new adhesive task. The `loop`, `when` and `lane` are only
        available when doing a programmatic API.
        :param code:
        :param expressions:
        :param regex_expressions:
        :param loop:
        :param when:
        :param lane:
        """
        if args:
            raise Exception("You need to pass in the arguments by name")

        super(ExecutionTask, self).__init__(
            code=code,
            expressions=expressions,
            regex_expressions=regex_expressions,
            deduplicate=deduplicate,
        )

        self.loop = loop
        self.when = when
        self.lane = lane

    def invoke(
            self,
            event: ActiveEvent) -> ExecutionToken:
        with redirect_stdout(event):

            # loop input
            if event.task.loop:
                logging.debug(event.task.loop.__dict__)
                # capture loop current item in loop input element variable
                event.context.data[event.task.loop.input_element] = event.context.loop.value

            if hasattr(event.context.task, "type"):
                params = token_utils.matches(self.re_expressions,
                                             event.context.task.type)
            else:
                params = token_utils.matches(self.re_expressions,
                                         event.context.task_name)

            self.code(event.context, *params)  # type: ignore

            # loop output
            if event.context.loop:
                # add loop output element variable value to loop output collection dict
                event.context.data[event.task.loop.output_collection][event.context.loop.index] = event.context.data[event.task.loop.output_element]

            return event.context

    def __repr__(self) -> str:
        return f"@task(expressions={self.expressions}, code={self.code.__name__})"
