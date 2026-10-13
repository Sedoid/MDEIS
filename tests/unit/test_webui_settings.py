"""The Open WebUI settings that keep the chat to its one job (static: reads the compose file)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ENV = yaml.safe_load((ROOT / "deploy" / "compose" / "webui.yml").read_text(encoding="utf-8"))[
    "services"]["webui"]["environment"]


def test_environment_wins_over_saved_admin_settings():
    assert ENV["ENABLE_PERSISTENT_CONFIG"] == "false"


def test_a_user_cannot_attach_files_or_open_the_controls():
    for name in ("USER_PERMISSIONS_CHAT_FILE_UPLOAD", "USER_PERMISSIONS_CHAT_CONTROLS",
                 "USER_PERMISSIONS_CHAT_SYSTEM_PROMPT", "USER_PERMISSIONS_CHAT_PARAMS"):
        assert ENV[name] == "false", name


def test_workspace_pages_and_extra_features_are_off():
    off = [name for name in ENV if name.startswith(("USER_PERMISSIONS_WORKSPACE_",
                                                    "USER_PERMISSIONS_FEATURES_"))]
    assert len(off) >= 10
    assert all(ENV[name] == "false" for name in off)
    for name in ("ENABLE_WEB_SEARCH", "ENABLE_CODE_INTERPRETER", "ENABLE_CODE_EXECUTION",
                 "ENABLE_IMAGE_GENERATION", "ENABLE_API_KEYS"):
        assert ENV[name] == "false", name


def test_no_other_model_source_and_no_signup():
    assert ENV["ENABLE_OLLAMA_API"] == "false" and ENV["ENABLE_DIRECT_CONNECTIONS"] == "false"
    assert ENV["ENABLE_SIGNUP"] == "false"
    assert ENV["OPENAI_API_BASE_URL"] == "http://gateway:8000/v1"
