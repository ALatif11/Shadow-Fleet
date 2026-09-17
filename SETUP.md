# SETUP: WSL2 runtime for Shadow Fleet (ADR-13)

Everything runs inside WSL2 Ubuntu on your PC. Takes about an hour, most of it downloads. Commands marked **PowerShell** run in Windows; everything else runs in the Ubuntu terminal.

## 1. Free disk first
The C: drive had 23 GB free on Sep 17. The DMA ingest needs about **100 GB free** (the window gate measures the real number), plus about 8 GB for a model. WSL's virtual disk lives on C: by default.
- Clear space on C: (Storage Sense, old downloads, games you are not playing), or
- if you have a second drive, move the distro there after step 2 (**PowerShell**): `wsl --shutdown` then `wsl --manage Ubuntu-24.04 --move D:\WSL\Ubuntu` (needs a recent WSL; `wsl --update` first).

## 2. Install WSL2 and Ubuntu (**PowerShell as admin**)
```powershell
wsl --install -d Ubuntu-24.04
wsl --update
wsl --version
```
Reboot if asked, open "Ubuntu 24.04" from the Start menu, create your Linux user.

Give WSL enough memory: create `C:\Users\adam1\.wslconfig` with
```ini
[wsl2]
memory=20GB
processors=12
swap=8GB
```
then **PowerShell**: `wsl --shutdown` and reopen Ubuntu.

## 3. GPU check
Install the latest NVIDIA Windows driver (Game Ready or Studio). Do **not** install a Linux NVIDIA driver inside WSL. In Ubuntu:
```bash
nvidia-smi        # should list the RTX 5070 and a CUDA version of 12.8 or higher
```

## 4. Base tools and Python
```bash
sudo apt update && sudo apt install -y build-essential cmake git tmux curl unzip
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv python install 3.12
```

## 5. Repo
Copy the repo out of your Windows folder onto the Linux filesystem (`/mnt/c` is slow for Parquet):
```bash
mkdir -p ~/shadowfleet
cp -r "/mnt/c/Users/adam1/Downloads/New Proj Files/shadowfleet/." ~/shadowfleet/
cd ~/shadowfleet
git init && git add -A && git commit -m "Phase 0 scaffold and reconciled plan"
make setup                 # creates .venv and .env
nano .env                  # paste GFW_TOKEN=... (from globalfishingwatch.org/our-apis)
make doctor                # everything should be ok or warn; nothing FAIL
make test
```
Push to GitHub when you want (`gh repo create ALatif11/shadowfleet --private --source . --push`). `.gitignore` already keeps `data/`, `.env` and GFW-derived files out.

## 6. llama.cpp with CUDA (Phase 0 task 9; timebox 30 minutes)
Install the CUDA toolkit for WSL (check NVIDIA's "CUDA Toolkit downloads, Linux, WSL-Ubuntu" page for the current command; this is the usual shape):
```bash
wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update && sudo apt-get -y install cuda-toolkit-12-8
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
nvcc --version
```
Build (RTX 50-series is compute capability 12.0):
```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120
cmake --build build --config Release -j 12
echo 'export PATH=$HOME/llama.cpp/build/bin:$PATH' >> ~/.bashrc && source ~/.bashrc
```
Model: download one Q4_K_M GGUF of the ADR-9 model (about 7 to 8 GB) into `~/models`. Model file names change, so search Hugging Face for the current instruct GGUF of the primary model and use its exact file name:
```bash
uv tool install "huggingface_hub[cli]"
hf download <repo> <file>.gguf --local-dir ~/models
```
Run the server in its own tmux window, then the smoke test:
```bash
llama-server -m ~/models/<file>.gguf -ngl 99 -c 8192 --jinja --host 127.0.0.1 --port 8080
# second window:
cd ~/shadowfleet && make llm-smoke
```
Stop the server afterwards (Ctrl-C). Nothing else may use the GPU while it runs.

## 7. Phase 0 run order
```bash
cd ~/shadowfleet
tmux new -s sf                 # keep long jobs alive; detach with Ctrl-b d, reattach with tmux attach -t sf
make probe-dma                 # one-day benchmark; ~5 to 20 min
make window-gate               # writes config/window.json; refuses windows under 21 months
make ingest-dma                # the long one. Ctrl-C is safe; rerun resumes.
# in a second tmux window while it runs:
make probe-opensanctions && make probe-gfw && make probe-ofac && make probe-mid
make ingest-dma-check          # progress and failed days
make report-phase0             # renders reports/phase0.md
```
Keep the PC awake while ingesting (Windows Settings, Power, Screen and sleep: "Never" when plugged in) and leave the Ubuntu window open.

## 8. Disk housekeeping
- The ingest pauses on its own when free space drops below 25 GB (`SHADOWFLEET_MIN_FREE_GB` in `.env`) and resumes at 30 GB.
- The WSL virtual disk grows but does not shrink by itself. After big deletions (**PowerShell**): `wsl --shutdown`, then `wsl --manage Ubuntu-24.04 --set-sparse true` on recent WSL, or compact the `ext4.vhdx` with `diskpart` (`select vdisk file=...`, `compact vdisk`).
