#!/bin/bash
#
# 智谱AI 云端 ASR 转录脚本
# 支持 m4a、mp3、wav 等 ffmpeg 支持的格式
#
# 用法:
#   ./zhipu_asr.sh input.m4a
#   ./zhipu_asr.sh input.mp3 -o result.txt -f json
#   ./zhipu_asr.sh input.wav --api-key YOUR_API_KEY
#
# 环境变量:
#   ZHIPU_API_KEY: 智谱AI API Key (必需)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${SCRIPT_DIR}/venv/bin/python"
TOOLS_DIR="${SCRIPT_DIR}/tools"
ASR_SCRIPT="${TOOLS_DIR}/zhipu_asr.py"
ENV_FILE="${SCRIPT_DIR}/.env"

# 检查 Python 脚本
if [[ ! -f "${ASR_SCRIPT}" ]]; then
    echo "错误: 未找到 ${ASR_SCRIPT}"
    exit 1
fi

# 检查虚拟环境
if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "错误: 未找到虚拟环境，请先运行: python -m venv venv && ./venv/bin/pip install -r requirements.txt"
    exit 1
fi

# 检查 ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "警告: 未安装 ffmpeg，长音频将无法处理"
fi

# 加载 .env 文件 (如果存在)
if [[ -f "${ENV_FILE}" ]]; then
    # 导出 .env 中的变量
    set -a
    source "${ENV_FILE}"
    set +a
fi

# 执行转录
exec "${VENV_PYTHON}" "${ASR_SCRIPT}" "$@"
