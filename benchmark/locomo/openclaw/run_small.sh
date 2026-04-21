#!/bin/bash
#
# LoCoMo Small 数据集评测 (locomo10_small.json, 4 sessions / 35 QA)
#
# 用法:
#   ./run_small.sh                           # 默认 OV 导入
#   ./run_small.sh --import-mode claw        # OpenClaw 会话导入
#   ./run_small.sh --import-mode both        # OV + OpenClaw 并行导入
#   ./run_small.sh --skip-import             # 跳过导入，只跑 QA/Judge/Stat
#   ./run_small.sh --force-ingest            # 强制重新导入
#   ./run_small.sh --tag my_run              # 自定义结果标签
#   ./run_small.sh --skip-judge              # 跳过 judge 和 stat（只跑到 QA）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_eval_common.sh"

INPUT_FILE="$SCRIPT_DIR/../data/locomo10_small.json"
DATASET_LABEL="small"
SAMPLE_ARG=""

run_eval "$@"
