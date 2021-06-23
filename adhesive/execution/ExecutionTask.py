import copy
import logging
import re
from typing import Callable, Optional

from pathlib import Path

from adhesive.execution import token_utils
from adhesive.execution.ExecutionBaseTask import (
    ExecutionBaseTask,
    ExpressionList,
    RegexList,
)
from adhesive.logredirect.LogRedirect import redirect_stdout
from adhesive.model.ActiveEvent import ActiveEvent
from .ExecutionData import ExecutionData
from .ExecutionToken import ExecutionToken


class ExecutionTask(ExecutionBaseTask):
    """
    A task implementation.
    """

    def __init__(
        self,
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

    def invoke(self, event: ActiveEvent) -> ExecutionToken:
        with redirect_stdout(event):

            # if Zeebe parent process (sub-process) has a loop and this task has not a loop...
            if hasattr(event.task.parent_process, "loop")\
                    and event.task.parent_process.loop is not None:
                # if input_element variable exists...
                if event.task.parent_process.loop.input_element not in event.context.data.as_dict():
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

            # Zeebe: add an event.task.input and event.task.output to use the I/O mapping between
            # context.data and the task local scope. Input mapping targets should only be visible to the task,
            # and output_data filtered by output mapping is added to context.data after the task. This is to avoid
            # global variable conflicts.

            # copy global variables in local data input
            event.task.input = copy.deepcopy(event.context.data)
            # create output local variable store
            event.task.output = ExecutionData()

            # inputs mapping
            if event.task.mapping:
                for input in event.task.mapping["inputs"]:
                    # remove zeebe equal
                    source = input["source"].strip("= ")
                    # parse source python expression and put result in target variable
                    eval_data = token_utils.get_eval_data(event.context)
                    event.task.input[input["target"]] = evaluate_expression(source, eval_data)

            self.code(event.context, *params)  # type: ignore

            # add local output variables to context data
            event.context.data = ExecutionData.merge(event.context.data, event.task.output)

            # outputs mapping
            if event.task.mapping:
                for output in event.task.mapping["outputs"]:
                    # remove zeebe equal
                    source = output["source"].strip("= ")
                    # parse source python expression and put result in target variable
                    eval_data = token_utils.get_eval_data(event.context)
                    event.context.data[output["target"]] = evaluate_expression(source, eval_data)

            # Zeebe task loop output mapping
            if event.task.loop and isinstance(event.context.data[event.task.loop.output_collection], dict):
                # parse source python expression and put result in target variable
                eval_data = token_utils.get_eval_data(event.context)
                # add loop output element variable value to loop output collection dict
                event.context.data[event.task.loop.output_collection][event.context.loop.index] = evaluate_expression(event.task.loop.output_element, eval_data)

            # if Zeebe parent process (sub-process) loop and output_collection is a dict and output_element variable
            # exist...
            if hasattr(event.task.parent_process, "loop")\
                    and event.task.parent_process.loop is not None\
                    and isinstance(event.context.data[event.task.parent_process.loop.output_collection], dict)\
                    and event.task.parent_process.loop.output_element in event.context.data.as_dict():

                # output mapping
                # add loop output element variable value to loop output collection dict
                event.context.data[event.task.parent_process.loop.output_collection][event.context.loop.index] = event.context.data[
                    event.task.parent_process.loop.output_element]

            return event.context

    def __repr__(self) -> str:
        return f"@task(expressions={self.expressions}, code={self.code.__name__})"


def evaluate_expression(expression: str, data: dict):
    """
    Isolate eval command in a function to avoid variable conflicts
    Return result of expression

    :param expression: Expression to evaluate
    :param data: variable which can be referenced in the expression
    :return:
    """
    try:
        result = eval(expression, {"Path": Path}, data)
    except Exception as exception:
        logging.error(exception)
        raise Exception(exception)
    return result
