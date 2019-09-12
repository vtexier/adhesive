import uuid
from typing import Callable, Any, Optional

from adhesive.graph.BaseTask import BaseTask
#from adhesive.model.ActiveEvent import ActiveEvent  # FIXME move from here
# references to the ActiveEvent
from adhesive.execution import token_utils


class ExecutionLoop:
    """
    Holds the current looping information.
    """
    def __init__(self,
                 loop_id: str,
                 parent_loop: Optional['ExecutionLoop'],
                 task: BaseTask,
                 item: Any,
                 index: int) -> None:
        self.loop_id = loop_id
        self._task = task
        self._key = item
        self._value = item
        self._index = index
        self.parent_loop = parent_loop

    @property
    def task(self) -> BaseTask:
        return self._task

    @property
    def key(self) -> Any:
        return self._key

    @property
    def item(self) -> Any:
        return self._value

    @property
    def value(self) -> Any:
        return self._value

    @property
    def index(self) -> int:
        return self._index

    @staticmethod
    def create_loop(event: 'ActiveEvent',
                    clone_event: Callable[['ActiveEvent', 'BaseTask'], 'ActiveEvent']) -> int:
        expression = event.task.loop.loop_expression

        # FIXME: I'm not sure why this is here and not in the model, since it has nothing
        # to do with what's being exposed.

        eval_data = token_utils.get_eval_data(event.context)
        result = eval(expression, {}, eval_data)

        if not result:
            return 0

        index = 0
        for item in result:
            new_event = clone_event(event, event.task)
            loop_id = str(uuid.uuid4())

            parent_loop = new_event.context.loop
            new_event.context.loop = ExecutionLoop(
                loop_id,
                parent_loop,
                event.task,
                item,
                index)

            # if we're iterating over a map, we're going to store the
            # values as well.
            if isinstance(result, dict):
                new_event.context.loop._value = result[item]

            # FIXME: this knows way too much about how the ExecutionTokens are
            # supposed to function
            # FIXME: rename all event.contexts to event.token. Context is only
            # true in the scope of an execution task.
            new_event.context.task_name = token_utils.parse_name(
                    new_event.context,
                    new_event.context.task.name)

            index += 1

        return index


def parent_loop_id(e: 'ActiveEvent') -> Optional[str]:
    if not e.context.loop:
        return None

    if not e.context.loop.parent_loop:
        return None

    return e.context.loop.parent_loop.loop_id


def loop_id(e: 'ActiveEvent') -> Optional[str]:
    if not e.context.loop:
        return None

    return e.context.loop.loop_id