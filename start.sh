#!/bin/bash

SESSION_NAME="gimenio"
PYTHON_SCRIPT="/home/bruno/ApiPoller_python/ApiPoller.py"

# Check if the session exists
tmux has-session -t $SESSION_NAME 2>/dev/null

if [ $? != 0 ]; then
    # Create a new tmux session and run the script
    cd /home/bruno/ApiPoller_python
    tmux new-session -d -s $SESSION_NAME "python3 $PYTHON_SCRIPT"
    echo "Started $PYTHON_SCRIPT in tmux session: $SESSION_NAME"
else
    echo "Tmux session $SESSION_NAME already exists."
fi
