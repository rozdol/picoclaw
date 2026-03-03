#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/rozdol/picoclaw.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/picoclaw}"
SKIP_SYSTEMD="${SKIP_SYSTEMD:-0}"

if [[ "$(id -u)" -eq 0 ]]; then
  RUNTIME_USER="${RUNTIME_USER:-${SUDO_USER:-root}}"
else
  RUNTIME_USER="${RUNTIME_USER:-$(id -un)}"
fi
RUNTIME_GROUP="${RUNTIME_GROUP:-$(id -gn "${RUNTIME_USER}" 2>/dev/null || echo "${RUNTIME_USER}")}"

log() {
  printf '[install] %s\n' "$*"
}

warn() {
  printf '[install][warn] %s\n' "$*" >&2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1
}

run_sudo() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

install_base_packages() {
  local missing=()

  require_cmd git || missing+=(git)
  require_cmd python3 || missing+=(python3)

  if python3 -m venv --help >/dev/null 2>&1; then
    :
  else
    missing+=(python3-venv)
  fi

  if [[ "${#missing[@]}" -eq 0 ]]; then
    return
  fi

  if ! require_cmd apt-get; then
    printf '[install][error] Missing required tools (%s) and apt-get not found. Install dependencies manually.\n' "${missing[*]}" >&2
    exit 1
  fi

  log "Installing missing packages: ${missing[*]}"
  run_sudo apt-get update
  run_sudo apt-get install -y "${missing[@]}"
}

prepare_source() {
  run_sudo mkdir -p "${INSTALL_DIR}"
  run_sudo chown -R "${RUNTIME_USER}:${RUNTIME_GROUP}" "${INSTALL_DIR}"

  if [[ -d "${INSTALL_DIR}/.git" ]]; then
    log "Existing git checkout detected. Updating branch ${REPO_BRANCH}."

    if ! git -C "${INSTALL_DIR}" diff --quiet || ! git -C "${INSTALL_DIR}" diff --cached --quiet; then
      warn "Local git changes detected in ${INSTALL_DIR}; skipping git pull to avoid overwriting local work."
      return
    fi

    git -C "${INSTALL_DIR}" fetch --depth 1 origin "${REPO_BRANCH}"
    git -C "${INSTALL_DIR}" checkout "${REPO_BRANCH}"
    git -C "${INSTALL_DIR}" pull --ff-only origin "${REPO_BRANCH}"
    return
  fi

  if [[ -n "$(ls -A "${INSTALL_DIR}" 2>/dev/null)" ]]; then
    printf '[install][error] %s exists and is not a git checkout. Aborting to avoid clobbering files.\n' "${INSTALL_DIR}" >&2
    exit 1
  fi

  log "Cloning ${REPO_URL} (${REPO_BRANCH}) into ${INSTALL_DIR}."
  git clone --depth 1 --branch "${REPO_BRANCH}" "${REPO_URL}" "${INSTALL_DIR}"
}

setup_python_env() {
  log "Creating virtualenv and installing dependencies."
  python3 -m venv "${INSTALL_DIR}/venv"
  "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip
  "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"
}

setup_env_file() {
  if [[ -f "${INSTALL_DIR}/.env" ]]; then
    log ".env already exists, leaving it unchanged."
    return
  fi

  cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
  log "Created ${INSTALL_DIR}/.env from .env.example."
}

init_database() {
  log "Initializing SQLite database (WAL mode)."
  (
    cd "${INSTALL_DIR}"
    "${INSTALL_DIR}/venv/bin/python" -m scripts.init_db
  )
}

install_systemd_units() {
  if [[ "${SKIP_SYSTEMD}" == "1" ]]; then
    log "SKIP_SYSTEMD=1, skipping systemd unit installation."
    return
  fi

  if ! require_cmd systemctl; then
    warn "systemctl not found. Skipping systemd unit installation."
    return
  fi

  local bot_src="${INSTALL_DIR}/systemd/picoclaw.service"
  local worker_src="${INSTALL_DIR}/systemd/picoclaw-worker.service"

  if [[ ! -f "${bot_src}" || ! -f "${worker_src}" ]]; then
    printf '[install][error] Missing service templates under %s/systemd\n' "${INSTALL_DIR}" >&2
    exit 1
  fi

  local tmp_bot tmp_worker
  tmp_bot="$(mktemp)"
  tmp_worker="$(mktemp)"

  sed \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${INSTALL_DIR}|" \
    -e "s|^EnvironmentFile=.*|EnvironmentFile=${INSTALL_DIR}/.env|" \
    -e "s|^ExecStart=.*|ExecStart=${INSTALL_DIR}/venv/bin/python -m app.main|" \
    -e "s|^User=.*|User=${RUNTIME_USER}|" \
    -e "s|^Group=.*|Group=${RUNTIME_GROUP}|" \
    "${bot_src}" >"${tmp_bot}"

  sed \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${INSTALL_DIR}|" \
    -e "s|^EnvironmentFile=.*|EnvironmentFile=${INSTALL_DIR}/.env|" \
    -e "s|^ExecStart=.*|ExecStart=${INSTALL_DIR}/venv/bin/python -m app.worker|" \
    -e "s|^User=.*|User=${RUNTIME_USER}|" \
    -e "s|^Group=.*|Group=${RUNTIME_GROUP}|" \
    "${worker_src}" >"${tmp_worker}"

  log "Installing systemd units."
  run_sudo install -m 0644 "${tmp_bot}" /etc/systemd/system/picoclaw.service
  run_sudo install -m 0644 "${tmp_worker}" /etc/systemd/system/picoclaw-worker.service
  rm -f "${tmp_bot}" "${tmp_worker}"

  run_sudo systemctl daemon-reload
  run_sudo systemctl enable picoclaw.service picoclaw-worker.service
  run_sudo systemctl restart picoclaw.service picoclaw-worker.service
}

print_next_steps() {
  cat <<MSG

Installation complete.

Next steps:
1. Edit ${INSTALL_DIR}/.env and set TELEGRAM_BOT_TOKEN, ALLOWED_USER_IDS, LLM provider and API key.
2. Restart services after editing env:
   sudo systemctl restart picoclaw.service picoclaw-worker.service
3. Follow logs:
   sudo journalctl -u picoclaw.service -f
   sudo journalctl -u picoclaw-worker.service -f
MSG
}

main() {
  log "Starting PicoClaw installation."
  install_base_packages
  prepare_source
  setup_python_env
  setup_env_file
  init_database
  install_systemd_units
  print_next_steps
}

main "$@"
