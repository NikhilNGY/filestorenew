#!/bin/bash
set -e

# Launch the bot directly (dependencies are already pre-installed in the Docker image)
exec python3 -O main.py
