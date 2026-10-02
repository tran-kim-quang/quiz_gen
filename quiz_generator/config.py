"""Shared CLI/web configuration; expose only non-secret status to the UI."""

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import dotenv_values

from prompt import sys_prompt
from .llm import OllamaGenerator

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class QuizPaths:
    documents: Path
    merged_documents: Path
    output: Path


DEFAULT_DOCUMENTS = Path('/home/meconlonton/Documents/quiz/documents')
DEFAULT_MERGED_DOCUMENTS = Path('/home/meconlonton/Documents/quiz/merged-documents')
DEFAULT_OUTPUT = Path('/home/meconlonton/Documents/quiz/documents/output')


def resolve_quiz_paths(
    env_file: Path, *, documents: Path | None = None,
    merged_documents: Path | None = None, output: Path | None = None,
) -> QuizPaths:
    config = read_config(env_file)

    def resolved(override, variable, default):
        value = override if override is not None else config.get(variable) or default
        return Path(value).expanduser().resolve()

    return QuizPaths(
        documents=resolved(documents, 'QUIZ_DOCUMENTS_DIR', DEFAULT_DOCUMENTS),
        merged_documents=resolved(
            merged_documents, 'QUIZ_MERGED_DOCUMENTS_DIR', DEFAULT_MERGED_DOCUMENTS
        ),
        output=resolved(output, 'QUIZ_OUTPUT_DIR', DEFAULT_OUTPUT),
    )


def read_config(env_file: Path = ROOT / '.env') -> dict:
    return {**dotenv_values(env_file), **os.environ}


def create_generator(env_file: Path = ROOT / '.env') -> OllamaGenerator:
    config = read_config(env_file)
    return OllamaGenerator(
        api_key=config.get('OLLAMA_API_KEY') or '', model=config.get('LLM_MODEL_NAME') or '',
        system_prompt=sys_prompt, base_url=config.get('OLLAMA_BASE_URL') or 'https://ollama.com',
        timeout=float(config.get('OLLAMA_TIMEOUT_SECONDS', '300')),
        max_attempts=int(config.get('LLM_MAX_ATTEMPTS', '3')),
        retry_delay=float(config.get('LLM_RETRY_DELAY_SECONDS', '2')),
        temperature=float(config.get('LLM_TEMPERATURE', '0.3')),
        max_tokens=int(config.get('LLM_MAX_TOKENS', '8192')),
    )


def public_config(env_file: Path = ROOT / '.env') -> dict:
    config = read_config(env_file)
    return {'model': config.get('LLM_MODEL_NAME') or '',
            'configured': bool(config.get('OLLAMA_API_KEY') and config.get('LLM_MODEL_NAME'))}
