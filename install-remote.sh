#!/usr/bin/env bash
# Download a source snapshot, then delegate to the local installer.
set -euo pipefail

install_remote() (
    set -euo pipefail
    local dependency temporary
    local ref=${CODEX_TMUX_REF:-main}
    for dependency in curl tar python3; do
        command -v "$dependency" >/dev/null || {
            printf 'Required command not found: %s\n' "$dependency" >&2
            exit 1
        }
    done
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required.")'

    temporary=$(mktemp -d)
    trap 'rm -rf -- "$temporary"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    printf 'Downloading codex-tmux (%s)...\n' "$ref"
    curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' \
        "https://codeload.github.com/minsoft1115/codex-tmux/tar.gz/$ref" \
        --output "$temporary/source.tar.gz"
    mkdir "$temporary/source"
    tar -xzf "$temporary/source.tar.gz" --strip-components=1 -C "$temporary/source"
    bash "$temporary/source/install.sh" "$@"
)

install_remote "$@"
