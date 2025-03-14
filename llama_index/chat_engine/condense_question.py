import logging
from typing import Any, List, Type, Optional

from llama_index.chat_engine.types import (
    BaseChatEngine,
    AgentChatResponse,
    StreamingAgentChatResponse,
)
from llama_index.chat_engine.utils import response_gen_with_chat_history
from llama_index.indices.query.base import BaseQueryEngine
from llama_index.indices.service_context import ServiceContext
from llama_index.llms.base import ChatMessage, MessageRole
from llama_index.llms.generic_utils import messages_to_history_str
from llama_index.memory import BaseMemory, ChatMemoryBuffer
from llama_index.prompts.base import Prompt
from llama_index.response.schema import StreamingResponse, RESPONSE_TYPE
from llama_index.tools import ToolOutput

logger = logging.getLogger(__name__)


DEFAULT_TEMPLATE = """\
Given a conversation (between Human and Assistant) and a follow up message from Human, \
rewrite the message to be a standalone question that captures all relevant context \
from the conversation.

<Chat History> 
{chat_history}

<Follow Up Message>
{question}

<Standalone question>
"""

DEFAULT_PROMPT = Prompt(DEFAULT_TEMPLATE)


class CondenseQuestionChatEngine(BaseChatEngine):
    """Condense Question Chat Engine.

    First generate a standalone question from conversation context and last message,
    then query the query engine for a response.
    """

    def __init__(
        self,
        query_engine: BaseQueryEngine,
        condense_question_prompt: Prompt,
        memory: BaseMemory,
        service_context: ServiceContext,
        verbose: bool = False,
    ) -> None:
        self._query_engine = query_engine
        self._condense_question_prompt = condense_question_prompt
        self._memory = memory
        self._service_context = service_context
        self._verbose = verbose

    @classmethod
    def from_defaults(
        cls,
        query_engine: BaseQueryEngine,
        condense_question_prompt: Optional[Prompt] = None,
        chat_history: Optional[List[ChatMessage]] = None,
        memory: Optional[BaseMemory] = None,
        memory_cls: Type[BaseMemory] = ChatMemoryBuffer,
        service_context: Optional[ServiceContext] = None,
        verbose: bool = False,
        system_prompt: Optional[str] = None,
        prefix_messages: Optional[List[ChatMessage]] = None,
        **kwargs: Any,
    ) -> "CondenseQuestionChatEngine":
        """Initialize a CondenseQuestionChatEngine from default parameters."""
        condense_question_prompt = condense_question_prompt or DEFAULT_PROMPT
        chat_history = chat_history or []
        memory = memory or memory_cls.from_defaults(chat_history=chat_history)
        service_context = service_context or ServiceContext.from_defaults()

        if system_prompt is not None:
            raise NotImplementedError(
                "system_prompt is not supported for CondenseQuestionChatEngine."
            )
        if prefix_messages is not None:
            raise NotImplementedError(
                "prefix_messages is not supported for CondenseQuestionChatEngine."
            )

        return cls(
            query_engine,
            condense_question_prompt,
            memory,
            service_context,
            verbose=verbose,
        )

    def _condense_question(
        self, chat_history: List[ChatMessage], last_message: str
    ) -> str:
        """
        Generate standalone question from conversation context and last message.
        """

        chat_history_str = messages_to_history_str(chat_history)
        logger.debug(chat_history_str)

        response = self._service_context.llm_predictor.predict(
            self._condense_question_prompt,
            question=last_message,
            chat_history=chat_history_str,
        )
        return response

    async def _acondense_question(
        self, chat_history: List[ChatMessage], last_message: str
    ) -> str:
        """
        Generate standalone question from conversation context and last message.
        """

        chat_history_str = messages_to_history_str(chat_history)
        logger.debug(chat_history_str)

        response = await self._service_context.llm_predictor.apredict(
            self._condense_question_prompt,
            question=last_message,
            chat_history=chat_history_str,
        )
        return response

    def _get_tool_output_from_response(
        self, query: str, response: RESPONSE_TYPE
    ) -> ToolOutput:
        return ToolOutput(
            content=str(response),
            tool_name="query_engine",
            raw_input={"query": query},
            raw_output=response,
        )

    def chat(
        self, message: str, chat_history: Optional[List[ChatMessage]] = None
    ) -> AgentChatResponse:
        chat_history = chat_history or self._memory.get()

        # Generate standalone question from conversation context and last message
        condensed_question = self._condense_question(chat_history, message)

        log_str = f"Querying with: {condensed_question}"
        logger.info(log_str)
        if self._verbose:
            print(log_str)

        # Query with standalone question
        query_response = self._query_engine.query(condensed_question)
        tool_output = self._get_tool_output_from_response(
            condensed_question, query_response
        )

        # Record response
        self._memory.put(ChatMessage(role=MessageRole.USER, content=message))
        self._memory.put(
            ChatMessage(role=MessageRole.ASSISTANT, content=str(query_response))
        )

        return AgentChatResponse(response=str(query_response), sources=[tool_output])

    def stream_chat(
        self, message: str, chat_history: Optional[List[ChatMessage]] = None
    ) -> StreamingAgentChatResponse:
        chat_history = chat_history or self._memory.get()

        # Generate standalone question from conversation context and last message
        condensed_question = self._condense_question(chat_history, message)

        log_str = f"Querying with: {condensed_question}"
        logger.info(log_str)
        if self._verbose:
            print(log_str)

        # Query with standalone question
        query_response = self._query_engine.query(condensed_question)
        tool_output = self._get_tool_output_from_response(
            condensed_question, query_response
        )

        # Record response
        if (
            isinstance(query_response, StreamingResponse)
            and query_response.response_gen is not None
        ):
            # override the generator to include writing to chat history
            response = StreamingAgentChatResponse(
                chat_stream=response_gen_with_chat_history(
                    message, self._memory, query_response.response_gen
                ),
                sources=[tool_output],
            )
        else:
            raise ValueError("Streaming is not enabled. Please use chat() instead.")
        return response

    async def achat(
        self, message: str, chat_history: Optional[List[ChatMessage]] = None
    ) -> AgentChatResponse:
        chat_history = chat_history or self._memory.get()

        # Generate standalone question from conversation context and last message
        condensed_question = await self._acondense_question(chat_history, message)

        log_str = f"Querying with: {condensed_question}"
        logger.info(log_str)
        if self._verbose:
            print(log_str)

        # Query with standalone question
        query_response = await self._query_engine.aquery(condensed_question)
        tool_output = self._get_tool_output_from_response(
            condensed_question, query_response
        )

        # Record response
        self._memory.put(ChatMessage(role=MessageRole.USER, content=message))
        self._memory.put(
            ChatMessage(role=MessageRole.ASSISTANT, content=str(query_response))
        )

        return AgentChatResponse(response=str(query_response), sources=[tool_output])

    async def astream_chat(
        self, message: str, chat_history: Optional[List[ChatMessage]] = None
    ) -> StreamingAgentChatResponse:
        chat_history = chat_history or self._memory.get()

        # Generate standalone question from conversation context and last message
        condensed_question = await self._acondense_question(chat_history, message)

        log_str = f"Querying with: {condensed_question}"
        logger.info(log_str)
        if self._verbose:
            print(log_str)

        # Query with standalone question
        query_response = await self._query_engine.aquery(condensed_question)
        tool_output = self._get_tool_output_from_response(
            condensed_question, query_response
        )

        # Record response
        if (
            isinstance(query_response, StreamingResponse)
            and query_response.response_gen is not None
        ):
            # override the generator to include writing to chat history
            # TODO: query engine does not support async generator yet
            response = StreamingAgentChatResponse(
                chat_stream=response_gen_with_chat_history(
                    message, self._memory, query_response.response_gen
                ),
                sources=[tool_output],
            )
        else:
            raise ValueError("Streaming is not enabled. Please use achat() instead.")
        return response

    def reset(self) -> None:
        # Clear chat history
        self._memory.reset()

    @property
    def chat_history(self) -> List[ChatMessage]:
        """Get chat history."""
        return self._memory.get_all()
