#!/usr/bin/env bash
#SBATCH --job-name=realtime-subtitle-remote-inference
#SBATCH --partition=L40-fbb
#SBATCH --nodelist=gpu4038
#SBATCH --gres=gpu:l40:1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G

set -euo pipefail
service_root="${REMOTE_INFERENCE_SERVICE_ROOT:-/share/home/tjfbb/data/yakutan_remote_inference}"
exec "$service_root/run_gpu4038.sh"
