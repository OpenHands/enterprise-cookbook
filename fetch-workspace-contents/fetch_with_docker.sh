#!/usr/bin/env bash
# Copy the workspace out of every OpenHands sandbox container on this Docker host.
# Works on running and stopped (paused) containers. Needs no OpenHands API key.
set -euo pipefail

OUTPUT_DIR="${1:-${OUTPUT_DIR:-./workspaces}}"
WORKSPACE_PATH="${WORKSPACE_PATH:-/workspace/project}"
CONTAINER_PREFIX="${CONTAINER_PREFIX:-oh-agent-server-}"

mkdir -p "$OUTPUT_DIR"

containers=$(docker ps -a --filter "name=^${CONTAINER_PREFIX}" --format '{{.Names}}')
if [ -z "$containers" ]; then
  echo "No containers named ${CONTAINER_PREFIX}* on this Docker host."
  exit 0
fi

copied=0
for container in $containers; do
  sandbox_id="${container#"$CONTAINER_PREFIX"}"
  state=$(docker inspect --format '{{.State.Status}}' "$container")
  dest="$OUTPUT_DIR/$sandbox_id"
  mkdir -p "$dest"
  # The trailing /. copies the directory's contents, so a second run refreshes
  # the same folder instead of nesting a new copy inside it.
  if docker cp "$container:$WORKSPACE_PATH/." "$dest" 2>/dev/null; then
    echo "$sandbox_id  $state  $(du -sh "$dest" | cut -f1)"
    copied=$((copied + 1))
  else
    rmdir "$dest" 2>/dev/null || true
    echo "$sandbox_id  $state  skipped: $WORKSPACE_PATH not found"
  fi
done

echo "Copied $copied workspace(s) to $OUTPUT_DIR"
