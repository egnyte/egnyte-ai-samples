import asyncio
import urllib
import httpx
import json
from uuid import uuid4

import asyncclick as click

from a2a.client import A2AClient, A2ACardResolver
from a2a.types import (
    # data types used to structure the messages/tasks
    TextPart,
    Task,
    TaskState,
    Message,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    MessageSendConfiguration,
    SendMessageRequest,
    SendStreamingMessageRequest,
    MessageSendParams,
    GetTaskRequest,
    TaskQueryParams,
    JSONRPCErrorResponse,
)


@click.command()
@click.option('--agent', default='http://localhost:10000')

async def cli(agent):
    async with httpx.AsyncClient(timeout=30) as httpx_client:
        card_resolver = A2ACardResolver(httpx_client, agent)
        card = await card_resolver.get_agent_card()

        print('======= Agent Card ========')
        print(card.model_dump_json(exclude_none=True))

        client = A2AClient(httpx_client, agent_card=card)

        continue_loop = True
        streaming = card.capabilities.streaming

        while continue_loop:
            print('\n=========  starting a new task ======== ')
            continue_loop, contextId, taskId = await completeTask(
                client,
                streaming,
                None,
                None,
            )

async def completeTask(
    client: A2AClient,
    streaming,
    taskId,
    contextId,
):
    prompt = click.prompt(
        '\nWhat do you want to send to the agent? (:q or quit to exit)'
    )
    if prompt == ':q' or prompt == 'quit':
        return False, None, None

    message = Message(
        role='user',
        parts=[TextPart(text=prompt)],
        messageId=str(uuid4()),
        taskId=taskId,
        contextId=contextId,
    )
 
    payload = MessageSendParams(
        id=str(uuid4()),
        message=message,
        configuration=MessageSendConfiguration(
            acceptedOutputModes=['text'],
        ),
    )

    taskResult = None
    message = None
    if streaming:
        response_stream = client.send_message_streaming(
            SendStreamingMessageRequest(
                id=str(uuid4()),
                params=payload,
            )
        )
        async for result in response_stream:
            if isinstance(result.root, JSONRPCErrorResponse):
                print("Error: ", result.root.error)
                return False, contextId, taskId
            event = result.root.result # Extract the event inside the result 
            # event returns object that belongs to class (Task / TaskStatusUpdate / TaskArtifactUpdate / Message)
            contextId = event.contextId # identifies the whole conversation/session.

            if (isinstance(event, Task)): # check if variable event is an instance of class Task
                # taskId identifies the current task.
                taskId = event.id

            elif isinstance(event, TaskStatusUpdateEvent):
                # status is "working" or "completed"
                taskId = event.taskId
                status_msg = getattr(event.status, "message", None)
                if status_msg:
                    parts = getattr(status_msg, "parts", [])
                    for part in parts:
                        text = getattr(part.root, "text", None)  # Access via root
                        if text:
                            print(text)
                        else:
                            print("No text in status part root:", repr(part.root))

            elif isinstance(event, TaskArtifactUpdateEvent):
                # result is updated
                taskId = event.taskId
                artifact = getattr(event, "artifact", None)
                if artifact:
                    for part in getattr(artifact, "parts", []):
                        text = getattr(part.root, "text", None)  # Access via root
                        if text:
                            print(text)
                        else:
                            print("No text in artifact part root:", repr(part.root))

            elif isinstance(event, Message):
                for part in getattr(event, "parts", []):
                    text = getattr(part.root, "text", None)  # Access via root
                    if text:
                        print(text)
                    else:
                        print("No text in message part root:", repr(part.root))
            
        if taskId:
            # Upon completion of the stream. Retrieve the full task if one was made.
            taskResult = await client.get_task(
                GetTaskRequest(
                    id=str(uuid4()),
                    params=TaskQueryParams(id=taskId),
                )
            )
            taskResult = taskResult.root.result

    else:
        try:
            # For non-streaming, assume the response is a task or message.
            event = await client.send_message(
                SendMessageRequest(
                    id=str(uuid4()),
                    params=payload,
                )
            )
            event = event.root.result
        except Exception as e:
            print("Failed to complete the call", e)
        if not contextId:
            contextId = event.contextId
        if isinstance(event, Task):
            if not taskId:
                taskId = event.id
            taskResult = event
        elif isinstance(event, Message):
            message = event

    if message:
        print(f'\n{message.model_dump_json(exclude_none=True)}')
        return True, contextId, taskId
    if taskResult:
        # Don't print the contents of a file.
        '''
        task_content = taskResult.model_dump_json(
            exclude={
                "history": {
                    "__all__": {
                        "parts": {
                            "__all__" : {"file"},
                        },
                    },
                },
            },
            exclude_none=True,
        )
        print(f'\n{task_content}') 
        '''
        ## if the result is that more input is required, loop again.
        state = TaskState(taskResult.status.state)
        if state.name == TaskState.input_required.name:
            return (
                await completeTask(
                    client,
                    streaming,
                    taskId,
                    contextId,
                ),
                contextId,
                taskId,
            )
        ## task is complete
        return True, contextId, taskId
    ## Failure case, shouldn't reach
    return True, contextId, taskId


if __name__ == '__main__':
    asyncio.run(cli())
