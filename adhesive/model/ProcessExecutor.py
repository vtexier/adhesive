import logging
import copy
import os
import sys
import traceback
import uuid

from concurrent.futures import Future
from typing import Set, Optional, Dict, TypeVar, Any, List, Tuple

from adhesive import logredirect
from adhesive.consoleui.color_print import green, red, yellow, white
from adhesive.graph.BoundaryEvent import BoundaryEvent
from adhesive.graph.Gateway import Gateway, NonWaitingGateway, WaitingGateway
from adhesive.graph.ScriptTask import ScriptTask
from adhesive.graph.Task import Task
from adhesive.graph.UserTask import UserTask
from adhesive.model.ActiveEventStateMachine import ActiveEventState
from adhesive.model.GatewayController import GatewayController
from adhesive.model.ProcessExecutorConfig import ProcessExecutorConfig
from adhesive.model.generate_methods import display_unmatched_tasks
from adhesive.steps.AdhesiveBaseTask import AdhesiveBaseTask
from adhesive.steps.ExecutionToken import ExecutionToken
from adhesive.steps.ExecutionData import ExecutionData
from adhesive.steps.WorkflowLoop import WorkflowLoop, parent_loop_id, loop_id
from adhesive.steps.call_script_task import call_script_task
from adhesive.storage.ensure_folder import get_folder
from adhesive.workspace.local.LocalLinuxWorkspace import LocalLinuxWorkspace

T = TypeVar('T')

import concurrent.futures

from adhesive.graph.EndEvent import EndEvent
from adhesive.graph.StartEvent import StartEvent
from adhesive.graph.SubProcess import SubProcess
from adhesive.graph.BaseTask import BaseTask
from adhesive.graph.Workflow import Workflow
from adhesive.steps.AdhesiveTask import AdhesiveTask
from adhesive import config

from .ActiveEvent import ActiveEvent
from .AdhesiveProcess import AdhesiveProcess

LOG = logging.getLogger(__name__)

DONE_STATES = {
    ActiveEventState.DONE_CHECK,
    ActiveEventState.DONE_END_TASK,
    ActiveEventState.DONE,
}

ACTIVE_STATES = {
    ActiveEventState.NEW,
    ActiveEventState.PROCESSING,
    ActiveEventState.WAITING,
    ActiveEventState.RUNNING,
    ActiveEventState.ERROR,
    ActiveEventState.ROUTING,
}

# When waiting for predecessors it only makes sense to collapse events
# into ActiveEvents only when the event is not already running.
PRE_RUN_STATES = {
    ActiveEventState.NEW,
    ActiveEventState.PROCESSING,
    ActiveEventState.WAITING,
}


def is_predecessor(event, e) -> bool:
    if e.state.state not in ACTIVE_STATES:
        return False

    if e.task == event.task:
        return False

    if event.context.loop:
        # if we are in a loop and the other predecessor is inside
        # a loop of its own, we need to check if it's in the same
        # loop as ours
        if e.task.loop:
            return parent_loop_id(e) == loop_id(event)

        # we check if we are in the same loop. each iteration
        # will have its own loop id.
        if loop_id(e) != loop_id(event):
            return False

    return True


def deep_copy_event(e: T) -> T:
    return copy.deepcopy(e)


def noop_copy_event(e: T) -> T:
    return e


copy_event = noop_copy_event \
        if config.current.parallel_processing == "process" else \
        deep_copy_event


class ProcessExecutor:
    """
    An executor of AdhesiveProcesses.
    """
    pool_size = int(config.current.pool_size) if config.current.pool_size else None
    pool = concurrent.futures.ProcessPoolExecutor(max_workers=pool_size) \
            if config.current.parallel_processing == "process" \
            else concurrent.futures.ThreadPoolExecutor(max_workers=pool_size)

    def __init__(self,
                 process: AdhesiveProcess,
                 ut_provider: Optional['UserTaskProvider'] = None,
                 wait_tasks: bool = True) -> None:
        self.process = process
        self.tasks_impl: Dict[str, AdhesiveTask] = dict()

        # A dictionary of events that are currently active. This is just to find out
        # the parent of an event, since from it we can also derive the current parent
        # workflow.
        self.events: Dict[str, ActiveEvent] = dict()
        self.futures: Dict[Any, str] = dict()
        self.ut_provider = ut_provider

        self.config = ProcessExecutorConfig(wait_tasks=wait_tasks)
        self.execution_id = str(uuid.uuid4())

    async def execute(self,
                      initial_data=None) -> ExecutionData:
        """
        Execute the current events. This will ensure new events are
        generating for forked events.
        """
        workflow = self.process.workflow
        self.tasks_impl: Dict[str, AdhesiveTask] = dict()

        self._validate_tasks(workflow)

        # FIXME: it's getting pretty crowded
        token_id = str(uuid.uuid4())
        workflow_context = ExecutionToken(
            task=workflow,
            execution_id=self.execution_id,
            token_id=token_id,
            data=initial_data,
            workspace=LocalLinuxWorkspace(
                execution_id=self.execution_id,
                token_id=token_id,
                pwd=None)
        )

        fake_event = ActiveEvent(
            execution_id=self.execution_id,
            parent_id=None,
            context=workflow_context
        )
        fake_event.token_id = None  # FIXME: why

        root_event = self.clone_event(fake_event, workflow)

        def raise_exception(_ev):
            log_path = get_folder(_ev.data['failed_event'])

            if logredirect.is_enabled:
                stdout_file = os.path.join(log_path, "stdout")
                if os.path.isfile(stdout_file):
                    with open(stdout_file) as f:
                        print(white("STDOUT:", bold=True))
                        print(white(f.read()))
                else:
                    print(white("STDOUT:", bold=True) + white(" not found"))

                stderr_file = os.path.join(log_path, "stderr")
                if os.path.isfile(stderr_file):
                    with open(stderr_file) as f:
                        print(red("STDERR:", bold=True))
                        print(red(f.read()), file=sys.stderr)
                else:
                    print(red("STDERR:", bold=True) + red(" not found"))

            print(red("Exception:", bold=True))
            print(red(_ev.data['error']), file=sys.stderr)

            sys.exit(1)

        root_event.state.after_enter(ActiveEventState.ERROR, raise_exception)

        await self.execute_workflow()

        return root_event.context.data

    async def execute_workflow(self) -> None:
        """
        Process the events in a workflow until no more events are available.
        For example an event is the start of the workflow. The events are
        then creating futures (i.e. actual stuff that's being processed)
        that in turn might generate new events.
        :return:
        """
        while self.events:
            pending_events = filter(lambda e: e.state.state == ActiveEventState.NEW,
                                    self.events.values())

            # except for new processes, and processes that are running with futures
            # the states should transition automatically.
            processed_events = list(pending_events)
            for pending_event in processed_events:
                pending_event.state.process()

            done_futures, not_done_futures = concurrent.futures.wait(
                self.futures.keys(),
                return_when=concurrent.futures.FIRST_COMPLETED,
                timeout=5)

            for future in done_futures:
                token_id = self.futures[future]
                try:
                    context = future.result()
                    self.events[token_id].state.route(context)
                except Exception as e:
                    self.events[token_id].state.error({
                        "error": traceback.format_exc(),
                        "failed_event": self.events[token_id]
                    })

    def register_event(self,
                       event: ActiveEvent) -> ActiveEvent:
        """
        Register the event into a big map for finding, so we can access it later
        for parents and what not, without serializing the event graph to
        the subprocesses.

        :param event:
        :return:
        """
        if event.token_id in self.events:
            raise Exception(f"Event {event.token_id} is already registered as "
                            f"{self.events[event.token_id]}. Got a new request to register "
                            f"it as {event}.")

        LOG.debug(f"Register {event}")

        self.events[event.token_id] = event

        return event

    def unregister_event(self,
                         event: ActiveEvent) -> None:
        if event.token_id not in self.events:
            raise Exception(f"{event} not found in events. Either the event was "
                            f"already terminated, either it was not registered.")

        LOG.debug(f"Unregister {event}")

        del self.events[event.token_id]

    def get_parent(self,
                   token_id: str) -> ActiveEvent:
        """
        Find the parent of an event by looking at the event ids.
        :param token_id:
        :return:
        """
        event = self.events[token_id]
        parent = self.events[event.parent_id]

        return parent

    def _validate_tasks(self,
                        workflow: Workflow) -> None:
        """
        Recursively traverse the graph, and print to the user if it needs to implement
        some tasks.

        :param workflow:
        :return:
        """
        unmatched_tasks: Set[BaseTask] = set()

        for task_id, task in workflow.tasks.items():
            if isinstance(task, SubProcess):
                self._validate_tasks(task)
                continue

            # gateways don't have associated tasks with them.
            if isinstance(task, StartEvent) or \
                    isinstance(task, EndEvent) or \
                    isinstance(task, Gateway) or \
                    isinstance(task, BoundaryEvent):
                continue

            if isinstance(task, ScriptTask):
                if task.language in ("python", "text/python"):
                    continue

                raise Exception(f"Unknown script task language: {task.language}. Only python and "
                                f"text/python are supported.")

            adhesive_step = self._match_task(task)

            self.tasks_impl[task_id] = adhesive_step

            if not adhesive_step:
                unmatched_tasks.add(task)

        if unmatched_tasks:
            display_unmatched_tasks(unmatched_tasks)
            sys.exit(1)

    def _match_task(self, task: BaseTask) -> Optional[AdhesiveBaseTask]:
        for step in self.process.steps:
            if step.matches(task, task.name) is not None:
                return step

        return None

    def clone_event(self,
                    old_event: ActiveEvent,
                    task: BaseTask,
                    parent_id: Optional[str] = None) -> ActiveEvent:

        if parent_id is None:
            parent_id = old_event.parent_id

        event = old_event.clone(task, parent_id)
        self.register_event(event)

        if parent_id is None:
            workflow = self.process.workflow
        else:
            workflow = self.events[parent_id].task

        def process_event(_event) -> ActiveEventState:
            # if there is no processing needed, we skip to routing
            if isinstance(event.task, StartEvent) or isinstance(event.task, EndEvent):
                return event.state.route(event.context)

            if isinstance(event.task, NonWaitingGateway):
                return event.state.route(event.context)

            # if we need to wait, we wait.
            if isinstance(event.task, WaitingGateway):
                return event.state.wait_check()

            # normally we shouldn't wait for tasks, since it's counter BPMN, so
            # we allow configuring waiting for it.
            if self.config.wait_tasks and (
                    isinstance(event.task, ScriptTask) or
                    isinstance(event.task, UserTask) or
                    isinstance(event.task, Task) or
                    isinstance(event.task, Workflow)
            ):
                return event.state.wait_check()

            return event.state.run()

        def get_other_task_waiting(source: ActiveEvent) -> \
                Tuple[Optional[ActiveEvent], int]:
            """
            Get any other task that is waiting to be executed on the
            same task that's waiting.
            :param source:
            :return:
            """
            result = None
            count = 0

            for ev in self.events.values():
                if ev == source:
                    continue

                if ev.task == source.task and \
                        ev.state.state in PRE_RUN_STATES and \
                        loop_id(ev) == loop_id(source):
                    if not result:
                        result = ev

                    count += 1

            return result, count

        def wait_task(_event) -> Optional[ActiveEventState]:
            # is another waiting task already present?
            other_waiting, tasks_waiting_count = get_other_task_waiting(event)

            potential_predecessors = list(map(
                lambda e: e.task,
                filter(lambda e: is_predecessor(event, e), self.events.values())))

            if other_waiting:
                new_data = ExecutionData.merge(other_waiting.context.data, event.context.data)
                other_waiting.context.data = new_data

                event.state.done()

            # if we have predecessors, we stay in waiting
            if workflow.are_predecessors(event.task, potential_predecessors):
                return None

            if not other_waiting:
                return event.state.run()

            if other_waiting.state.state == ActiveEventState.WAITING and \
                    tasks_waiting_count == 1:
                other_waiting.state.run()

            return None

        def run_task(_event) -> None:
            try:
                print(yellow("Run  ") + yellow(event.context.task_name, bold=True))
            except Exception as e:
                raise Exception(f"Failure on {event.context.task_name}", e)

            # If the event is not yet started as a loop, we need to do that. We might have a wrong
            # loop context, if we're running in a nested loop.
            if event.task.loop and (not event.context.loop or event.context.loop.task != event.task):
                # we start a loop by firing the loop events, and consume this event.
                events_created = WorkflowLoop.create_loop(event, self.clone_event)
                if events_created == 0:
                    event.state.route(event.context)
                else:
                    event.state.done_check(None)

                return

            if isinstance(event.task, Workflow):
                for start_task in event.task.start_tasks.values():
                    # this automatically registers our events for execution
                    self.clone_event(event, start_task, parent_id=event.token_id)
                return

            if isinstance(event.task, Task):
                future = ProcessExecutor.pool.submit(
                    self.tasks_impl[event.task.id].invoke,
                    copy_event(event))
                self.futures[future] = event.token_id
                event.future = future
                return

            if isinstance(event.task, ScriptTask):
                future = ProcessExecutor.pool.submit(
                    call_script_task,
                    copy_event(event))
                self.futures[future] = event.token_id
                event.future = future
                return

            if isinstance(event.task, UserTask):
                future = Future()
                self.futures[future] = event.token_id
                event.future = future

                self.ut_provider.register_event(self, event)

                return

            return event.state.route(event.context)

        def log_running_done(_event):
            if _event.target_state == ActiveEventState.ERROR:
                print(red("Failed ") + red(event.context.task_name, bold=True))
                return

            print(green("Done ") + green(event.context.task_name, bold=True))

        def error_parent_task(event, e):
            if event.parent_id in self.events:
                parent_event = self.get_parent(event.token_id)

                # we move the parent into error
                parent_event.state.error(e)

                # FIXME: not sure if this is enough.
                for potential_child in list(self.events.values()):
                    if potential_child.parent_id != event.parent_id:
                        continue

                    # potential_child.state.error(_event.data)
                    potential_child.state.done()

        def error_task(_event) -> None:
            # if we have a boundary error task, we use that one for processing.
            if event.task.error_task:
                new_event = self.clone_event(event, event.task.error_task)
                new_event.context.data._error = _event.data['error']

                if event.task.error_task.cancel_activity:
                    event.state.done_check(None)
                else:
                    event.state.route()

                return

            error_parent_task(event, _event.data)
            # event.state.done_check(None)  # we kill the current event

        def route_task(_event) -> None:
            try:
                event.context = _event.data
                outgoing_edges = GatewayController.compute_outgoing_edges(workflow, event)

                for outgoing_edge in outgoing_edges:
                    target_task = workflow.tasks[outgoing_edge.target_id]
                    self.clone_event(event, target_task)

                event.state.done_check(outgoing_edges)
            except Exception as e:
                error_parent_task(event, {
                    "error": traceback.format_exc(),
                    "failed_event": self.events[event.token_id]
                })

        def done_check(_event) -> None:
            outgoing_edges = _event.data

            if not outgoing_edges:
                return event.state.done_end_task()

            return event.state.done()

        def done_end_task(_event) -> None:
            # we should check all the WAITING processes if they finished.
            event_count: Dict[BaseTask, int] = dict()
            waiting_events: List[ActiveEvent] = list()

            for id, self_event in self.events.items():
                if self_event.state.state in DONE_STATES:
                    continue

                if self_event.task.workflow_id != workflow.id:
                    continue

                event_count[self_event.task] = event_count.get(self_event.task, 0) + 1

                if self_event.state.state == ActiveEventState.WAITING:
                    waiting_events.append(self_event)

            for waiting_event in waiting_events:
                if event_count[waiting_event.task] > 1:
                    continue

                if waiting_event.task.workflow_id != workflow.id:
                    continue

                potential_predecessors = list(map(
                    lambda e: e.task,
                    filter(lambda e: is_predecessor(waiting_event, e), self.events.values())))

                if not workflow.are_predecessors(waiting_event.task, potential_predecessors):
                    waiting_event.state.run()

            # check sub-process termination
            found = False
            for ev in self.events.values():
                if ev.parent_id == event.parent_id and ev != event:
                    found = True
                    break

            # we merge into the parent event if it's an end state.
            if parent_id is not None:
                self.events[parent_id].context.data = ExecutionData.merge(
                    self.events[parent_id].context.data,
                    event.context.data
                )

                if not found:
                    parent_event = self.events[parent_id]
                    parent_event.state.route(parent_event.context)

            event.state.done()

        def done_task(_event) -> None:
            # if the task is done, but the future is still registered, we need to
            # remove the future.
            if event.future in self.futures:
                del self.futures[event.future]

            self.unregister_event(event)

        event.state.after_enter(ActiveEventState.PROCESSING, process_event)
        event.state.after_enter(ActiveEventState.WAITING, wait_task)
        event.state.after_enter(ActiveEventState.RUNNING, run_task)
        event.state.after_leave(ActiveEventState.RUNNING, log_running_done)
        event.state.after_enter(ActiveEventState.ERROR, error_task)
        event.state.after_enter(ActiveEventState.ROUTING, route_task)
        event.state.after_enter(ActiveEventState.DONE_CHECK, done_check)
        event.state.after_enter(ActiveEventState.DONE_END_TASK, done_end_task)
        event.state.after_enter(ActiveEventState.DONE, done_task)

        return event