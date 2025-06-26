import logging
import os
import click

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from agent import CopilotAgent
from agent_executor import CopilotAgentExecutor
from agents.langgraph.agent import CopilotAgent
from dotenv import load_dotenv
import uvicorn

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MissingAPIKeyError(Exception):
    """Exception for missing API key."""

    pass

@click.command()
@click.option('--host', 'host', default='localhost')
@click.option('--port', 'port', default=10000)
def main(host, port):
    """Starts the Copilot Agent server."""
    try:
        if not os.getenv('GOOGLE_API_KEY'):
            raise MissingAPIKeyError(
                'GOOGLE_API_KEY environment variable not set.'
            )

        capabilities = AgentCapabilities(streaming=True)
        skill = AgentSkill(
            id='copilot_agent',
            name='Egnyte Copilot Agent Tool',
            description='Search through Egnyte documents and provide relevant answers.',
            tags=['Egnyte Copilot', 'Document Q&A'],
            examples=['What are the requirements for expense tracker app?'],
        )
        agent_card = AgentCard(
            name='Egnyte Copilot Agent',
            description='Search through Egnyte documents and provide relevant answers.',
            url=f'http://{host}:{port}/',
            version='1.0.0',
            defaultInputModes=CopilotAgent.SUPPORTED_CONTENT_TYPES,
            defaultOutputModes=CopilotAgent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        request_handler = DefaultRequestHandler(
            agent_executor=CopilotAgentExecutor(),
            task_store=InMemoryTaskStore()  #store the current task,
        )
        server = A2AStarletteApplication(
            agent_card=agent_card, http_handler=request_handler
        )

        uvicorn.run(server.build(), host=host, port=port)
    except MissingAPIKeyError as e:
        logger.error(f'Error: {e}')
        exit(1)
    except Exception as e:
        logger.error(f'An error occurred during server startup: {e}')
        exit(1)


if __name__ == '__main__':
    main()
