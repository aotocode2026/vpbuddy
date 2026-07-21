"""v0.23.4: 百炼 DashScope vision 配置看护 — 两路分离后不再依赖 OPENAI_*.
百炼路: DASHSCOPE_API_KEY + hardcoded DashScope URL → qwen-vl-max/plus  
MiniMax路: MINIMAX_API_KEY + MINIMAX_BASE_URL → minimax-m3 (Hermes 自定)
"""

from __future__ import annotations
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def test_hermes_vision_provider_is_custom():
    """Hermes auxiliary.vision.provider 必须为 custom."""
    import yaml

    config_path = Path("/root/.hermes/config.yaml")
    if not config_path.exists():
        config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        pytest.skip("Hermes config.yaml 不存在")
    config = yaml.safe_load(config_path.read_text())
    vision = config.get("auxiliary", {}).get("vision", {})
    assert vision.get("provider") == "custom", (
        f"Vision provider 必须为 custom，当前: {vision.get('provider', 'N/A')}"
    )


def test_dashscope_api_key_available():
    """DASHSCOPE_API_KEY env 必须存在且为 sk- 开头 — ASR + vision 共用."""
    key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("BAILIAN_API_KEY", "")
    if not key:
        pytest.skip("DASHSCOPE_API_KEY 未在环境变量中设置（非 GPU 环境）")
    assert key.startswith("sk-"), f"DASHSCOPE_API_KEY 必须以 sk- 开头，当前: {key[:20]}..."
    assert len(key) >= 32, f"DASHSCOPE_API_KEY 长度不足，当前: {len(key)}"


def test_minimax_api_key_available():
    """MINIMAX_API_KEY env 必须存在 — LLM (chat + 6doc) 使用."""
    key = os.environ.get("MINIMAX_API_KEY", "")
    if not key:
        pytest.skip("MINIMAX_API_KEY 未在环境变量中设置（非 GPU 环境）")
    assert len(key) >= 20, f"MINIMAX_API_KEY 长度不足，当前: {len(key)}"


def test_hermes_vision_model_is_qwen_vl():
    """Hermes auxiliary.vision.model 必须包含 qwen-vl（DashScope 视觉模型）."""
    import yaml

    config_path = Path("/root/.hermes/config.yaml")
    if not config_path.exists():
        config_path = Path.home() / ".hermes" / "config.yaml"
    if not config_path.exists():
        pytest.skip("Hermes config.yaml 不存在")
    config = yaml.safe_load(config_path.read_text())
    vision = config.get("auxiliary", {}).get("vision", {})
    assert "qwen-vl" in vision.get("model", ""), (
        f"Vision model 必须为 qwen-vl-* 系列（DashScope），当前: {vision.get('model', 'N/A')}"
    )
