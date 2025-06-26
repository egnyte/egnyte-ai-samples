from collections.abc import AsyncIterable
from typing import Any, Literal, Dict, Optional, List
import json
import httpx
import os
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent


memory = MemorySaver()


def get_shared_folder_id(egnyte__domain:str , egnyte_acess_token:str) -> Optional[str]:
    """
    Fetch the Shared folder ID from Egnyte API
    Returns:
        Shared folder ID or None if request fails
    """

    url = f"https://{egnyte__domain}/pubapi/v1/fs/"
    headers = {
        'Authorization': f'Bearer {egnyte_acess_token}',
        'Content-Type': 'application/json'
    }

    try:
        response = httpx.get(url, headers=headers, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        #print("json-response-data", data)

        # Look for the "Shared" folder
        folders = data.get('folders', [])
        for folder in folders:
            if folder.get('name') == 'Shared':
                return folder.get('folder_id')

        # If Shared folder not found, return None
        print("Shared folder not found in the response")
        return None
    except Exception as e:
        print(f"Error fetching Shared folder ID: {e}")
        return None


@tool
def ask_copilot(question: str) -> Dict[str, Any]:
    """
    Call the Copilot API to get answers based on Egnyte documents
    Args:
        question: The question to ask
    Returns:
        Dictionary containing the response from Copilot API
    """
    egnyte_domain = os.getenv('EGNYTE_DOMAIN')
    egnyte_access_token = os.getenv('EGNYTE_ACCESS_TOKEN')

    if not egnyte_access_token:
        raise ValueError("EGNYTE_ACCESS_TOKEN environment variable must be set")

    url = f"https://{egnyte_domain}/pubapi/v1/ai/copilot/ask"
    headers = {
        'Authorization': f'Bearer {egnyte_access_token}',
        'Content-Type': 'application/json'
    }

    shared_id = get_shared_folder_id(egnyte_domain , egnyte_access_token)
    if not shared_id:
        return {'error': 'Unable to fetch root folder ID'}
    payload = {
        "question": question,
        "chatHistory": {
            "messages": []
        },
        "selectedItems": {
            "folders": [
                {"id": shared_id}
            ],
            "files": []
        },
        "includeCitations": True
    }

    try:
        print("Sending request to Copilot API: %s", payload)
        response = httpx.post(url, headers=headers, json=payload, timeout=30.0)
        print(response.headers)
        response.raise_for_status()
        out_put = response.json()
        print (out_put)
        return out_put

    except httpx.HTTPError as e:
        return {'error': f'API request failed: {e}'}
    except json.JSONDecodeError:
        return {'error': 'Invalid JSON response from API'}
    except Exception as e:
        return {'error': f'Unexpected error: {e}'}

class ResponseFormat(BaseModel):
    """Respond to the user in this format."""

    status: Literal['input_required', 'completed', 'error'] = 'input_required'
    message: str


class CopilotAgent:
    SYSTEM_INSTRUCTION = (
        'You are a specialized assistant for answering questions about content in Egnyte. '
        'Your purpose is to use the "ask_copilot" tool to search through documents and provide relevant answers. '
        'If you cannot find relevant information, politely inform the user that the information is not available in the accessible documents. '
        'Set response status to input_required if the user needs to provide more information. '
        'Set response status to error if there is an error while processing the request. '
        'Set response status to completed if the request is complete.'
    )

    def __init__(self):
        self.model = ChatGoogleGenerativeAI(model='gemini-2.0-flash')
        self.tools = [ask_copilot]

        self.graph = create_react_agent(
            self.model,
            tools=self.tools,
            checkpointer=memory,
            prompt=self.SYSTEM_INSTRUCTION,
            response_format=ResponseFormat,
        )

    def invoke(self, query, sessionId) -> str:
        config = {'configurable': {'thread_id': sessionId}}
        self.graph.invoke({'messages': [('user', query)]}, config)
        return self.get_agent_response(config)

    async def stream(self, query, sessionId) -> AsyncIterable[Dict[str, Any]]:
        inputs = {'messages': [('user', query)]}
        config = {'configurable': {'thread_id': sessionId}}

        for item in self.graph.stream(inputs, config, stream_mode='values'):
            message = item['messages'][-1]
            if (
                isinstance(message, AIMessage) # AI is about to call a tool
                and message.tool_calls
                and len(message.tool_calls) > 0
            ):
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': '\nLooking up the relevant documents...',
                }
            elif isinstance(message, ToolMessage): # A tool just returned its result
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': '\nProcessing the retrieved answers ..\n',
                }

        yield self.get_agent_response(config)

    def get_agent_response(self, config):
        current_state = self.graph.get_state(config)
        structured_response = current_state.values.get('structured_response')
        if structured_response and isinstance(
            structured_response, ResponseFormat
        ):
            if structured_response.status == 'input_required':
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            elif structured_response.status == 'error':
                return {
                    'is_task_complete': False,
                    'require_user_input': True,
                    'content': structured_response.message,
                }
            elif structured_response.status == 'completed':
                return {
                    'is_task_complete': True,
                    'require_user_input': False,
                    'content': structured_response.message,
                }

        return {
            'is_task_complete': False,
            'require_user_input': True,
            'content': 'We are unable to process your request at the moment. Please try again.',
        }

    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']
