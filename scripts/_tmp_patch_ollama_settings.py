from pathlib import Path

path = Path('config/settings.py')
text = path.read_text(encoding='utf-8-sig')

if 'OLLAMA_BASE_URL' not in text:
    marker = 'MISTRAL_IMAGE_MODEL = os.getenv("MISTRAL_IMAGE_MODEL", "")\n'
    insert = '''MISTRAL_IMAGE_MODEL = os.getenv("MISTRAL_IMAGE_MODEL", "")

# Ollama local models (OpenAI-compatible API at /v1)
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:12b")
'''
    if marker not in text:
        raise SystemExit('Expected Mistral marker not found; refusing patch')
    text = text.replace(marker, insert, 1)

text = text.replace(
    '"""Return normalized provider name: \'xai\', \'openai\', or \'mistral\'."""',
    '"""Return normalized provider name: \'xai\', \'openai\', \'mistral\', or \'ollama\'."""'
)
text = text.replace(
    'if LLM_PROVIDER in {"openai", "mistral"}:\n        return LLM_PROVIDER',
    'if LLM_PROVIDER in {"openai", "mistral", "ollama"}:\n        return LLM_PROVIDER'
)
text = text.replace(
    '    if provider == "mistral":\n        return MISTRAL_MODEL\n    return XAI_MODEL',
    '    if provider == "mistral":\n        return MISTRAL_MODEL\n    if provider == "ollama":\n        return OLLAMA_MODEL\n    return XAI_MODEL',
    1
)
text = text.replace(
    '    if provider == "mistral":\n        return MISTRAL_API_KEY\n    return XAI_API_KEY',
    '    if provider == "mistral":\n        return MISTRAL_API_KEY\n    if provider == "ollama":\n        return OLLAMA_API_KEY\n    return XAI_API_KEY',
    1
)
text = text.replace(
    '    if provider == "mistral":\n        return MISTRAL_BASE_URL\n    return XAI_BASE_URL',
    '    if provider == "mistral":\n        return MISTRAL_BASE_URL\n    if provider == "ollama":\n        return OLLAMA_BASE_URL\n    return XAI_BASE_URL',
    1
)
text = text.replace(
    '    if provider == "mistral":\n        return "MISTRAL_API_KEY"\n    return "XAI_API_KEY"',
    '    if provider == "mistral":\n        return "MISTRAL_API_KEY"\n    if provider == "ollama":\n        return "OLLAMA_API_KEY"\n    return "XAI_API_KEY"',
    1
)

path.write_text(text, encoding='utf-8')
print('patched config/settings.py')
