"""Shared CLI/web configuration; expose only non-secret status to the UI."""

import os
from pathlib import Path

from dotenv import dotenv_values

from prompt import sys_prompt
from .llm import OllamaGenerator

ROOT = Path(__file__).resolve().parents[1]


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
