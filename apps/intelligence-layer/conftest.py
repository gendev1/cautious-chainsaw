"""Root conftest — set dummy API keys before any agent imports."""
import os

# Pydantic AI validates API keys at Agent() instantiation time.
# Set dummy keys so agents can be imported in tests without
# real credentials.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-dummy")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
os.environ.setdefault("TOGETHER_API_KEY", "test-dummy")
