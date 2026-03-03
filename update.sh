#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/picoclaw}"
REPO_BRANCH="${REPO_BRANCH:-}"
SKIP_RESTART="${SKIP_RESTART:-0}"

log() {
  printf '[update] %s\n' "$*"
}

warn() {
  printf '[update][warn] %s\n' "$*" >&2
}

die() {
  printf '[update][error] %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
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
  ./update.sh --check
  ./update.sh --apply

Options:
  --check            Check for available updates only.
  --apply            Apply updates (git pull, pip install, DB init, service restart).
  --help             Show this help.

Environment overrides:
  INSTALL_DIR        Install path (default: /opt/picoclaw)
  REPO_BRANCH        Branch to track (default: current checked-out branch)
  SKIP_RESTART       Set to 1 to skip systemd restart after apply
MSG
}

ACTION=""
case "${1:-}" in
  --check)
    ACTION="check"
    ;;
  --apply)
    ACTION="apply"
    ;;
  --help|-h)
    usage
    exit 0
    ;;
  "")
    die "No mode selected. Use --check or --apply."
    ;;
  *)
    die "Unknown option: $1"
    ;;
esac

require_cmd git
require_cmd python3

[[ -d "${INSTALL_DIR}" ]] || die "Install directory not found: ${INSTALL_DIR}"
[[ -d "${INSTALL_DIR}/.git" ]] || die "${INSTALL_DIR} is not a git checkout"

if [[ -n "${REPO_BRANCH}" ]]; then
  BRANCH="${REPO_BRANCH}"
else
  BRANCH="$(git -C "${INSTALL_DIR}" rev-parse --abbrev-ref HEAD)"
fi

[[ -n "${BRANCH}" && "${BRANCH}" != "HEAD" ]] || die "Cannot detect branch (detached HEAD). Set REPO_BRANCH explicitly."

if ! git -C "${INSTALL_DIR}" diff --quiet || ! git -C "${INSTALL_DIR}" diff --cached --quiet; then
  if [[ "${ACTION}" == "apply" ]]; then
    die "Local git changes detected in ${INSTALL_DIR}. Commit/stash/discard changes before --apply."
  fi
  warn "Local git changes detected in ${INSTALL_DIR}; update status may be unreliable."
fi

log "Fetching latest changes for branch ${BRANCH}."
git -C "${INSTALL_DIR}" fetch --depth 1 origin "${BRANCH}"

LOCAL_SHA="$(git -C "${INSTALL_DIR}" rev-parse "${BRANCH}")"
REMOTE_SHA="$(git -C "${INSTALL_DIR}" rev-parse "origin/${BRANCH}")"
LOCAL_IS_ANCESTOR=0
REMOTE_IS_ANCESTOR=0

if git -C "${INSTALL_DIR}" merge-base --is-ancestor "${LOCAL_SHA}" "${REMOTE_SHA}"; then
  LOCAL_IS_ANCESTOR=1
fi
if git -C "${INSTALL_DIR}" merge-base --is-ancestor "${REMOTE_SHA}" "${LOCAL_SHA}"; then
  REMOTE_IS_ANCESTOR=1
fi

if [[ "${LOCAL_SHA}" == "${REMOTE_SHA}" ]]; then
  log "Already up to date (${BRANCH} @ ${LOCAL_SHA:0:7})."
  exit 0
fi

if [[ "${ACTION}" == "check" ]]; then
  if [[ "${LOCAL_IS_ANCESTOR}" == "1" && "${REMOTE_IS_ANCESTOR}" == "0" ]]; then
    log "Update available: ${LOCAL_SHA:0:7} -> ${REMOTE_SHA:0:7}"
    git -C "${INSTALL_DIR}" log --oneline --no-decorate "${LOCAL_SHA}..${REMOTE_SHA}" | sed 's/^/[update]   /'
    exit 0
  fi

  if [[ "${LOCAL_IS_ANCESTOR}" == "0" && "${REMOTE_IS_ANCESTOR}" == "1" ]]; then
    warn "Local branch is ahead of origin/${BRANCH}. No remote updates to apply."
    git -C "${INSTALL_DIR}" log --oneline --no-decorate "${REMOTE_SHA}..${LOCAL_SHA}" | sed 's/^/[update]   local: /'
    exit 0
  fi

  die "Local branch and origin/${BRANCH} diverged. Manual reconciliation required."
fi

if [[ "${LOCAL_IS_ANCESTOR}" == "0" && "${REMOTE_IS_ANCESTOR}" == "0" ]]; then
  die "Local branch and origin/${BRANCH} diverged. Manual reconciliation required."
fi

if [[ "${LOCAL_IS_ANCESTOR}" == "1" && "${REMOTE_IS_ANCESTOR}" == "0" ]]; then
  log "Applying update ${LOCAL_SHA:0:7} -> ${REMOTE_SHA:0:7}."
  git -C "${INSTALL_DIR}" checkout "${BRANCH}"
  git -C "${INSTALL_DIR}" pull --ff-only origin "${BRANCH}"
else
  warn "Local branch is ahead of origin/${BRANCH}. Skipping git pull and applying local checkout as-is."
fi

if [[ ! -x "${INSTALL_DIR}/venv/bin/python" ]]; then
  log "Virtualenv missing. Creating ${INSTALL_DIR}/venv."
  python3 -m venv "${INSTALL_DIR}/venv"
fi

log "Installing Python dependencies."
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

log "Applying DB migrations/init (idempotent)."
(
  cd "${INSTALL_DIR}"
  "${INSTALL_DIR}/venv/bin/python" -m scripts.init_db
)

if [[ "${SKIP_RESTART}" == "1" ]]; then
  log "SKIP_RESTART=1, skipping systemd restart."
  exit 0
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-unit-files picoclaw.service >/dev/null 2>&1 && systemctl list-unit-files picoclaw-worker.service >/dev/null 2>&1; then
    log "Restarting picoclaw services."
    run_sudo systemctl restart picoclaw.service picoclaw-worker.service
  else
    warn "Systemd unit files not found. Skipping restart."
  fi
else
  warn "systemctl not found. Skipping restart."
fi

log "Update complete."
