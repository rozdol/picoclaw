#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/picoclaw}"
REMOVE_INSTALL_DIR="${REMOVE_INSTALL_DIR:-1}"
REMOVE_EXTERNAL_DB="${REMOVE_EXTERNAL_DB:-0}"

log() {
  printf '[uninstall] %s\n' "$*"
}

warn() {
  printf '[uninstall][warn] %s\n' "$*" >&2
}

die() {
  printf '[uninstall][error] %s\n' "$*" >&2
  exit 1
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

usage() {
  cat <<MSG
Usage:
  ./uninstall.sh [--dry-run]

Options:
  --dry-run          Print planned actions without making changes.
  --help             Show this help.

Environment overrides:
  INSTALL_DIR        Install path to remove (default: /opt/picoclaw)
  REMOVE_INSTALL_DIR Set to 0 to keep the install directory
  REMOVE_EXTERNAL_DB Set to 1 to also remove DB_PATH outside INSTALL_DIR
MSG
}

DRY_RUN=0
case "${1:-}" in
  "")
    ;;
  --dry-run)
    DRY_RUN=1
    ;;
  --help|-h)
    usage
    exit 0
    ;;
  *)
    die "Unknown option: $1"
    ;;
esac

run_cmd() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '[uninstall][dry-run] '
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi

  "$@"
}

run_sudo_cmd() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '[uninstall][dry-run] '
    if [[ "$(id -u)" -ne 0 ]]; then
      printf '%q ' sudo
    fi
    printf '%q ' "$@"
    printf '\n'
    return 0
  fi

  run_sudo "$@"
}

resolve_path() {
  local raw_path="$1"
  if [[ "${raw_path}" = /* ]]; then
    printf '%s\n' "${raw_path}"
  else
    printf '%s\n' "${INSTALL_DIR}/${raw_path#./}"
  fi
}

load_db_path() {
  local env_file="${INSTALL_DIR}/.env"
  local raw_value=""

  if [[ -f "${env_file}" ]]; then
    raw_value="$(sed -n 's/^[[:space:]]*DB_PATH=\(.*\)$/\1/p' "${env_file}" | tail -n 1)"
  fi

  if [[ -z "${raw_value}" ]]; then
    raw_value="./picoclaw.db"
  fi

  raw_value="${raw_value%\"}"
  raw_value="${raw_value#\"}"
  raw_value="${raw_value%\'}"
  raw_value="${raw_value#\'}"
  DB_PATH_RESOLVED="$(resolve_path "${raw_value}")"
}

remove_systemd_units() {
  if ! require_cmd systemctl; then
    warn "systemctl not found. Skipping service cleanup."
    return
  fi

  log "Stopping and disabling systemd services."
  run_sudo_cmd systemctl stop picoclaw.service picoclaw-worker.service || true
  run_sudo_cmd systemctl disable picoclaw.service picoclaw-worker.service || true

  if [[ -f /etc/systemd/system/picoclaw.service ]]; then
    run_sudo_cmd rm -f /etc/systemd/system/picoclaw.service
  fi
  if [[ -f /etc/systemd/system/picoclaw-worker.service ]]; then
    run_sudo_cmd rm -f /etc/systemd/system/picoclaw-worker.service
  fi

  run_sudo_cmd systemctl daemon-reload
  run_sudo_cmd systemctl reset-failed || true
}

remove_database_files() {
  load_db_path

  if [[ "${DB_PATH_RESOLVED}" == "${INSTALL_DIR}"/* || "${DB_PATH_RESOLVED}" == "${INSTALL_DIR}" ]]; then
    log "Removing SQLite database files under ${DB_PATH_RESOLVED}."
    run_cmd rm -f "${DB_PATH_RESOLVED}" "${DB_PATH_RESOLVED}-wal" "${DB_PATH_RESOLVED}-shm"
    return
  fi

  if [[ "${REMOVE_EXTERNAL_DB}" == "1" ]]; then
    log "Removing external SQLite database files at ${DB_PATH_RESOLVED}."
    run_cmd rm -f "${DB_PATH_RESOLVED}" "${DB_PATH_RESOLVED}-wal" "${DB_PATH_RESOLVED}-shm"
    return
  fi

  warn "DB_PATH resolves outside INSTALL_DIR (${DB_PATH_RESOLVED}); leaving it in place. Set REMOVE_EXTERNAL_DB=1 to remove it."
}

remove_install_dir() {
  if [[ "${REMOVE_INSTALL_DIR}" != "1" ]]; then
    log "REMOVE_INSTALL_DIR=0, keeping ${INSTALL_DIR}."
    return
  fi

  [[ -n "${INSTALL_DIR}" ]] || die "INSTALL_DIR must not be empty"
  [[ "${INSTALL_DIR}" != "/" ]] || die "Refusing to remove /"

  if [[ -e "${INSTALL_DIR}" ]]; then
    log "Removing install directory ${INSTALL_DIR}."
    run_sudo_cmd rm -rf "${INSTALL_DIR}"
  else
    log "Install directory not present: ${INSTALL_DIR}"
  fi
}

main() {
  log "Starting PicoClaw uninstall."
  remove_systemd_units
  remove_database_files
  remove_install_dir
  log "Uninstall complete."
}

main "$@"
