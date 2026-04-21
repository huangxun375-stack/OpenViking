#!/bin/bash
#
# LoCoMo 完整数据集评测 (locomo10.json 全量, 所有 samples)
#
# 用法:
#   ./run_full.sh                            # 默认 OV 导入
#   ./run_full.sh --import-mode claw         # OpenClaw 会话导入
#   ./run_full.sh --import-mode both         # OV + OpenClaw 并行导入
#   ./run_full.sh --skip-import              # 跳过导入，只跑 QA/Judge/Stat
#   ./run_full.sh --force-ingest             # 强制重新导入
#   ./run_full.sh --tag my_run               # 自定义结果标签
#   ./run_full.sh --skip-judge               # 跳过 judge 和 stat（只跑到 QA）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_eval_common.sh"

INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"
DATASET_LABEL="full"
SAMPLE_ARG=""

run_eval "$@"
