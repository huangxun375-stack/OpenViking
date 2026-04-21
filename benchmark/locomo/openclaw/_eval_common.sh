#!/bin/bash
# ─────────────────────────────────────────────────────────────
# LoCoMo 评测公共逻辑
# 由 run_small.sh / run_case0.sh / run_full.sh 通过 source 引入
#
# 调用方在 source 之前需设置:
#   SCRIPT_DIR     脚本所在目录
#   INPUT_FILE     数据集文件路径
#   DATASET_LABEL  数据集标签 (small / case0 / full)
#   SAMPLE_ARG     sample 过滤参数 (空字符串 或 "--sample 0")
# ─────────────────────────────────────────────────────────────

# ── 默认配置 ──────────────────────────────────────────────────
OPENCLAW_BASE_URL="${OPENCLAW_BASE_URL:-http://127.0.0.1:18789}"
OPENCLAW_TOKEN="${OPENCLAW_GATEWAY_TOKEN:-90f2d2dc2f7b4d50cb943d3d3345e667bb3e9bcb7ec3a1fb}"
OPENVIKING_URL="${OPENVIKING_URL:-http://127.0.0.1:1933}"
QA_PARALLEL="${QA_PARALLEL:-15}"
JUDGE_PARALLEL="${JUDGE_PARALLEL:-40}"
IMPORT_WAIT="${IMPORT_WAIT:-60}"

# ── 颜色 ─────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_step()  { echo -e "${CYAN}[$1/$TOTAL_STEPS] $2${NC}"; }
log_ok()    { echo -e "${GREEN}✓ $1${NC}"; }
log_warn()  { echo -e "${YELLOW}⚠ $1${NC}"; }
log_err()   { echo -e "${RED}✗ $1${NC}"; }

# ── 参数解析 ──────────────────────────────────────────────────
parse_args() {
    IMPORT_MODE="ov"
    SKIP_IMPORT=false
    SKIP_JUDGE=false
    FORCE_INGEST=false
    TAG=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --import-mode)
                IMPORT_MODE="$2"
                if [[ ! "$IMPORT_MODE" =~ ^(ov|claw|both)$ ]]; then
                    log_err "--import-mode 必须是 ov / claw / both"
                    exit 1
                fi
                shift 2 ;;
            --skip-import)
                SKIP_IMPORT=true
                shift ;;
            --skip-judge)
                SKIP_JUDGE=true
                shift ;;
            --force-ingest)
                FORCE_INGEST=true
                shift ;;
            --tag)
                TAG="$2"
                shift 2 ;;
            --qa-parallel)
                QA_PARALLEL="$2"
                shift 2 ;;
            --judge-parallel)
                JUDGE_PARALLEL="$2"
                shift 2 ;;
            --import-wait)
                IMPORT_WAIT="$2"
                shift 2 ;;
            -h|--help)
                print_help
                exit 0 ;;
            *)
                log_warn "未知参数: $1"
                shift ;;
        esac
    done
}

print_help() {
    cat <<'HELP'
LoCoMo 评测脚本

参数:
  --import-mode MODE    导入模式: ov / claw / both (默认: ov)
                          ov   = OpenViking 直接导入 (import_to_ov.py)
                          claw = OpenClaw 会话导入 (eval.py ingest)
                          both = OV + OpenClaw 并行导入
  --skip-import         跳过导入步骤
  --skip-judge          跳过 judge 和 stat 步骤（只跑到 QA）
  --force-ingest        强制重新导入
  --tag TAG             结果目录标签 (默认: 自动生成时间戳)
  --qa-parallel N       QA 并发数 (默认: 15)
  --judge-parallel N    Judge 并发数 (默认: 40)
  --import-wait SECS    导入后等待秒数 (默认: 60)
  -h, --help            显示帮助

环境变量:
  OPENCLAW_BASE_URL       OpenClaw Gateway 地址 (默认: http://127.0.0.1:18789)
  OPENCLAW_GATEWAY_TOKEN  OpenClaw 认证 token
  OPENVIKING_URL          OpenViking 服务地址 (默认: http://127.0.0.1:1933)
  QA_PARALLEL             QA 并发数 (可被 --qa-parallel 覆盖)
  JUDGE_PARALLEL          Judge 并发数 (可被 --judge-parallel 覆盖)
  IMPORT_WAIT             导入后等待秒数 (可被 --import-wait 覆盖)
HELP
}

# ── 前置检查 ──────────────────────────────────────────────────
preflight() {
    if [[ ! -f "$INPUT_FILE" ]]; then
        log_err "数据集文件不存在: $INPUT_FILE"
        exit 1
    fi

    if ! command -v python &>/dev/null && ! command -v python3 &>/dev/null; then
        log_err "找不到 python 或 python3"
        exit 1
    fi

    PYTHON=$(command -v python3 || command -v python)
    log_ok "Python: $PYTHON"
    log_ok "数据集: $INPUT_FILE"
    log_ok "导入模式: $IMPORT_MODE"
}

# ── 结果目录 ──────────────────────────────────────────────────
setup_result_dir() {
    local ts
    ts=$(date +%Y%m%d_%H%M%S)

    if [[ -n "$TAG" ]]; then
        RUN_TAG="${DATASET_LABEL}_${IMPORT_MODE}_${TAG}"
    else
        RUN_TAG="${DATASET_LABEL}_${IMPORT_MODE}_${ts}"
    fi

    RESULT_DIR="$SCRIPT_DIR/result/${RUN_TAG}"
    mkdir -p "$RESULT_DIR"

    OUTPUT_CSV="$RESULT_DIR/qa_results.csv"
    IMPORT_OV_LOG="$RESULT_DIR/import_ov.log"
    IMPORT_CLAW_LOG="$RESULT_DIR/import_claw.log"
    IMPORT_SUCCESS_CSV="$RESULT_DIR/import_success.csv"

    log_ok "结果目录: $RESULT_DIR"
}

# ── 导入步骤 ──────────────────────────────────────────────────
do_import() {
    local force_arg=""
    [[ "$FORCE_INGEST" == true ]] && force_arg="--force-ingest"

    case "$IMPORT_MODE" in
        ov)
            log_step "$STEP" "导入数据到 OpenViking..."
            $PYTHON "$SCRIPT_DIR/import_to_ov.py" \
                --no-user-agent-id \
                --input "$INPUT_FILE" \
                --openviking-url "$OPENVIKING_URL" \
                --success-csv "$IMPORT_SUCCESS_CSV" \
                --error-log "$RESULT_DIR/import_errors.log" \
                $force_arg $SAMPLE_ARG \
                2>&1 | tee "$IMPORT_OV_LOG"
            ;;
        claw)
            log_step "$STEP" "导入数据到 OpenClaw..."
            $PYTHON "$SCRIPT_DIR/eval.py" ingest "$INPUT_FILE" \
                --token "$OPENCLAW_TOKEN" \
                --base-url "$OPENCLAW_BASE_URL" \
                $force_arg $SAMPLE_ARG \
                2>&1 | tee "$IMPORT_CLAW_LOG"
            ;;
        both)
            log_step "$STEP" "并行导入到 OpenViking + OpenClaw..."

            $PYTHON "$SCRIPT_DIR/import_to_ov.py" \
                --no-user-agent-id \
                --input "$INPUT_FILE" \
                --openviking-url "$OPENVIKING_URL" \
                --success-csv "$IMPORT_SUCCESS_CSV" \
                --error-log "$RESULT_DIR/import_errors.log" \
                $force_arg $SAMPLE_ARG \
                >"$IMPORT_OV_LOG" 2>&1 &
            local pid_ov=$!

            $PYTHON "$SCRIPT_DIR/eval.py" ingest "$INPUT_FILE" \
                --token "$OPENCLAW_TOKEN" \
                --base-url "$OPENCLAW_BASE_URL" \
                $force_arg $SAMPLE_ARG \
                >"$IMPORT_CLAW_LOG" 2>&1 &
            local pid_claw=$!

            local fail=0
            wait $pid_ov || { log_err "OV 导入失败 (exit=$?)"; fail=1; }
            wait $pid_claw || { log_err "OpenClaw 导入失败 (exit=$?)"; fail=1; }

            if [[ $fail -ne 0 ]]; then
                log_err "导入日志: $IMPORT_OV_LOG / $IMPORT_CLAW_LOG"
                exit 1
            fi
            log_ok "两路导入均完成"
            ;;
    esac

    log_ok "导入完成，等待 ${IMPORT_WAIT}s 让后台任务收敛..."
    sleep "$IMPORT_WAIT"
}

# ── QA 评测 ───────────────────────────────────────────────────
do_qa() {
    log_step "$STEP" "运行 QA 评估 (并发=$QA_PARALLEL)..."
    $PYTHON "$SCRIPT_DIR/eval.py" qa "$INPUT_FILE" \
        --token "$OPENCLAW_TOKEN" \
        --base-url "$OPENCLAW_BASE_URL" \
        --parallel "$QA_PARALLEL" \
        --output "${OUTPUT_CSV%.csv}" \
        $SAMPLE_ARG \
        2>&1 | tee "$RESULT_DIR/qa.log"
    log_ok "QA 结果: $OUTPUT_CSV"
}

# ── Judge 打分 ────────────────────────────────────────────────
do_judge() {
    log_step "$STEP" "裁判打分 (并发=$JUDGE_PARALLEL)..."
    $PYTHON "$SCRIPT_DIR/judge.py" \
        --input "$OUTPUT_CSV" \
        --parallel "$JUDGE_PARALLEL" \
        2>&1 | tee "$RESULT_DIR/judge.log"
    log_ok "打分完成"
}

# ── 统计 ──────────────────────────────────────────────────────
do_stat() {
    log_step "$STEP" "统计结果..."
    local stat_args="--input $OUTPUT_CSV"

    if [[ -f "$IMPORT_SUCCESS_CSV" ]]; then
        stat_args="$stat_args --import-csv $IMPORT_SUCCESS_CSV"
    fi

    $PYTHON "$SCRIPT_DIR/stat_judge_result.py" $stat_args \
        2>&1 | tee "$RESULT_DIR/stat.log"
    log_ok "统计完成"
}

# ── 保存配置快照 ──────────────────────────────────────────────
save_run_meta() {
    cat > "$RESULT_DIR/run_meta.json" <<EOF
{
  "dataset": "$DATASET_LABEL",
  "input_file": "$INPUT_FILE",
  "import_mode": "$IMPORT_MODE",
  "sample_arg": "$SAMPLE_ARG",
  "force_ingest": $FORCE_INGEST,
  "skip_import": $SKIP_IMPORT,
  "skip_judge": $SKIP_JUDGE,
  "qa_parallel": $QA_PARALLEL,
  "judge_parallel": $JUDGE_PARALLEL,
  "import_wait": $IMPORT_WAIT,
  "openclaw_base_url": "$OPENCLAW_BASE_URL",
  "openviking_url": "$OPENVIKING_URL",
  "timestamp": "$(date -Iseconds)"
}
EOF
}

# ── 主流程 ────────────────────────────────────────────────────
run_eval() {
    parse_args "$@"
    preflight
    setup_result_dir
    save_run_meta

    local STEP=1

    if [[ "$SKIP_JUDGE" == true ]]; then
        TOTAL_STEPS=2
    else
        TOTAL_STEPS=4
    fi
    [[ "$SKIP_IMPORT" == true ]] && TOTAL_STEPS=$((TOTAL_STEPS - 1))

    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "  LoCoMo 评测: $DATASET_LABEL ($IMPORT_MODE)"
    echo "  数据集: $INPUT_FILE"
    echo "  结果: $RESULT_DIR"
    echo "═══════════════════════════════════════════════════════"
    echo ""

    if [[ "$SKIP_IMPORT" == false ]]; then
        do_import
        STEP=$((STEP + 1))
    else
        log_warn "跳过导入步骤"
    fi

    do_qa
    STEP=$((STEP + 1))

    if [[ "$SKIP_JUDGE" == false ]]; then
        do_judge
        STEP=$((STEP + 1))

        do_stat
    else
        log_warn "跳过 judge/stat 步骤"
    fi

    echo ""
    echo "═══════════════════════════════════════════════════════"
    log_ok "评测完成!"
    echo "  结果目录: $RESULT_DIR"
    [[ -f "$OUTPUT_CSV" ]] && echo "  QA CSV:   $OUTPUT_CSV"
    [[ -f "$RESULT_DIR/summary.txt" ]] && echo "  Summary:  $RESULT_DIR/summary.txt"
    echo "═══════════════════════════════════════════════════════"
}
