#!/bin/bash
#
# LoCoMo Case0 数据集评测 (locomo10.json sample 0, 19 sessions / 152 QA)
#
# 用法:
#   ./run_case0.sh                           # 默认 OV 导入
#   ./run_case0.sh --import-mode claw        # OpenClaw 会话导入
#   ./run_case0.sh --import-mode both        # OV + OpenClaw 并行导入
#   ./run_case0.sh --skip-import             # 跳过导入，只跑 QA/Judge/Stat
#   ./run_case0.sh --force-ingest            # 强制重新导入
#   ./run_case0.sh --tag my_run              # 自定义结果标签
#   ./run_case0.sh --skip-judge              # 跳过 judge 和 stat（只跑到 QA）
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_eval_common.sh"

INPUT_FILE="$SCRIPT_DIR/../data/locomo10.json"
DATASET_LABEL="case0"
SAMPLE_ARG="--sample 0"

run_eval "$@"
