#!/usr/bin/env bash
set -euo pipefail

if [[ "$(hostname)" != "gpu4038" ]]; then
  echo "[$(date -Is)] Hostname $(hostname) is not gpu4038; refusing to start." >&2
  exit 0
fi

# Default to the verified Yakutan deployment so both desktop applications use
# one service.  A future standalone copy may override every root explicitly.
service_root="${REMOTE_INFERENCE_SERVICE_ROOT:-${YAKUTAN_REMOTE_SERVICE_ROOT:-/share/home/tjfbb/data/yakutan_remote_inference}}"
runtime_root="${REMOTE_INFERENCE_RUNTIME_ROOT:-${YAKUTAN_RUNTIME_ROOT:-$service_root/runtime}}"
models_root="${REMOTE_INFERENCE_MODELS_ROOT:-${YAKUTAN_MODELS_ROOT:-$service_root/models}}"
python_bin="${REMOTE_INFERENCE_PYTHON:-${YAKUTAN_PYTHON:-$service_root/.venv/bin/python}}"
llama_bin_dir="${REMOTE_INFERENCE_LLAMA_BIN_DIR:-${YAKUTAN_LLAMA_BIN_DIR:-/share/home/tjfbb/data/llama.cpp-src/build-cuda/bin}}"
host="${REMOTE_INFERENCE_HOST:-127.0.0.1}"
port="${REMOTE_INFERENCE_PORT:-18775}"
llama_port="${REMOTE_INFERENCE_HYMT2_LLAMA_PORT:-18776}"
hymt2_parallel="${REMOTE_INFERENCE_HYMT2_PARALLEL:-16}"
hymt2_ctx_size="${REMOTE_INFERENCE_HYMT2_CTX_SIZE:-8192}"
qwen_workers="${REMOTE_INFERENCE_QWEN_WORKERS:-16}"
qwen_batch_wait_ms="${REMOTE_INFERENCE_QWEN_BATCH_WAIT_MS:-80}"
gpu="${REMOTE_INFERENCE_GPU:-0}"
hymt2_model="${REMOTE_INFERENCE_HYMT2_MODEL:-$models_root/hymt2/Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf}"

for path in "$service_root/server.py" "$runtime_root/config.py" "$models_root/qwen3-asr" \
  "$models_root/sensevoice-onnx" "$hymt2_model" "$python_bin" "$llama_bin_dir/llama-server"; do
  if [[ ! -e "$path" ]]; then
    echo "required path missing: $path" >&2
    exit 2
  fi
done
if [[ ! "$gpu" =~ ^[0-7]$ ]]; then
  echo "REMOTE_INFERENCE_GPU must be one physical GPU index (0-7); got $gpu" >&2
  exit 2
fi

export CUDA_VISIBLE_DEVICES="$gpu"
export REMOTE_INFERENCE_RUNTIME_ROOT="$runtime_root"
export REMOTE_INFERENCE_MODELS_ROOT="$models_root"
# The existing Yakutan runtime also consumes these compatibility names.
export YAKUTAN_RUNTIME_ROOT="$runtime_root"
export YAKUTAN_MODELS_ROOT="$models_root"
export YAKUTAN_ONNX_THREADS="${REMOTE_INFERENCE_ONNX_THREADS:-${SLURM_CPUS_PER_TASK:-16}}"
export YAKUTAN_ONNX_PROVIDER="${REMOTE_INFERENCE_ONNX_PROVIDER:-cuda}"
cudnn_lib_dir="$("$python_bin" -c 'import pathlib, nvidia.cudnn; print(pathlib.Path(nvidia.cudnn.__file__).parent / "lib")' 2>/dev/null || true)"
export LD_LIBRARY_PATH="$llama_bin_dir${cudnn_lib_dir:+:$cudnn_lib_dir}:/share/home/tjfbb/.conda/envs/fbbvllm/lib:${LD_LIBRARY_PATH:-}"

"$llama_bin_dir/llama-server" \
  -m "$hymt2_model" -c "$hymt2_ctx_size" --host 127.0.0.1 --port "$llama_port" \
  -ngl 99 --parallel "$hymt2_parallel" --cont-batching --no-webui >"$service_root/llama-server.log" 2>&1 &
llama_pid=$!
server_pid=""
cleanup() {
  if [[ -n "$server_pid" ]]; then kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; fi
  kill "$llama_pid" 2>/dev/null || true
  wait "$llama_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 120); do
  if env -u LD_LIBRARY_PATH /usr/bin/curl -fsS "http://127.0.0.1:$llama_port/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "$llama_pid" 2>/dev/null; then
    echo "llama-server exited during startup" >&2; tail -n 100 "$service_root/llama-server.log" >&2 || true; exit 1
  fi
  sleep 1
done
env -u LD_LIBRARY_PATH /usr/bin/curl -fsS "http://127.0.0.1:$llama_port/health" >/dev/null
"$python_bin" "$service_root/server.py" --host "$host" --port "$port" \
  --runtime-root "$runtime_root" --models-root "$models_root" \
  --llama-url "http://127.0.0.1:$llama_port/completion" \
  --qwen-workers "$qwen_workers" --qwen-batch-wait-ms "$qwen_batch_wait_ms" &
server_pid=$!
wait "$server_pid"
