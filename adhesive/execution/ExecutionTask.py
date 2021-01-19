import logging
import re
from typing import Callable, Optional

from pathlib import Path

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

            # if Zeebe parent process (sub-process) loop...
            if hasattr(event.task.parent_process, "loop") and event.task.parent_process.loop is not None:
                # loop input mapping
                # capture loop current item in loop input element variable
                event.context.data[event.task.parent_process.loop.input_element] = event.context.loop.value

            # Zeebe task loop input mapping
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

            # inputs mapping
            if event.task.mapping:
                for input in event.task.mapping["inputs"]:
                    # remove zeebe equal
                    source = input["source"].strip("= ")
                    # replace variables by their values
                    for variable, value in event.context.data.as_dict().items():
                        if isinstance(value, str) or isinstance(value, int) or isinstance(value, float):
                            # replace variable occurrence by value if not in quotes
                            source = re.sub('{text}(?=([^"]*"[^"]*")*[^"]*$)'.format(text=variable),
                                            value if not isinstance(value, str) else f"\"{value}\"", source)
                    # parse source python expression and put result in target variable
                    eval_data = token_utils.get_eval_data(event.context)
                    try:
                        result = eval(source, {"Path": Path}, eval_data)
                    except Exception as exception:
                        logging.error(exception)
                        raise Exception(exception)
                    event.context.data[input["target"]] = result

            self.code(event.context, *params)  # type: ignore

            # outputs mapping
            if event.task.mapping:
                for output in event.task.mapping["outputs"]:
                    # remove zeebe equal
                    source = output["source"].strip("= ")
                    # replace variables by their values
                    for variable, value in event.context.data.as_dict().items():
                        if isinstance(value, str) or isinstance(value, int) or isinstance(value, float):
                            # replace variable occurrence by value if not in quotes
                            source = re.sub('{text}(?=([^"]*"[^"]*")*[^"]*$)'.format(text=variable),
                                            value if not isinstance(value, str) else f"\"{value}\"", source)
                    # parse source python expression and put result in target variable
                    eval_data = token_utils.get_eval_data(event.context)
                    try:
                        result = eval(source, {"Path": Path}, eval_data)
                    except Exception as exception:
                        logging.error(exception)
                        raise Exception(exception)
                    event.context.data[output["target"]] = result

            # Zeebe task loop output mapping
            if event.task.loop:
                # add loop output element variable value to loop output collection dict
                event.context.data[event.task.loop.output_collection][event.context.loop.index] = event.context.data[
                    event.task.loop.output_element]

            # Zeebe parent process (sub-process) loop
            if hasattr(event.task.parent_process, "loop") and event.task.parent_process.loop is not None:
                # output mapping
                # add loop output element variable value to loop output collection dict
                event.context.data[event.task.parent_process.loop.output_collection][event.context.loop.index] = event.context.data[
                    event.task.parent_process.loop.output_element]

            return event.context

    def __repr__(self) -> str:
        return f"@task(expressions={self.expressions}, code={self.code.__name__})"
