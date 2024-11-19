#!/bin/bash

# Usage:./get_pr_commands_from_log.sh <log_file> <pr_number>

LOG_FILE=$1
PR_NUMBER=$2
cat $LOG_FILE | grep "$PR_NUMBER" | grep "Running" | awk -F": Running '" '{print $2}' | awk -F"' command" '{print $1}'
