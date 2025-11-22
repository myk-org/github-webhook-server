"""Google Gemini API client for AI-powered workflow automation."""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from google.generativeai.types import GenerationConfig


@dataclass
class GeminiConfig:
    """Configuration for Gemini API client."""

    api_key: str
    model: str = "gemini-2.0-flash-latest"
    temperature: float = 0.3
    max_tokens: int = 2000
    timeout: int = 30


@dataclass
class GeminiResponse:
    """Wrapper for Gemini API responses."""

    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str | None = None
    raw_response: Any = None

    @property
    def usage_stats(self) -> dict[str, int]:
        """Get token usage statistics."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class GeminiClient:
    """Google Gemini API client with retry logic and error handling."""

    def __init__(
        self,
        config: GeminiConfig,
        logger: logging.Logger | None = None,
    ):
        """Initialize Gemini client.

        Args:
            config: Gemini configuration
            logger: Logger instance (optional)
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._request_count = 0
        self._error_count = 0

        # Configure Gemini API
        genai.configure(api_key=self.config.api_key)

        # Initialize model
        self._model = genai.GenerativeModel(
            model_name=self.config.model,
            generation_config=GenerationConfig(
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_tokens,
            ),
        )

    async def analyze_text(
        self,
        prompt: str,
        context: str | None = None,
        retry_count: int = 3,
    ) -> GeminiResponse:
        """Analyze text using Gemini API.

        Args:
            prompt: The prompt/question to send to Gemini
            context: Additional context to include (optional)
            retry_count: Number of retry attempts on failure

        Returns:
            GeminiResponse with analysis results

        Raises:
            Exception: If all retry attempts fail
        """
        full_prompt = f"{context}\n\n{prompt}" if context else prompt

        for attempt in range(retry_count):
            try:
                self.logger.debug(f"Gemini API request (attempt {attempt + 1}/{retry_count})")

                # Wrap blocking Gemini API call in asyncio.to_thread
                response = await asyncio.to_thread(
                    self._model.generate_content,
                    full_prompt,
                )

                # Extract response data
                content = response.text if response.text else ""

                # Track usage (Gemini provides usage metadata)
                prompt_tokens = getattr(response.usage_metadata, "prompt_token_count", 0)
                completion_tokens = getattr(response.usage_metadata, "candidates_token_count", 0)
                total_tokens = getattr(response.usage_metadata, "total_token_count", 0)

                # Update statistics
                self._total_prompt_tokens += prompt_tokens
                self._total_completion_tokens += completion_tokens
                self._request_count += 1

                finish_reason = getattr(response.candidates[0], "finish_reason", None) if response.candidates else None

                self.logger.debug(
                    f"Gemini API success: {prompt_tokens} prompt tokens, "
                    f"{completion_tokens} completion tokens, "
                    f"finish_reason: {finish_reason}"
                )

                return GeminiResponse(
                    content=content,
                    model=self.config.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    finish_reason=str(finish_reason) if finish_reason else None,
                    raw_response=response,
                )

            except google_exceptions.ResourceExhausted as ex:
                self._error_count += 1
                self.logger.warning(f"Gemini API rate limit exceeded (attempt {attempt + 1}): {ex}")
                if attempt < retry_count - 1:
                    wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                    self.logger.debug(f"Retrying in {wait_time} seconds...")
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.error("Gemini API rate limit: all retry attempts exhausted")
                    raise

            except google_exceptions.GoogleAPIError as ex:
                self._error_count += 1
                self.logger.warning(f"Gemini API error (attempt {attempt + 1}): {ex}")
                if attempt < retry_count - 1:
                    wait_time = 2**attempt
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.exception("Gemini API error: all retry attempts exhausted")
                    raise

            except Exception as ex:
                self._error_count += 1
                self.logger.exception(f"Unexpected error calling Gemini API (attempt {attempt + 1}): {ex}")
                if attempt < retry_count - 1:
                    wait_time = 2**attempt
                    await asyncio.sleep(wait_time)
                else:
                    raise

        # Should never reach here, but satisfy type checker
        raise RuntimeError("All retry attempts failed")

    async def function_call(
        self,
        prompt: str,
        functions: list[dict[str, Any]],
        context: str | None = None,
        retry_count: int = 3,
    ) -> dict[str, Any]:
        """Call Gemini with function calling support.

        Args:
            prompt: The prompt/question to send to Gemini
            functions: List of function definitions in OpenAI format
            context: Additional context to include (optional)
            retry_count: Number of retry attempts on failure

        Returns:
            Dictionary with function call details (name, arguments)

        Raises:
            Exception: If all retry attempts fail
        """
        full_prompt = f"{context}\n\n{prompt}" if context else prompt

        for attempt in range(retry_count):
            try:
                self.logger.debug(f"Gemini function call (attempt {attempt + 1}/{retry_count})")

                # Convert functions to Gemini format (tools)
                tools = [genai.protos.Tool(function_declarations=functions)]

                # Wrap blocking Gemini API call
                response = await asyncio.to_thread(
                    self._model.generate_content,
                    full_prompt,
                    tools=tools,
                )

                # Extract function call from response
                if response.candidates and response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if hasattr(part, "function_call"):
                            function_call = part.function_call
                            self._request_count += 1
                            self.logger.debug(f"Gemini function call result: {function_call.name}")

                            return {
                                "name": function_call.name,
                                "arguments": dict(function_call.args),
                            }

                # No function call in response
                self._request_count += 1
                self.logger.warning("Gemini response did not contain a function call")
                return {"name": None, "arguments": {}}

            except google_exceptions.ResourceExhausted as ex:
                self._error_count += 1
                self.logger.warning(f"Gemini function call rate limit (attempt {attempt + 1}): {ex}")
                if attempt < retry_count - 1:
                    wait_time = 2**attempt
                    await asyncio.sleep(wait_time)
                else:
                    raise

            except Exception as ex:
                self._error_count += 1
                self.logger.exception(f"Gemini function call error (attempt {attempt + 1}): {ex}")
                if attempt < retry_count - 1:
                    wait_time = 2**attempt
                    await asyncio.sleep(wait_time)
                else:
                    raise

        raise RuntimeError("All retry attempts failed")

    def get_usage_stats(self) -> dict[str, int | float]:
        """Get accumulated usage statistics.

        Returns:
            Dictionary with usage metrics
        """
        total_tokens = self._total_prompt_tokens + self._total_completion_tokens

        # Calculate cost (Gemini 2.0 Flash pricing)
        # Input: $0.075 per 1M tokens
        # Output: $0.30 per 1M tokens
        input_cost = (self._total_prompt_tokens / 1_000_000) * 0.075
        output_cost = (self._total_completion_tokens / 1_000_000) * 0.30
        total_cost = input_cost + output_cost

        return {
            "request_count": self._request_count,
            "error_count": self._error_count,
            "prompt_tokens": self._total_prompt_tokens,
            "completion_tokens": self._total_completion_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(total_cost, 6),
        }

    @classmethod
    def from_env(
        cls,
        api_key_env: str = "GEMINI_API_KEY",
        model: str = "gemini-2.0-flash-latest",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        logger: logging.Logger | None = None,
    ) -> "GeminiClient":
        """Create GeminiClient from environment variable.

        Args:
            api_key_env: Environment variable name containing API key
            model: Gemini model to use
            temperature: Model temperature
            max_tokens: Maximum output tokens
            logger: Logger instance

        Returns:
            Configured GeminiClient instance

        Raises:
            ValueError: If API key environment variable is not set
        """
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise ValueError(f"Environment variable {api_key_env} is not set")

        config = GeminiConfig(
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return cls(config=config, logger=logger)
